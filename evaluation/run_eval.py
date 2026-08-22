from __future__ import annotations

import asyncio
import json
import math
import statistics
import time
from collections import Counter
from pathlib import Path

from app.models import (
    ChangeInputType,
    GraphEdge,
    GraphNode,
    ImpactRequest,
    KnowledgeChunk,
    QueryRequest,
    SourceType,
    UserContext,
)
from app.services.changes import ChangeReader
from app.services.graph import KnowledgeGraph
from app.services.retrieval import HybridRetriever, cosine, hashed_embedding, tokenize
from app.services.workflow import ImpactWorkflow, KnowledgeWorkflow


ROOT = Path(__file__).resolve().parents[1]


def chunk(
    chunk_id: str,
    source_type: SourceType,
    path: str,
    symbol: str,
    content: str,
) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=chunk_id,
        repository="eval-commerce",
        team="default",
        source_type=source_type,
        path=path,
        symbol=symbol,
        content=content,
        line_start=1,
        line_end=max(1, content.count("\n") + 1),
    )


CHUNKS = [
    chunk(
        "order-service",
        SourceType.CODE,
        "services/order/service.py",
        "OrderService.create_order",
        "订单创建 create_order 校验用户，写入 orders.status，并调用 PaymentClient.reserve 支付预占和 InventoryClient.lock 库存锁定。",
    ),
    chunk(
        "payment-service",
        SourceType.CODE,
        "services/payment/client.py",
        "PaymentClient.reserve",
        "支付服务 payment-service 调用银行下游 POST /bank/reservations，失败时抛出 PaymentReserveError。",
    ),
    chunk(
        "order-api",
        SourceType.OPENAPI,
        "openapi/order.yaml",
        "POST /orders",
        "POST /orders 创建订单，由 OrderService.create_order 实现，返回订单状态 status。",
    ),
    chunk(
        "user-dao",
        SourceType.CODE,
        "services/user/dao.py",
        "UserDAO.find_by_phone",
        "用户手机号 user.phone 在 UserDAO.find_by_phone 中读取，用于手机号登录和用户查询。",
    ),
    chunk(
        "user-api",
        SourceType.OPENAPI,
        "openapi/user.yaml",
        "PATCH /users/{id}/phone",
        "用户 API PATCH /users/{id}/phone 更新手机号字段，调用 UserService.update_phone。",
    ),
    chunk(
        "user-ddl",
        SourceType.DDL,
        "database/users.sql",
        "users.phone",
        "CREATE TABLE users (id bigint, phone varchar(32)); users.phone 建有唯一索引。",
    ),
    chunk(
        "phone-test",
        SourceType.TEST,
        "tests/user/test_phone_login.py",
        "test_login_by_phone",
        "测试覆盖 users.phone 手机号登录、手机号更新与重复手机号校验。",
    ),
    chunk(
        "payment-issue",
        SourceType.ISSUE,
        "issues/INC-142.md",
        "INC-142",
        "历史异常：支付状态枚举变更导致订单创建失败；通过更新 webhook 状态映射并增加契约测试修复。",
    ),
    chunk(
        "order-test",
        SourceType.TEST,
        "tests/contract/test_order_payment.py",
        "test_order_payment_status_contract",
        "契约测试覆盖 orders.status 订单状态映射、支付预占失败和 webhook 回调。",
    ),
    chunk(
        "inventory-service",
        SourceType.CODE,
        "services/inventory/service.py",
        "InventoryService.lock",
        "库存服务 InventoryService.lock 锁定 SKU 库存，由订单服务在创建订单时调用。",
    ),
    chunk(
        "refund-doc",
        SourceType.DOCUMENT,
        "docs/refund.md",
        "退款流程",
        "退款流程先调用 payment-service 的 POST /refunds，再将订单状态更新为 REFUNDED。",
    ),
    chunk(
        "auth-issue",
        SourceType.ISSUE,
        "issues/INC-201.md",
        "INC-201",
        "认证服务 auth-service 曾因 Redis 连接超时导致登录失败，解决方案为连接池限流与熔断。",
    ),
]


