from __future__ import annotations

import re

from opentelemetry import trace

from app.models import (
    ImpactItem,
    ImpactRequest,
    ImpactResponse,
    QueryRequest,
    QueryResponse,
    RiskLevel,
    SourceType,
    UserContext,
)
from app.observability import current_trace_id
from app.services.changes import ChangeReader, extract_change_entities
from app.services.citations import unique_citations
from app.services.context import compress_chunks
from app.services.graph import KnowledgeGraph
from app.services.retrieval import HybridRetriever


tracer = trace.get_tracer(__name__)


def select_evidence(hits, max_items: int = 5, relative_threshold: float = 0.84):
    """Keep strongly relevant evidence while preserving at least the best candidate."""
    if not hits:
        return []
    threshold = hits[0].score * relative_threshold
    selected = [hit.chunk for hit in hits if hit.score >= threshold]
    return selected[:max_items] or [hits[0].chunk]


class KnowledgeWorkflow:
    """Explicit sequential workflow: rewrite -> retrieve -> graph -> compose."""

    def __init__(self, retriever: HybridRetriever, graph: KnowledgeGraph) -> None:
        self.retriever = retriever
        self.graph = graph

    async def answer(self, request: QueryRequest, user: UserContext) -> QueryResponse:
        with tracer.start_as_current_span("query.rewrite"):
            query = self._rewrite_query(request.question)
        with tracer.start_as_current_span("retrieval.hybrid"):
            hits = await self.retriever.search(
                query,
                user=user,
                repositories=request.repositories,
                teams=request.teams,
                limit=request.top_k,
            )
        with tracer.start_as_current_span("graph.expand"):
            terms = self._entity_terms(request.question, hits)
            entities = await self.graph.find_entities(terms)
            paths = await self.graph.traverse([entity.id for entity in entities], max_hops=2)
        with tracer.start_as_current_span("answer.compose"):
            chunks = compress_chunks(select_evidence(hits, max_items=5))
            answer = self._compose_answer(request.question, chunks, paths)
            warnings = [] if chunks else ["没有找到可验证的知识来源，未生成推断性答案。"]
        return QueryResponse(
            answer=answer,
            citations=unique_citations(chunks),
            related_paths=paths[:10],
            trace_id=current_trace_id(),
            warnings=warnings,
        )

    @staticmethod
    def _rewrite_query(question: str) -> str:
        aliases = {
            "调用了哪些": "调用 依赖 下游 API",
            "哪里被使用": "引用 读写 使用",
            "以前出现过": "历史 Issue 异常 解决方案",
            "影响哪些": "依赖 调用 影响 测试",
        }
        additions = [value for key, value in aliases.items() if key in question]
        return f"{question} {' '.join(additions)}".strip()

    @staticmethod
    def _entity_terms(question, hits) -> list[str]:
        identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_.:/-]*|[\u4e00-\u9fff]{2,}", question)
        symbols = [hit.chunk.symbol for hit in hits if hit.chunk.symbol]
        return list(dict.fromkeys([*identifiers, *symbols]))[:30]

    @staticmethod
    def _compose_answer(question, chunks, paths) -> str:
        if not chunks:
            return "现有授权范围内没有足够证据回答该问题。请先索引相关仓库或缩小问题范围。"
        lines = [f"针对“{question}”，检索到以下可验证信息："]
        for index, chunk in enumerate(chunks[:6], 1):
            content = " ".join(chunk.content.strip().split())
            lines.append(f"{index}. {chunk.symbol or chunk.path}：{content[:220]}")
        if paths:
            lines.append("\n关系图谱显示：")
            for path in paths[:5]:
                segments = []
                for idx, edge in enumerate(path.edges):
                    left = path.nodes[idx].name
                    right = path.nodes[idx + 1].name
                    segments.append(f"{left} -[{edge.relation}]-> {right}")
                lines.append(f"- {'；'.join(segments)}")
        lines.append("\n结论仅基于下方引用证据；未被代码、文档或图谱支持的关系不作推断。")
        return "\n".join(lines)


