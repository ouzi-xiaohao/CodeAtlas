from __future__ import annotations

import asyncio
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from neo4j import AsyncGraphDatabase
from qdrant_client import AsyncQdrantClient, models

from app.models import (
    GraphEdge,
    GraphNode,
    GraphPath,
    KnowledgeChunk,
    RetrievalHit,
    UserContext,
)
from app.services.graph import KnowledgeGraph
from app.services.retrieval import EmbeddingProvider, HybridRetriever, Reranker, tokenize


class QdrantHybridRetriever(HybridRetriever):
    def __init__(
        self,
        url: str,
        reranker: Reranker | None = None,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        super().__init__(reranker=reranker, embedder=embedder)
        self.client = AsyncQdrantClient(url=url)
        self.collection = f"codeatlas_knowledge_{self.embedder.dimensions}"
        self._initialized = False

    async def _ensure_collection(self) -> None:
        if self._initialized:
            return
        if not await self.client.collection_exists(self.collection):
            await self.client.create_collection(
                collection_name=self.collection,
                vectors_config={
                    "dense": models.VectorParams(
                        size=self.embedder.dimensions, distance=models.Distance.COSINE
                    )
                },
                sparse_vectors_config={
                    "sparse": models.SparseVectorParams(
                        index=models.SparseIndexParams(on_disk=False)
                    )
                },
            )
        self._initialized = True

    @staticmethod
    def _sparse(text: str) -> models.SparseVector:
        counts: dict[int, float] = {}
        for token in tokenize(text):
            index = int.from_bytes(token.encode("utf-8")[:4].ljust(4, b"\0"), "big")
            counts[index] = counts.get(index, 0.0) + 1.0
        ordered = sorted(counts.items())
        return models.SparseVector(
            indices=[item[0] for item in ordered], values=[item[1] for item in ordered]
        )

    async def upsert(self, chunks: list[KnowledgeChunk]) -> None:
        await self._ensure_collection()
        if not chunks:
            return
        texts = [
            " ".join(filter(None, [chunk.path, chunk.symbol, chunk.content])) for chunk in chunks
        ]
        embeddings = await asyncio.to_thread(self.embedder.embed_documents, texts)
        await self.client.upsert(
            collection_name=self.collection,
            points=[
                models.PointStruct(
                    id=str(uuid5(NAMESPACE_URL, chunk.id)),
                    vector={
                        "dense": embedding,
                        "sparse": self._sparse(
                            " ".join(filter(None, [chunk.path, chunk.symbol, chunk.content]))
                        ),
                    },
                    payload=chunk.model_dump(mode="json"),
                )
                for chunk, embedding in zip(chunks, embeddings, strict=True)
            ],
        )

    async def delete_by_repository_paths(self, repository: str, paths: set[str]) -> None:
        await self._ensure_collection()
        if not paths:
            return
        await self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="repository", match=models.MatchValue(value=repository)
                        ),
                        models.FieldCondition(key="path", match=models.MatchAny(any=list(paths))),
                    ]
                )
            ),
        )

    async def search(
        self,
        query: str,
        user: UserContext,
        repositories: list[str] | None = None,
        teams: list[str] | None = None,
        limit: int = 8,
    ) -> list[RetrievalHit]:
        await self._ensure_collection()
        conditions: list[Any] = [
            models.FieldCondition(key="team", match=models.MatchAny(any=list(user.teams)))
        ]
        allowed_repositories = repositories or list(user.repositories)
        if allowed_repositories:
            conditions.append(
                models.FieldCondition(
                    key="repository", match=models.MatchAny(any=allowed_repositories)
                )
            )
        query_filter = models.Filter(must=conditions)
        query_embedding = await asyncio.to_thread(self.embedder.embed_query, query)
        result = await self.client.query_points(
            collection_name=self.collection,
            prefetch=[
                models.Prefetch(
                    query=query_embedding,
                    using="dense",
                    filter=query_filter,
                    limit=limit * 3,
                ),
                models.Prefetch(
                    query=self._sparse(query), using="sparse", filter=query_filter, limit=limit * 3
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=max(limit * 4, 20),
            with_payload=True,
        )
        hits = [
            RetrievalHit(chunk=KnowledgeChunk.model_validate(point.payload), score=point.score)
            for point in result.points
            if point.payload
        ]
        return await self.reranker.rerank(query, hits, limit)


class Neo4jKnowledgeGraph(KnowledgeGraph):
    def __init__(self, uri: str, user: str, password: str) -> None:
        super().__init__()
        self.driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    async def upsert(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
        # Keep a local mirror for predictable fallback and demos while persisting the real graph.
        await super().upsert(nodes, edges)
        async with self.driver.session() as session:
            await session.run(
                "UNWIND $nodes AS node MERGE (n:Entity {id: node.id}) "
                "SET n.kind=node.kind, n.name=node.name, n += node.properties",
                nodes=[node.model_dump() for node in nodes],
            )
            # Relationship type is stored as a property to keep Cypher injection impossible.
            await session.run(
                "UNWIND $edges AS edge MATCH (a:Entity {id: edge.source}) "
                "MATCH (b:Entity {id: edge.target}) MERGE (a)-[r:RELATES_TO]->(b) "
                "SET r.kind=edge.relation, r += edge.properties",
                edges=[edge.model_dump() for edge in edges],
            )

    async def find_entities(self, terms: list[str], limit: int = 20) -> list[GraphNode]:
        async with self.driver.session() as session:
            result = await session.run(
                "MATCH (n:Entity) WHERE any(term IN $terms WHERE "
                "toLower(n.name) CONTAINS toLower(term) OR toLower(n.id) CONTAINS toLower(term)) "
                "RETURN n LIMIT $limit",
                terms=terms,
                limit=limit,
            )
            return [
                GraphNode(
                    id=record["n"]["id"],
                    kind=record["n"].get("kind", "Entity"),
                    name=record["n"].get("name", record["n"]["id"]),
                    properties=dict(record["n"]),
                )
                async for record in result
            ]

    async def traverse(self, start_ids: list[str], max_hops: int = 2) -> list[GraphPath]:
        # Hop count is fixed by configuration; never interpolate user-provided Cypher.
        hops = max(1, min(max_hops, 2))
        query = (
            "MATCH path=(start:Entity)-[rels:RELATES_TO*1.." + str(hops) + "]-(end:Entity) "
            "WHERE start.id IN $ids RETURN nodes(path) AS ns, relationships(path) AS rs LIMIT 50"
        )
        paths: list[GraphPath] = []
        async with self.driver.session() as session:
            result = await session.run(query, ids=start_ids)
            async for record in result:
                ns = list(record["ns"])
                rs = list(record["rs"])
                nodes = [
                    GraphNode(
                        id=node["id"],
                        kind=node.get("kind", "Entity"),
                        name=node.get("name", node["id"]),
                        properties=dict(node),
                    )
                    for node in ns
                ]
                edges = [
                    GraphEdge(
                        source=ns[index]["id"],
                        relation=relationship.get("kind", "RELATES_TO"),
                        target=ns[index + 1]["id"],
                        properties=dict(relationship),
                    )
                    for index, relationship in enumerate(rs)
                ]
                paths.append(GraphPath(nodes=nodes, edges=edges))
        return paths