CASES = [
    ("订单创建失败可能涉及哪些模块？", {"order-service", "payment-service", "inventory-service"}),
    ("支付服务调用了哪个银行下游接口？", {"payment-service"}),
    ("users.phone 手机号字段在哪里被使用？", {"user-dao", "user-api", "user-ddl"}),
    ("支付状态枚举异常以前出现过吗？", {"payment-issue"}),
    ("删除 user.phone 应该执行哪些测试？", {"user-dao", "user-ddl", "phone-test"}),
    ("orders.status 变更有哪些契约测试和历史问题？", {"order-test", "payment-issue"}),
    ("InventoryService.lock 被谁调用？", {"inventory-service", "order-service"}),
    ("退款流程会调用什么 API？", {"refund-doc"}),
    ("Redis 超时导致登录失败如何解决？", {"auth-issue"}),
    ("POST /orders 的实现在哪里？", {"order-api", "order-service"}),
]


DOMAIN_SPECS = [
    (
        "catalog",
        "商品目录",
        "CatalogService",
        "products.category_id",
        "search-service",
        "CATALOG-301",
    ),
    (
        "shipping",
        "物流配送",
        "ShippingService",
        "shipments.tracking_no",
        "carrier-gateway",
        "SHIP-302",
    ),
    ("coupon", "优惠券", "CouponService", "coupons.valid_until", "pricing-service", "COUPON-303"),
    (
        "notification",
        "消息通知",
        "NotifyService",
        "notifications.channel",
        "sms-gateway",
        "NOTIFY-304",
    ),
    ("invoice", "电子发票", "InvoiceService", "invoices.tax_no", "tax-gateway", "INVOICE-305"),
    ("review", "商品评价", "ReviewService", "reviews.rating", "moderation-service", "REVIEW-306"),
    ("search", "商品搜索", "SearchService", "search_docs.version", "elasticsearch", "SEARCH-307"),
    ("risk", "交易风控", "RiskService", "risk_events.decision", "rule-engine", "RISK-308"),
    (
        "warehouse",
        "仓库履约",
        "WarehouseService",
        "stocks.available",
        "wms-gateway",
        "WAREHOUSE-309",
    ),
    ("loyalty", "会员积分", "LoyaltyService", "points.balance", "member-service", "LOYALTY-310"),
]


for key, chinese, service, field, downstream, issue in DOMAIN_SPECS:
    generated = [
        chunk(
            f"{key}-service",
            SourceType.CODE,
            f"services/{key}/service.py",
            f"{service}.execute",
            f"{chinese}核心逻辑由 {service}.execute 实现，读写 {field}，并调用下游 {downstream}。",
        ),
        chunk(
            f"{key}-api",
            SourceType.OPENAPI,
            f"openapi/{key}.yaml",
            f"POST /{key}/actions",
            f"{chinese}接口 POST /{key}/actions 调用 {service}.execute 并返回处理状态。",
        ),
        chunk(
            f"{key}-ddl",
            SourceType.DDL,
            f"database/{key}.sql",
            field,
            f"{chinese}数据库字段 {field} 为非空业务字段，并建立查询索引。",
        ),
        chunk(
            f"{key}-test",
            SourceType.TEST,
            f"tests/{key}/test_contract.py",
            f"test_{key}_contract",
            f"{chinese}契约测试覆盖 {service}.execute、{field} 变更和 {downstream} 调用失败。",
        ),
        chunk(
            f"{key}-issue",
            SourceType.ISSUE,
            f"issues/{issue}.md",
            issue,
            f"历史问题 {issue}：{chinese}处理失败，根因是 {downstream} 超时；通过重试、熔断和契约测试修复。",
        ),
    ]
    CHUNKS.extend(generated)
    CASES.extend(
        [
            (f"{chinese}接口的代码实现在哪里？", {f"{key}-service", f"{key}-api"}),
            (f"字段 {field} 在哪里读写？", {f"{key}-service", f"{key}-ddl"}),
            (f"{chinese}以前发生过什么超时问题？", {f"{key}-issue"}),
            (f"修改 {chinese}应该执行什么契约测试？", {f"{key}-test"}),
            (f"{service}.execute 调用了哪个下游？", {f"{key}-service"}),
        ]
    )