class ImpactWorkflow:
    """Explicit sequential workflow: change -> retrieval -> graph -> tests -> risk."""

    def __init__(
        self, retriever: HybridRetriever, graph: KnowledgeGraph, change_reader: ChangeReader
    ) -> None:
        self.retriever = retriever
        self.graph = graph
        self.change_reader = change_reader

    async def analyze(self, request: ImpactRequest, user: UserContext) -> ImpactResponse:
        with tracer.start_as_current_span("impact.read_change"):
            change = self.change_reader.read(request.input_type.value, request.value)
            files, symbols = extract_change_entities(change)
        query = " ".join([*files, *symbols, "依赖 调用 API 数据表 Issue 测试"])
        with tracer.start_as_current_span("impact.retrieve"):
            hits = await self.retriever.search(
                query, user=user, repositories=[request.repository], limit=20
            )
        with tracer.start_as_current_span("impact.graph"):
            graph_entities = await self.graph.find_entities([*files, *symbols])
            paths = await self.graph.traverse([entity.id for entity in graph_entities], max_hops=2)
        with tracer.start_as_current_span("impact.classify"):
            affected = self._affected(paths)
            issues = self._from_hits(hits, SourceType.ISSUE, "historical_issue")
            tests = self._tests(hits, paths)
            level, reasons = self._risk(change, affected, tests, issues)
            chunks = compress_chunks(select_evidence(hits, max_items=8, relative_threshold=0.55))
        return ImpactResponse(
            summary=(
                f"识别到 {len(files)} 个变更文件、{len(symbols)} 个符号，"
                f"沿一至两跳依赖发现 {len(affected)} 个潜在影响项。"
            ),
            changed_symbols=list(dict.fromkeys([*files, *symbols])),
            affected=affected,
            historical_issues=issues,
            suggested_tests=tests,
            risk_level=level,
            risk_reasons=reasons,
            citations=unique_citations(chunks),
            trace_id=current_trace_id(),
        )

    @staticmethod
    def _affected(paths) -> list[ImpactItem]:
        result: list[ImpactItem] = []
        seen: set[str] = set()
        for path in paths:
            target = path.nodes[-1]
            if target.id in seen or target.kind in {"File", "Symbol"}:
                continue
            seen.add(target.id)
            result.append(
                ImpactItem(
                    kind=target.kind.lower(),
                    name=target.name,
                    reason="通过知识图谱依赖路径关联",
                    path=[node.name for node in path.nodes],
                )
            )
        return result[:30]

    @staticmethod
    def _from_hits(hits, source_type, kind):
        return [
            ImpactItem(
                kind=kind,
                name=hit.chunk.symbol or hit.chunk.path,
                reason="与变更实体的混合检索结果相关",
                path=[hit.chunk.path],
            )
            for hit in hits
            if hit.chunk.source_type == source_type
        ][:10]

    def _tests(self, hits, paths):
        direct = self._from_hits(hits, SourceType.TEST, "test")
        seen = {item.name for item in direct}
        for path in paths:
            for node in path.nodes:
                if node.kind == "Test" and node.name not in seen:
                    direct.append(
                        ImpactItem(
                            kind="test",
                            name=node.name,
                            reason="图谱中存在 COVERS 覆盖关系",
                            path=[item.name for item in path.nodes],
                        )
                    )
                    seen.add(node.name)
        return direct[:15]

    @staticmethod
    def _risk(change, affected, tests, issues):
        score = 0
        reasons = []
        lowered = change.lower()
        destructive = any(
            term in lowered for term in ["drop ", "delete ", "remove", "删除", "rename"]
        )
        schema = any(term in lowered for term in ["table", "column", "字段", "数据库", ".sql"])
        if destructive:
            score += 3
            reasons.append("检测到删除或重命名等破坏性变更")
        if schema:
            score += 2
            reasons.append("变更涉及数据库表或字段契约")
        if len(affected) >= 5:
            score += 2
            reasons.append("影响路径跨越至少 5 个实体")
        elif affected:
            score += 1
            reasons.append("存在跨模块依赖路径")
        if issues:
            score += 1
            reasons.append("检索到相关历史问题")
        if not tests:
            score += 2
            reasons.append("未找到直接关联的测试覆盖证据")
        if score >= 7:
            level = RiskLevel.CRITICAL
        elif score >= 4:
            level = RiskLevel.HIGH
        elif score >= 2:
            level = RiskLevel.MEDIUM
        else:
            level = RiskLevel.LOW
        return level, reasons or ["变更范围较小且存在测试证据"]
