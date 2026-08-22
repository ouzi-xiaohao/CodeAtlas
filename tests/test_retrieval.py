import pytest

from app.models import KnowledgeChunk, SourceType, UserContext
from app.services.retrieval import FastEmbedEmbeddingProvider, FastEmbedReranker, HybridRetriever


@pytest.mark.asyncio
async def test_hybrid_retrieval_prefers_exact_symbol_and_applies_acl():
    retriever = HybridRetriever()
    await retriever.upsert(
        [
            KnowledgeChunk(
                id="allowed",
                repository="shop",
                team="payments",
                source_type=SourceType.CODE,
                path="payment.py",
                symbol="PaymentService.reserve",
                content="reserve payment for an order",
            ),
            KnowledgeChunk(
                id="forbidden",
                repository="secret",
                team="security",
                source_type=SourceType.CODE,
                path="secret.py",
                symbol="PaymentService.reserve",
                content="secret payment implementation",
            ),
        ]
    )
    hits = await retriever.search(
        "PaymentService.reserve",
        UserContext(user_id="alice", teams={"payments"}, repositories={"shop"}),
    )
    assert [hit.chunk.id for hit in hits] == ["allowed"]


@pytest.mark.asyncio
async def test_reranker_promotes_exact_code_symbol():
    retriever = HybridRetriever()
    await retriever.upsert(
        [
            KnowledgeChunk(
                id="exact",
                repository="shop",
                team="default",
                source_type=SourceType.CODE,
                path="catalog.py",
                symbol="CatalogService.execute",
                content="calls the search downstream",
            ),
            KnowledgeChunk(
                id="generic",
                repository="shop",
                team="default",
                source_type=SourceType.CODE,
                path="payment.py",
                symbol="PaymentService.execute",
                content="service calls a downstream dependency",
            ),
        ]
    )
    hits = await retriever.search(
        "CatalogService.execute 调用了哪个下游？", UserContext(user_id="alice"), limit=2
    )
    assert hits[0].chunk.id == "exact"


@pytest.mark.asyncio
async def test_reranker_uses_test_intent():
    retriever = HybridRetriever()
    await retriever.upsert(
        [
            KnowledgeChunk(
                id="issue",
                repository="shop",
                team="default",
                source_type=SourceType.ISSUE,
                path="issues/ORDER-1.md",
                symbol="ORDER-1",
                content="订单契约失败，最终补充了测试",
            ),
            KnowledgeChunk(
                id="test",
                repository="shop",
                team="default",
                source_type=SourceType.TEST,
                path="tests/test_order_contract.py",
                symbol="test_order_contract",
                content="订单状态契约测试",
            ),
        ]
    )
    hits = await retriever.search("订单应该执行什么契约测试？", UserContext(user_id="alice"))
    assert hits[0].chunk.id == "test"


def test_fastembed_adapters_are_lazy_and_infer_model_dimensions():
    embedder = FastEmbedEmbeddingProvider("BAAI/bge-small-zh-v1.5", dimensions=1)
    reranker = FastEmbedReranker("BAAI/bge-reranker-base")

    assert embedder.dimensions == 512
    assert embedder._model is None
    assert reranker._model is None