NODES = [
    GraphNode(id="field:users.phone", kind="Field", name="users.phone"),
    GraphNode(id="dao:user", kind="Function", name="UserDAO.find_by_phone"),
    GraphNode(id="api:user-phone", kind="API", name="PATCH /users/{id}/phone"),
    GraphNode(id="test:phone", kind="Test", name="test_login_by_phone"),
    GraphNode(id="field:orders.status", kind="Field", name="orders.status"),
    GraphNode(id="service:order", kind="Service", name="order-service"),
    GraphNode(id="service:payment", kind="Service", name="payment-service"),
    GraphNode(id="service:inventory", kind="Service", name="inventory-service"),
    GraphNode(id="test:order", kind="Test", name="test_order_payment_status_contract"),
]


EDGES = [
    GraphEdge(source="dao:user", relation="READS", target="field:users.phone"),
    GraphEdge(source="api:user-phone", relation="WRITES", target="field:users.phone"),
    GraphEdge(source="test:phone", relation="COVERS", target="api:user-phone"),
    GraphEdge(source="service:order", relation="WRITES", target="field:orders.status"),
    GraphEdge(source="service:order", relation="CALLS", target="service:payment"),
    GraphEdge(source="service:order", relation="CALLS", target="service:inventory"),
    GraphEdge(source="test:order", relation="COVERS", target="service:order"),
]


for key, _chinese, service, field, downstream, _issue in DOMAIN_SPECS:
    NODES.extend(
        [
            GraphNode(id=f"service:{key}", kind="Service", name=service),
            GraphNode(id=f"field:{field}", kind="Field", name=field),
            GraphNode(id=f"service:{downstream}", kind="Service", name=downstream),
            GraphNode(id=f"test:{key}", kind="Test", name=f"test_{key}_contract"),
        ]
    )
    EDGES.extend(
        [
            GraphEdge(source=f"service:{key}", relation="WRITES", target=f"field:{field}"),
            GraphEdge(source=f"service:{key}", relation="CALLS", target=f"service:{downstream}"),
            GraphEdge(source=f"test:{key}", relation="COVERS", target=f"service:{key}"),
        ]
    )


def sparse_score(query: str, item: KnowledgeChunk) -> float:
    query_tokens = Counter(tokenize(query))
    haystack = Counter(tokenize(" ".join([item.path, item.symbol or "", item.content])))
    score = 0.0
    for token, count in query_tokens.items():
        if token in haystack:
            score += count * (1 + math.log(haystack[token]))
    return score


async def ranking(mode: str, query: str, reranked_retriever: HybridRetriever) -> list[str]:
    dense = sorted(
        CHUNKS,
        key=lambda item: cosine(
            hashed_embedding(query),
            hashed_embedding(" ".join([item.path, item.symbol or "", item.content])),
        ),
        reverse=True,
    )
    sparse = sorted(CHUNKS, key=lambda item: sparse_score(query, item), reverse=True)
    if mode == "dense":
        return [item.id for item in dense]
    if mode == "sparse":
        return [item.id for item in sparse]
    dense_rank = {item.id: rank for rank, item in enumerate(dense, 1)}
    sparse_rank = {item.id: rank for rank, item in enumerate(sparse, 1)}
    fused = sorted(
        CHUNKS,
        key=lambda item: 1 / (60 + dense_rank[item.id]) + 1 / (60 + sparse_rank[item.id]),
        reverse=True,
    )
    if mode == "hybrid_rrf_rerank":
        hits = await reranked_retriever.search(
            query,
            UserContext(user_id="evaluator", repositories={"eval-commerce"}),
            repositories=["eval-commerce"],
            limit=len(CHUNKS),
        )
        ranked_ids = [hit.chunk.id for hit in hits]
        return [*ranked_ids, *[item.id for item in fused if item.id not in ranked_ids]]
    return [item.id for item in fused]


