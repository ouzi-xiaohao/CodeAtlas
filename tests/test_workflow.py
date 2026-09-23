from pathlib import Path

import pytest

from app.models import ChangeInputType, ImpactRequest, QueryRequest, UserContext
from app.services.changes import ChangeReader
from app.services.graph import KnowledgeGraph
from app.services.retrieval import HybridRetriever
from app.services.seed import seed_demo
from app.services.workflow import ImpactWorkflow, KnowledgeWorkflow


@pytest.mark.asyncio
async def test_query_always_returns_citations_for_supported_answer():
    retriever, graph = HybridRetriever(), KnowledgeGraph()
    await seed_demo(retriever, graph)
    result = await KnowledgeWorkflow(retriever, graph).answer(
        QueryRequest(question="订单创建失败可能涉及哪些模块？"),
        UserContext(user_id="tester"),
    )
    assert result.citations
    assert "引用" in result.answer


@pytest.mark.asyncio
async def test_destructive_schema_change_is_high_risk(tmp_path: Path):
    retriever, graph = HybridRetriever(), KnowledgeGraph()
    await seed_demo(retriever, graph)
    result = await ImpactWorkflow(retriever, graph, ChangeReader(tmp_path)).analyze(
        ImpactRequest(
            input_type=ChangeInputType.DESCRIPTION,
            value="删除 orders.status 字段 remove column",
            repository="nexus-commerce-platform",
        ),
        UserContext(user_id="tester"),
    )
    assert result.risk_level in {"high", "critical"}
    assert result.citations
