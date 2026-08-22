from dataclasses import dataclass

from app.config import Settings
from app.services.audit import AuditLog, PostgresAuditLog
from app.services.changes import ChangeReader
from app.services.external import Neo4jKnowledgeGraph, QdrantHybridRetriever
from app.services.graph import KnowledgeGraph
from app.services.ingestion import RepositoryParser
from app.services.repository import GitHubRepositoryClient
from app.services.retrieval import (
    FastEmbedEmbeddingProvider,
    FastEmbedReranker,
    HashEmbeddingProvider,
    HttpReranker,
    HybridRetriever,
)
from app.services.workflow import ImpactWorkflow, KnowledgeWorkflow


@dataclass
class Container:
    settings: Settings
    retriever: HybridRetriever
    graph: KnowledgeGraph
    parser: RepositoryParser
    audit: AuditLog
    knowledge_workflow: KnowledgeWorkflow
    impact_workflow: ImpactWorkflow
    github: GitHubRepositoryClient


def build_container(settings: Settings) -> Container:
    embedder = (
        FastEmbedEmbeddingProvider(settings.embedding_model, settings.embedding_dimensions)
        if settings.embedding_backend == "fastembed"
        else HashEmbeddingProvider()
    )
    if settings.reranker_url:
        reranker = HttpReranker(settings.reranker_url)
    elif settings.reranker_backend == "fastembed":
        reranker = FastEmbedReranker(settings.reranker_model)
    else:
        reranker = None
    if settings.enable_external_services:
        retriever = QdrantHybridRetriever(settings.qdrant_url, reranker=reranker, embedder=embedder)
        graph = Neo4jKnowledgeGraph(
            settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password
        )
    else:
        retriever = HybridRetriever(reranker=reranker, embedder=embedder)
        graph = KnowledgeGraph()
    parser = RepositoryParser(settings.repository_root)
    audit = (
        PostgresAuditLog(settings.postgres_dsn) if settings.enable_external_services else AuditLog()
    )
    return Container(
        settings=settings,
        retriever=retriever,
        graph=graph,
        parser=parser,
        audit=audit,
        knowledge_workflow=KnowledgeWorkflow(retriever, graph),
        impact_workflow=ImpactWorkflow(retriever, graph, ChangeReader(settings.repository_root)),
        github=GitHubRepositoryClient(settings.github_api_url, settings.github_token),
    )
