from __future__ import annotations

import re
import subprocess
from pathlib import Path


DIFF_FILE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
SYMBOL_PATTERNS = [
    re.compile(r"^[+-]\s*(?:async\s+)?def\s+([A-Za-z_]\w*)", re.MULTILINE),
    re.compile(r"^[+-]\s*class\s+([A-Za-z_]\w*)", re.MULTILINE),
    re.compile(r"[+-].*?\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b"),
    re.compile(r"[+-].*?\b(?:column|field)\s+['\"]?([A-Za-z_]\w*)", re.IGNORECASE),
    re.compile(
        r"^[+-]\s*(?:(?:public|protected|private|abstract|final|static)\s+)*"
        r"(?:class|interface|enum|record)\s+([A-Za-z_]\w*)",
        re.MULTILINE,
    ),
    re.compile(
        r"^[+-]\s*(?:(?:public|protected|private|static|final|synchronized)\s+)+"
        r"(?:[A-Za-z_][\w<>?,.\[\]]*\s+)+([A-Za-z_]\w*)\s*\(",
        re.MULTILINE,
    ),
    re.compile(
        r"^[+-]\s*@(?:Get|Post|Put|Patch|Delete|Request)Mapping\s*"
        r"\(\s*[\"']([^\"']+)",
        re.MULTILINE,
    ),
]


class ChangeReader:
    def __init__(self, repository_root: Path) -> None:
        self.root = repository_root.resolve()

    def read(self, input_type: str, value: str) -> str:
        if input_type in {"diff", "description"}:
            return value
        if input_type == "commit":
            return self._git("show", "--format=fuller", "--no-ext-diff", value)
        if input_type == "pull_request":
            # Accept a locally available merge ref or commit range; remote access belongs in Repository MCP.
            return self._git("diff", "--no-ext-diff", value)
        raise ValueError(f"Unsupported change input: {input_type}")

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.stdout


def extract_change_entities(change: str) -> tuple[list[str], list[str]]:
    files = list(dict.fromkeys(DIFF_FILE.findall(change)))
    symbols: list[str] = []
    for pattern in SYMBOL_PATTERNS:
        for match in pattern.findall(change):
            symbol = ".".join(match) if isinstance(match, tuple) else match
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    # Description-only requests still benefit from identifiers and dotted fields.
    if not symbols:
        symbols = list(
            dict.fromkeys(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_]\w*)*\b", change))
        )[:30]
    return files, symbols[:50]
