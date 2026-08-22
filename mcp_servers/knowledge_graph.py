from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.config import get_settings
from app.container import build_container
from app.services.seed import seed_demo


mcp = FastMCP("CodeAtlas Knowledge Graph")
container = build_container(get_settings())
seeded = False


async def ensure_seeded() -> None:
    global seeded
    if not seeded and not container.settings.enable_external_services:
        await seed_demo(container.retriever, container.graph)
        seeded = True


@mcp.tool()
async def find_entities(terms: list[str], limit: int = 20) -> list[dict]:
    """Find graph entities matching code symbols, APIs, services, tables, or issues."""
    await ensure_seeded()
    nodes = await container.graph.find_entities(terms[:30], min(limit, 50))
    return [node.model_dump() for node in nodes]


@mcp.tool()
async def dependency_paths(entity_ids: list[str], max_hops: int = 2) -> list[dict]:
    """Return one-to-two-hop dependency paths from known entity IDs."""
    await ensure_seeded()
    paths = await container.graph.traverse(entity_ids[:20], min(max(max_hops, 1), 2))
    return [path.model_dump() for path in paths]


@mcp.tool()
async def module_owners(entity_ids: list[str]) -> list[dict]:
    """Return owners connected to modules through an OWNS relationship."""
    await ensure_seeded()
    owners = await container.graph.owners_for(entity_ids[:20])
    return [owner.model_dump() for owner in owners]


if __name__ == "__main__":
    mcp.run(transport="stdio")
