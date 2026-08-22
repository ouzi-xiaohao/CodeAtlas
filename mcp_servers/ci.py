from __future__ import annotations

import re
import subprocess
import sys

from mcp.server.fastmcp import FastMCP

from app.config import get_settings
from app.security import verify_confirmation
from app.services.ingestion import RepositoryParser


mcp = FastMCP("CodeAtlas CI")
settings = get_settings()
parser = RepositoryParser(settings.repository_root)
SAFE_TARGET = re.compile(r"^[A-Za-z0-9_./:\\-]+$")


@mcp.tool()
def latest_test_report(path: str = "reports/pytest.txt") -> dict:
    """Read a previously generated CI test report; this tool never starts a build."""
    resolved = parser.resolve_safe(path)
    if not resolved.is_file():
        return {"status": "not_found", "path": path}
    return {
        "status": "available",
        "path": path,
        "content": resolved.read_text(errors="replace")[-50_000:],
    }


@mcp.tool()
def request_test_run(target: str = "tests") -> dict:
    """Describe the write operation that needs explicit user confirmation."""
    if not SAFE_TARGET.fullmatch(target):
        raise ValueError("Invalid test target")
    parser.resolve_safe(target)
    return {
        "status": "confirmation_required",
        "action": "ci.trigger",
        "resource": target,
        "message": "Ask the user to confirm, then obtain a short-lived token from /api/v1/confirmations.",
    }


@mcp.tool()
def trigger_test_run(target: str, confirmation_token: str) -> dict:
    """Run pytest only when a trusted UI supplied an explicit short-lived confirmation."""
    if not SAFE_TARGET.fullmatch(target):
        raise ValueError("Invalid test target")
    parser.resolve_safe(target)
    verify_confirmation(confirmation_token, "ci.trigger", target)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q"],
        cwd=parser.root,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "exit_code": result.returncode,
        "output": (result.stdout + result.stderr)[-50_000:],
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
