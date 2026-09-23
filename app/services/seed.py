from app.models import GraphEdge, GraphNode, KnowledgeChunk, SourceType
from app.services.graph import KnowledgeGraph
from app.services.retrieval import HybridRetriever


async def seed_demo(retriever: HybridRetriever, graph: KnowledgeGraph) -> None:
    chunks = [
        KnowledgeChunk(
            id="nexus-commerce-platform-order-service",
            repository="nexus-commerce-platform",
            team="default",
            source_type=SourceType.CODE,
            path="services/order/service.py",
            symbol="OrderService.create_order",
            content=(
                "订单创建 create_order 会校验用户，写入 orders.status，然后调用 "
                "PaymentClient.reserve 支付预占与 InventoryClient.lock 库存锁定；"
                "任一调用失败都可能导致订单创建失败。"
            ),
            line_start=18,
            line_end=46,
        ),
        KnowledgeChunk(
            id="nexus-commerce-platform-user-ddl",
            repository="nexus-commerce-platform",
            team="default",
            source_type=SourceType.DDL,
            path="database/schema.sql",
            symbol="users",
            content="CREATE TABLE users (id bigint primary key, phone varchar(32), status varchar(16));",
            line_start=3,
            line_end=7,
        ),
        KnowledgeChunk(
            id="nexus-commerce-platform-inc-142",
            repository="nexus-commerce-platform",
            team="default",
            source_type=SourceType.ISSUE,
            path="issues/INC-142.md",
            symbol="INC-142",
            content=(
                "历史异常：支付状态枚举变更后订单创建失败。修复方式是更新支付 webhook "
                "状态映射并增加契约测试。"
            ),
            line_start=1,
            line_end=12,
            metadata={"uri": "issue://nexus-commerce-platform/INC-142"},
        ),
        KnowledgeChunk(
            id="nexus-commerce-platform-payment-contract",
            repository="nexus-commerce-platform",
            team="default",
            source_type=SourceType.TEST,
            path="tests/contract/test_order_payment.py",
            symbol="test_order_payment_status_contract",
            content="契约测试覆盖订单状态映射和支付预占失败场景。",
            line_start=10,
            line_end=35,
        ),
    ]
    nodes = [
        GraphNode(id="field:orders.status", kind="Field", name="orders.status"),
        GraphNode(id="service:order", kind="Service", name="order-service"),
        GraphNode(id="api:POST/orders", kind="API", name="POST /orders"),
        GraphNode(id="service:payment", kind="Service", name="payment-service"),
        GraphNode(id="table:orders", kind="Table", name="orders"),
        GraphNode(id="test:order-payment", kind="Test", name="test_order_payment_status_contract"),
        GraphNode(id="issue:INC-142", kind="Issue", name="INC-142"),
    ]
    edges = [
        GraphEdge(source="service:order", relation="EXPOSES", target="api:POST/orders"),
        GraphEdge(source="service:order", relation="CALLS", target="service:payment"),
        GraphEdge(source="service:order", relation="WRITES", target="table:orders"),
        GraphEdge(source="table:orders", relation="HAS_FIELD", target="field:orders.status"),
        GraphEdge(source="test:order-payment", relation="COVERS", target="api:POST/orders"),
        GraphEdge(source="issue:INC-142", relation="RELATED_TO", target="service:order"),
    ]
    await retriever.upsert(chunks)
    await graph.upsert(nodes, edges)
