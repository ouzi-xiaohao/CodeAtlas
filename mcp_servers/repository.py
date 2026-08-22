from __future__ import annotations

import subprocess

from mcp.server.fastmcp import FastMCP

from app.config import get_settings
from app.services.ingestion import RepositoryParser
from app.services.repository import GitHubRepositoryClient


mcp = FastMCP("CodeAtlas Repository")
settings = get_settings()
parser = RepositoryParser(settings.repository_root)
github = GitHubRepositoryClient(settings.github_api_url, settings.github_token)


@mcp.tool()
def read_file(path: str, start_line: int = 1, end_line: int = 300) -> dict:
    """Read an authorized repository file with a bounded line range."""
    resolved = parser.resolve_safe(path)
    if not resolved.is_file():
        raise ValueError("File does not exist")
    if resolved.stat().st_size > 1_000_000:
        raise ValueError("File is larger than the MCP read limit")
    lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(start_line, 1)
    end = min(max(end_line, start), start + 500, len(lines))
    return {
        "path": path,
        "start_line": start,
        "end_line": end,
        "content": "\n".join(lines[start - 1 : end]),
    }


@mcp.tool()
def commit_details(commit: str) -> str:
    """Return commit metadata and diff from the configured local repository."""
    if not commit or commit.startswith("-"):
        raise ValueError("Invalid commit reference")
    result = subprocess.run(
        ["git", "show", "--no-ext-diff", "--format=fuller", commit],
        cwd=parser.root,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result.stdout[:200_000]


@mcp.tool()
def compare_refs(base: str, head: str) -> str:
    """Read a PR-like diff between two locally available Git refs."""
    for ref in (base, head):
        if not ref or ref.startswith("-"):
            raise ValueError("Invalid Git ref")
    result = subprocess.run(
        ["git", "diff", "--no-ext-diff", base, head],
        cwd=parser.root,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result.stdout[:200_000]


@mcp.tool()
async def github_pull_request(url: str) -> dict:
    """Read metadata, changed files and normalized diff for a GitHub pull request."""
    snapshot = await github.pull_request(url)
    return snapshot.model_dump()


if __name__ == "__main__":
    mcp.run(transport="stdio")
