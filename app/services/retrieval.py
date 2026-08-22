from __future__ import annotations

import asyncio
import hashlib
import math
import re
from collections import Counter
from typing import Protocol

import httpx

from app.models import KnowledgeChunk, RetrievalHit, SourceType, UserContext


TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]*|[\u4e00-\u9fff]+")
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/{}-]*")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for token in TOKEN_PATTERN.findall(text):
        normalized = token.lower()
        if re.fullmatch(r"[\u4e00-\u9fff]+", normalized):
            tokens.extend(normalized)
            tokens.extend(normalized[index : index + 2] for index in range(len(normalized) - 1))
        else:
            tokens.append(normalized)
    return tokens


def hashed_embedding(text: str, dimensions: int = 256) -> list[float]:
    """Dependency-free embedding used in local mode, not intended for production quality."""
    vector = [0.0] * dimensions
    for token, count in Counter(tokenize(text)).items():
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign * (1.0 + math.log(count))
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


class Reranker(Protocol):
    async def rerank(
        self, query: str, hits: list[RetrievalHit], limit: int
    ) -> list[RetrievalHit]: ...


class EmbeddingProvider(Protocol):
    dimensions: int

    def embed_query(self, text: str) -> list[float]: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbeddingProvider:
    dimensions = 256

    def embed_query(self, text: str) -> list[float]:
        return hashed_embedding(text, self.dimensions)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [hashed_embedding(text, self.dimensions) for text in texts]


class FastEmbedEmbeddingProvider:
    """Lazy ONNX embedding provider backed by Qdrant FastEmbed."""

    def __init__(self, model_name: str, dimensions: int) -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self._model = None
        try:
            from fastembed import TextEmbedding

            description = next(
                (
                    item
                    for item in TextEmbedding.list_supported_models()
                    if item.get("model") == model_name
                ),
                None,
            )
            if description and description.get("dim"):
                self.dimensions = int(description["dim"])
        except ImportError:
            pass

    def _get_model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed_query(self, text: str) -> list[float]:
        vector = next(iter(self._get_model().query_embed(text)))
        return vector.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [vector.tolist() for vector in self._get_model().passage_embed(texts)]


class FeatureReranker:
    """Deterministic local reranker for offline development and graceful fallback.

    It combines candidate rank, semantic similarity, token coverage and exact code-symbol
    matches. Production deployments can replace it with a cross-encoder through
    ``HttpReranker`` without changing the retrieval workflow.
    """

    async def rerank(self, query: str, hits: list[RetrievalHit], limit: int) -> list[RetrievalHit]:
        if not hits:
            return []
        query_tokens = set(tokenize(query))
        identifiers = {item.lower() for item in IDENTIFIER_PATTERN.findall(query)}
        intent_sources = self._intent_sources(query)
        query_vector = hashed_embedding(query)
        best_fusion_score = max(hit.score for hit in hits) or 1.0
        reranked: list[RetrievalHit] = []
        for hit in hits:
            chunk = hit.chunk
            text = " ".join(filter(None, [chunk.path, chunk.symbol, chunk.content]))
            document_tokens = set(tokenize(text))
            coverage = len(query_tokens & document_tokens) / max(len(query_tokens), 1)
            normalized_text = text.lower()
            identifier_coverage = (
                sum(identifier in normalized_text for identifier in identifiers)
                / max(len(identifiers), 1)
                if identifiers
                else 0.0
            )
            symbol_text = f"{chunk.path} {chunk.symbol or ''}".lower()
            symbol_match = float(any(item in symbol_text for item in identifiers))
            semantic = max(0.0, cosine(query_vector, hashed_embedding(text)))
            fusion = hit.score / best_fusion_score
            intent_match = float(chunk.source_type in intent_sources) if intent_sources else 0.0
            score = (
                0.28 * coverage
                + 0.22 * identifier_coverage
                + 0.16 * symbol_match
                + 0.10 * semantic
                + 0.08 * fusion
                + 0.16 * intent_match
            )
            reranked.append(hit.model_copy(update={"score": round(score, 8)}))
        return sorted(reranked, key=lambda item: item.score, reverse=True)[:limit]

    @staticmethod
    def _intent_sources(query: str) -> set[SourceType]:
        lowered = query.lower()
        sources: set[SourceType] = set()
        if any(term in lowered for term in ("测试", "契约", "test", "覆盖")):
            sources.add(SourceType.TEST)
        if any(term in lowered for term in ("历史", "以前", "异常", "问题", "issue", "解决")):
            sources.add(SourceType.ISSUE)
        if any(term in lowered for term in ("字段", "数据表", "ddl", "column", "读写")):
            sources.update({SourceType.DDL, SourceType.CODE})
        if any(term in lowered for term in ("实现", "接口", "api", "post ", "get ")):
            sources.update({SourceType.CODE, SourceType.OPENAPI, SourceType.DOCUMENT})
        if any(term in lowered for term in ("调用", "下游", "模块", "服务", "哪里")):
            sources.update({SourceType.CODE, SourceType.DOCUMENT})
        return sources


