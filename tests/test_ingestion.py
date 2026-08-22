from pathlib import Path

import pytest

from app.services.ingestion import RepositoryParser


def test_python_ast_chunking(tmp_path: Path):
    source = tmp_path / "service.py"
    source.write_text(
        "class OrderService:\n    def create(self):\n        return save_order()\n",
        encoding="utf-8",
    )
    chunks, nodes, edges = RepositoryParser(tmp_path).parse(source, "shop", "orders")
    assert {chunk.symbol for chunk in chunks} >= {
        "service.OrderService",
        "service.create",
    }
    assert any(edge.relation == "CALLS" for edge in edges)
    assert any(node.kind == "Class" for node in nodes)


def test_path_escape_is_rejected(tmp_path: Path):
    parser = RepositoryParser(tmp_path)
    with pytest.raises(ValueError):
        parser.resolve_safe("../outside.py")


def test_java_tree_sitter_extracts_spring_api_calls_and_fields(tmp_path: Path):
    source = tmp_path / "OrderController.java"
    source.write_text(
        """package com.shop.order;
import com.shop.payment.PaymentClient;

@RestController
@RequestMapping("/orders")
class OrderController {
    private String status;

    @PostMapping("/{id}/pay")
    public void pay() {
        paymentClient.reserve();
    }
}
""",
        encoding="utf-8",
    )
    chunks, nodes, edges = RepositoryParser(tmp_path).parse(source, "shop", "orders")

    assert {chunk.symbol for chunk in chunks} >= {
        "com.shop.order.OrderController",
        "com.shop.order.OrderController.pay",
    }
    assert any(node.kind == "API" and node.name == "POST /orders/{id}/pay" for node in nodes)
    assert any(node.kind == "Field" and node.name.endswith(".status") for node in nodes)
    assert any(
        edge.relation == "CALLS" and edge.target.endswith("paymentClient.reserve") for edge in edges
    )
