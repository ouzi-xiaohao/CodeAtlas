from __future__ import annotations

from collections import defaultdict, deque

from app.models import GraphEdge, GraphNode, GraphPath


class KnowledgeGraph:
    """In-memory graph with bounded path traversal; Neo4j is used in production mode."""

    def __init__(self) -> None:
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self._outgoing: dict[str, list[GraphEdge]] = defaultdict(list)
        self._incoming: dict[str, list[GraphEdge]] = defaultdict(list)

    async def upsert(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
        self.nodes.update({node.id: node for node in nodes})
        existing = {(edge.source, edge.relation, edge.target) for edge in self.edges}
        for edge in edges:
            key = (edge.source, edge.relation, edge.target)
            if key not in existing:
                self.edges.append(edge)
                self._outgoing[edge.source].append(edge)
                self._incoming[edge.target].append(edge)
                existing.add(key)

    async def find_entities(self, terms: list[str], limit: int = 20) -> list[GraphNode]:
        normalized = [term.lower() for term in terms if term]
        return [
            node
            for node in self.nodes.values()
            if any(term in node.name.lower() or term in node.id.lower() for term in normalized)
        ][:limit]

    async def traverse(self, start_ids: list[str], max_hops: int = 2) -> list[GraphPath]:
        paths: list[GraphPath] = []
        for start_id in start_ids:
            if start_id not in self.nodes:
                continue
            queue = deque([(start_id, [self.nodes[start_id]], [], {start_id})])
            while queue:
                current, nodes, edges, visited = queue.popleft()
                if edges:
                    paths.append(GraphPath(nodes=nodes, edges=edges))
                if len(edges) >= max_hops:
                    continue
                adjacent = self._outgoing[current] + self._incoming[current]
                for edge in adjacent:
                    next_id = edge.target if edge.source == current else edge.source
                    if next_id in visited or next_id not in self.nodes:
                        continue
                    queue.append(
                        (
                            next_id,
                            [*nodes, self.nodes[next_id]],
                            [*edges, edge],
                            {*visited, next_id},
                        )
                    )
        return paths[:50]

    async def owners_for(self, entity_ids: list[str]) -> list[GraphNode]:
        owners: list[GraphNode] = []
        for entity_id in entity_ids:
            for edge in self._incoming[entity_id]:
                if edge.relation == "OWNS" and edge.source in self.nodes:
                    owners.append(self.nodes[edge.source])
        return owners
