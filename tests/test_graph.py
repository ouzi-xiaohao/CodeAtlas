import pytest

from app.models import GraphEdge, GraphNode
from app.services.graph import KnowledgeGraph


@pytest.mark.asyncio
async def test_graph_traversal_is_bounded_to_two_hops():
    graph = KnowledgeGraph()
    nodes = [GraphNode(id=str(i), kind="Service", name=str(i)) for i in range(4)]
    edges = [
        GraphEdge(source="0", relation="CALLS", target="1"),
        GraphEdge(source="1", relation="CALLS", target="2"),
        GraphEdge(source="2", relation="CALLS", target="3"),
    ]
    await graph.upsert(nodes, edges)
    paths = await graph.traverse(["0"], max_hops=2)
    assert paths
    assert max(len(path.edges) for path in paths) == 2
    assert all(path.nodes[-1].id != "3" for path in paths)