class HttpReranker:
    """Adapter for a cross-encoder reranking HTTP endpoint with local fallback."""

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        self.url = url
        self.timeout = timeout
        self.fallback = FeatureReranker()

    async def rerank(self, query: str, hits: list[RetrievalHit], limit: int) -> list[RetrievalHit]:
        if not hits:
            return []
        documents = [
            " ".join(filter(None, [hit.chunk.path, hit.chunk.symbol, hit.chunk.content]))
            for hit in hits
        ]
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.url,
                    json={"query": query, "documents": documents, "top_n": limit},
                )
                response.raise_for_status()
                results = response.json()["results"]
            return [
                hits[int(result["index"])].model_copy(update={"score": float(result["score"])})
                for result in results[:limit]
            ]
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError):
            return await self.fallback.rerank(query, hits, limit)


class FastEmbedReranker:
    """Local Cross-Encoder reranker using an ONNX model from FastEmbed."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = None
        self.fallback = FeatureReranker()

    def _get_model(self):
        if self._model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            self._model = TextCrossEncoder(model_name=self.model_name)
        return self._model

    async def rerank(self, query: str, hits: list[RetrievalHit], limit: int) -> list[RetrievalHit]:
        if not hits:
            return []
        documents = [
            " ".join(filter(None, [hit.chunk.path, hit.chunk.symbol, hit.chunk.content]))
            for hit in hits
        ]
        try:
            scores = await asyncio.to_thread(
                lambda: list(self._get_model().rerank(query, documents))
            )
        except (ImportError, OSError, RuntimeError, ValueError):
            return await self.fallback.rerank(query, hits, limit)
        scored = [
            hit.model_copy(update={"score": float(score)})
            for hit, score in zip(hits, scores, strict=True)
        ]
        return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]


class HybridRetriever:
    """Dense + sparse retrieval with reciprocal-rank fusion and ACL filtering.

    Local mode stores chunks in memory. The interface is deliberately compatible with
    a Qdrant-backed implementation so deployments can replace storage without changing
    the deterministic workflow.
    """

    def __init__(
        self,
        reranker: Reranker | None = None,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self._chunks: dict[str, KnowledgeChunk] = {}
        self._embeddings: dict[str, list[float]] = {}
        self.reranker = reranker or FeatureReranker()
        self.embedder = embedder or HashEmbeddingProvider()

    async def upsert(self, chunks: list[KnowledgeChunk]) -> None:
        texts = [
            " ".join(filter(None, [chunk.path, chunk.symbol, chunk.content])) for chunk in chunks
        ]
        embeddings = await asyncio.to_thread(self.embedder.embed_documents, texts)
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            self._chunks[chunk.id] = chunk
            self._embeddings[chunk.id] = embedding

    async def delete_by_repository_paths(self, repository: str, paths: set[str]) -> None:
        stale = [
            chunk_id
            for chunk_id, chunk in self._chunks.items()
            if chunk.repository == repository and chunk.path in paths
        ]
        for chunk_id in stale:
            self._chunks.pop(chunk_id, None)
            self._embeddings.pop(chunk_id, None)

    def _allowed(
        self,
        chunk: KnowledgeChunk,
        user: UserContext,
        repositories: list[str],
        teams: list[str],
    ) -> bool:
        if user.repositories and chunk.repository not in user.repositories:
            return False
        if chunk.team not in user.teams:
            return False
        if repositories and chunk.repository not in repositories:
            return False
        return not teams or chunk.team in teams

    async def search(
        self,
        query: str,
        user: UserContext,
        repositories: list[str] | None = None,
        teams: list[str] | None = None,
        limit: int = 8,
    ) -> list[RetrievalHit]:
        candidates = [
            chunk
            for chunk in self._chunks.values()
            if self._allowed(chunk, user, repositories or [], teams or [])
        ]
        query_tokens = Counter(tokenize(query))
        query_embedding = await asyncio.to_thread(self.embedder.embed_query, query)

        sparse = sorted(
            candidates,
            key=lambda chunk: self._sparse_score(query_tokens, chunk),
            reverse=True,
        )
        dense = sorted(
            candidates,
            key=lambda chunk: cosine(query_embedding, self._embeddings[chunk.id]),
            reverse=True,
        )
        sparse_rank = {chunk.id: rank for rank, chunk in enumerate(sparse, 1)}
        dense_rank = {chunk.id: rank for rank, chunk in enumerate(dense, 1)}

        # RRF is robust when dense and exact symbol matching disagree.
        fused = sorted(
            candidates,
            key=lambda chunk: 1 / (60 + sparse_rank[chunk.id]) + 1 / (60 + dense_rank[chunk.id]),
            reverse=True,
        )
        initial_hits = [
            RetrievalHit(
                chunk=chunk,
                score=1 / (60 + sparse_rank[chunk.id]) + 1 / (60 + dense_rank[chunk.id]),
                sparse_rank=sparse_rank[chunk.id],
                dense_rank=dense_rank[chunk.id],
            )
            for chunk in fused[: max(limit * 4, 20)]
            if self._sparse_score(query_tokens, chunk) > 0
            or cosine(query_embedding, self._embeddings[chunk.id]) > 0
        ]
        return await self.reranker.rerank(query, initial_hits, limit)

    @staticmethod
    def _sparse_score(query: Counter[str], chunk: KnowledgeChunk) -> float:
        haystack = Counter(tokenize(" ".join([chunk.path, chunk.symbol or "", chunk.content])))
        score = 0.0
        for token, query_count in query.items():
            if token in haystack:
                exact_symbol_bonus = 4.0 if token == (chunk.symbol or "").lower() else 1.0
                score += query_count * exact_symbol_bonus * (1 + math.log(haystack[token]))
        return score

    @property
    def size(self) -> int:
        return len(self._chunks)