async def retrieval_metrics(mode: str, reranked_retriever: HybridRetriever) -> dict[str, float]:
    recalls_3, recalls_5, reciprocal_ranks, precisions_5, ndcgs_5, success_1 = (
        [],
        [],
        [],
        [],
        [],
        [],
    )
    for query, relevant in CASES:
        ranked = await ranking(mode, query, reranked_retriever)
        recalls_3.append(len(set(ranked[:3]) & relevant) / len(relevant))
        recalls_5.append(len(set(ranked[:5]) & relevant) / len(relevant))
        first = min((ranked.index(item) + 1 for item in relevant), default=0)
        reciprocal_ranks.append(1 / first if first else 0)
        precisions_5.append(len(set(ranked[:5]) & relevant) / 5)
        dcg = sum(
            (1.0 if item in relevant else 0.0) / math.log2(rank + 1)
            for rank, item in enumerate(ranked[:5], 1)
        )
        ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(5, len(relevant)) + 1))
        ndcgs_5.append(dcg / ideal if ideal else 0.0)
        success_1.append(float(ranked[0] in relevant))
    return {
        "recall@3": round(statistics.mean(recalls_3), 4),
        "recall@5": round(statistics.mean(recalls_5), 4),
        "mrr": round(statistics.mean(reciprocal_ranks), 4),
        "precision@5": round(statistics.mean(precisions_5), 4),
        "ndcg@5": round(statistics.mean(ndcgs_5), 4),
        "success@1": round(statistics.mean(success_1), 4),
    }


async def error_analysis(reranked_retriever: HybridRetriever) -> dict:
    rrf_top1_errors = 0
    reranked_top1_errors = 0
    changed_queries = 0
    for query, relevant in CASES:
        rrf = await ranking("hybrid_rrf", query, reranked_retriever)
        reranked = await ranking("hybrid_rrf_rerank", query, reranked_retriever)
        rrf_top1_errors += rrf[0] not in relevant
        reranked_top1_errors += reranked[0] not in relevant
        changed_queries += rrf[:5] != reranked[:5]
    return {
        "rrf_top1_errors": rrf_top1_errors,
        "reranked_top1_errors": reranked_top1_errors,
        "changed_top5_queries": changed_queries,
    }


async def citation_threshold_sweep(reranked_retriever: HybridRetriever) -> dict[str, dict]:
    results = {}
    user = UserContext(user_id="evaluator", repositories={"eval-commerce"})
    for threshold in (0.60, 0.68, 0.72, 0.76, 0.80, 0.84):
        recalls, precisions = [], []
        for question, relevant in CASES:
            rewritten = KnowledgeWorkflow._rewrite_query(question)
            hits = await reranked_retriever.search(
                rewritten,
                user,
                repositories=["eval-commerce"],
                limit=5,
            )
            selected = {
                hit.chunk.id for hit in hits if hits and hit.score >= hits[0].score * threshold
            }
            recalls.append(len(selected & relevant) / len(relevant))
            precisions.append(len(selected & relevant) / len(selected) if selected else 0.0)
        recall = statistics.mean(recalls)
        precision = statistics.mean(precisions)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        results[f"{threshold:.2f}"] = {
            "citation_recall": round(recall, 4),
            "citation_precision": round(precision, 4),
            "f1": round(f1, 4),
        }
    return results


def percentile(values: list[float], percentage: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(percentage * len(ordered)) - 1)
    return ordered[index]


async def workflow_metrics() -> dict:
    retriever, graph = HybridRetriever(), KnowledgeGraph()
    await retriever.upsert(CHUNKS)
    await graph.upsert(NODES, EDGES)
    user = UserContext(user_id="evaluator", repositories={"eval-commerce"})
    knowledge = KnowledgeWorkflow(retriever, graph)
    impact = ImpactWorkflow(retriever, graph, ChangeReader(ROOT))

    query_latencies = []
    citation_recalls = []
    citation_precisions = []
    for _ in range(3):
        for question, relevant in CASES:
            started = time.perf_counter()
            response = await knowledge.answer(
                QueryRequest(question=question, repositories=["eval-commerce"], top_k=5),
                user,
            )
            query_latencies.append((time.perf_counter() - started) * 1000)
            cited = {
                item.id
                for item in CHUNKS
                if any(
                    citation.label == (item.symbol or item.path) for citation in response.citations
                )
            }
            citation_recalls.append(len(cited & relevant) / len(relevant))
            citation_precisions.append(len(cited & relevant) / len(cited) if cited else 0)

    impact_cases = [
        ("删除 users.phone 字段 remove column", {"high", "critical"}),
        ("删除 orders.status 字段 drop column", {"high", "critical"}),
        ("修改退款文档中的拼写", {"low", "medium"}),
    ]
    impact_cases.extend(
        (f"删除 {field} 字段 drop column", {"high", "critical"})
        for _key, _chinese, _service, field, _downstream, _issue in DOMAIN_SPECS
    )
    impact_cases.extend(
        [
            ("修正商品目录文档标点", {"low", "medium"}),
            ("补充物流配送接口说明", {"low", "medium"}),
        ]
    )
    impact_latencies = []
    correct_risks = 0
    for description, expected in impact_cases:
        started = time.perf_counter()
        response = await impact.analyze(
            ImpactRequest(
                input_type=ChangeInputType.DESCRIPTION,
                value=description,
                repository="eval-commerce",
            ),
            user,
        )
        impact_latencies.append((time.perf_counter() - started) * 1000)
        correct_risks += response.risk_level.value in expected

    return {
        "queries": len(CASES),
        "query_runs": len(query_latencies),
        "citation_recall": round(statistics.mean(citation_recalls), 4),
        "citation_precision": round(statistics.mean(citation_precisions), 4),
        "query_latency_ms": {
            "mean": round(statistics.mean(query_latencies), 2),
            "p50": round(percentile(query_latencies, 0.50), 2),
            "p95": round(percentile(query_latencies, 0.95), 2),
        },
        "impact_cases": len(impact_cases),
        "risk_classification_accuracy": round(correct_risks / len(impact_cases), 4),
        "impact_latency_ms": {
            "mean": round(statistics.mean(impact_latencies), 2),
            "p95": round(percentile(impact_latencies, 0.95), 2),
        },
    }


async def main() -> None:
    reranked_retriever = HybridRetriever()
    await reranked_retriever.upsert(CHUNKS)
    retrieval = {}
    for mode in ("dense", "sparse", "hybrid_rrf", "hybrid_rrf_rerank"):
        retrieval[mode] = await retrieval_metrics(mode, reranked_retriever)
    before = retrieval["hybrid_rrf"]
    after = retrieval["hybrid_rrf_rerank"]
    improvements = {
        metric: round((after[metric] - before[metric]) * 100, 2)
        for metric in ("recall@3", "recall@5", "mrr", "ndcg@5", "success@1")
    }
    result = {
        "benchmark": "CodeAtlas synthetic local benchmark v2",
        "dataset": {
            "chunks": len(CHUNKS),
            "retrieval_queries": len(CASES),
            "domains": len(DOMAIN_SPECS) + 2,
            "impact_cases": len(DOMAIN_SPECS) + 5,
            "note": "Expanded synthetic bilingual regression dataset; not a production benchmark.",
        },
        "retrieval": retrieval,
        "reranker_improvement_percentage_points": improvements,
        "error_analysis": await error_analysis(reranked_retriever),
        "citation_threshold_sweep": await citation_threshold_sweep(reranked_retriever),
        "workflow": await workflow_metrics(),
    }
    output = ROOT / "evaluation" / "results.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
