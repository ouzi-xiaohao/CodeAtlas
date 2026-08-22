from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

from app.models import GraphEdge, GraphNode, KnowledgeChunk, SourceType


SUPPORTED_SUFFIXES = {".py", ".java", ".md", ".yaml", ".yml", ".json", ".sql"}
MAX_FILE_BYTES = 1_000_000


def stable_id(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode(), usedforsecurity=False).hexdigest()


class RepositoryParser:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def resolve_safe(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if self.root != candidate and self.root not in candidate.parents:
            raise ValueError(f"Path escapes repository root: {relative_path}")
        return candidate

    def discover(self, paths: list[str] | None = None) -> list[Path]:
        roots = [self.resolve_safe(path) for path in paths] if paths else [self.root]
        found: list[Path] = []
        for root in roots:
            if root.is_file():
                found.append(root)
                continue
            for path in root.rglob("*"):
                if (
                    path.is_file()
                    and path.suffix.lower() in SUPPORTED_SUFFIXES
                    and ".git" not in path.parts
                    and path.stat().st_size <= MAX_FILE_BYTES
                ):
                    found.append(path)
        return found

    def parse(
        self, path: Path, repository: str, team: str
    ) -> tuple[list[KnowledgeChunk], list[GraphNode], list[GraphEdge]]:
        relative = path.relative_to(self.root).as_posix()
        content = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() == ".py":
            return self._parse_python(relative, content, repository, team)
        if path.suffix.lower() == ".java":
            return self._parse_java(relative, content, repository, team)
        if path.suffix.lower() == ".md":
            chunks = self._parse_markdown(relative, content, repository, team)
        elif path.suffix.lower() == ".sql":
            chunks = self._parse_ddl(relative, content, repository, team)
        else:
            chunks = self._parse_structured(relative, content, repository, team)
        file_node = GraphNode(id=f"file:{relative}", kind="File", name=relative)
        return chunks, [file_node], []

    def _base_chunk(
        self,
        repository: str,
        team: str,
        source_type: SourceType,
        path: str,
        content: str,
        start: int,
        end: int,
        symbol: str | None = None,
    ) -> KnowledgeChunk:
        return KnowledgeChunk(
            id=stable_id(repository, path, symbol or "", str(start), content),
            repository=repository,
            team=team,
            source_type=source_type,
            path=path,
            symbol=symbol,
            content=content,
            line_start=start,
            line_end=end,
        )

    def _parse_python(self, path: str, content: str, repository: str, team: str):
        lines = content.splitlines()
        chunks: list[KnowledgeChunk] = []
        nodes = [GraphNode(id=f"file:{path}", kind="File", name=path)]
        edges: list[GraphEdge] = []
        module_name = path.removesuffix(".py").replace("/", ".")
        module_id = f"module:{module_name}"
        nodes.append(GraphNode(id=module_id, kind="Module", name=module_name))
        edges.append(GraphEdge(source=module_id, relation="DEFINED_IN", target=f"file:{path}"))
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return (
                [self._base_chunk(repository, team, SourceType.CODE, path, content, 1, len(lines))],
                nodes,
                edges,
            )

        definitions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if not definitions:
            chunks.append(
                self._base_chunk(repository, team, SourceType.CODE, path, content, 1, len(lines))
            )
        for node in definitions:
            end = getattr(node, "end_lineno", node.lineno)
            symbol = f"{module_name}.{node.name}"
            kind = "Class" if isinstance(node, ast.ClassDef) else "Function"
            node_id = f"{kind.lower()}:{symbol}"
            snippet = "\n".join(lines[node.lineno - 1 : end])
            chunks.append(
                self._base_chunk(
                    repository, team, SourceType.CODE, path, snippet, node.lineno, end, symbol
                )
            )
            nodes.append(GraphNode(id=node_id, kind=kind, name=symbol))
            edges.append(GraphEdge(source=node_id, relation="DEFINED_IN", target=module_id))
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    called = self._call_name(child.func)
                    if called:
                        target_id = f"symbol:{called}"
                        nodes.append(GraphNode(id=target_id, kind="Symbol", name=called))
                        edges.append(GraphEdge(source=node_id, relation="CALLS", target=target_id))
        return chunks, nodes, edges

    @staticmethod
    def _call_name(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = RepositoryParser._call_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return None

    def _parse_java(self, path: str, content: str, repository: str, team: str):
        try:
            import tree_sitter_java
            from tree_sitter import Language, Parser
        except ImportError:
            return self._parse_java_fallback(path, content, repository, team)

        source = content.encode("utf-8")
        tree = Parser(Language(tree_sitter_java.language())).parse(source)
        root = tree.root_node
        package_node = self._first_descendant(root, {"package_declaration"})
        package = ""
        if package_node is not None:
            package = (
                self._node_text(package_node, source).removeprefix("package").rstrip(";").strip()
            )
        module_name = package or path.rsplit("/", 1)[0].replace("/", ".")
        module_id = f"module:{module_name}"
        nodes = [
            GraphNode(id=f"file:{path}", kind="File", name=path),
            GraphNode(id=module_id, kind="Module", name=module_name),
        ]
        edges = [GraphEdge(source=module_id, relation="DEFINED_IN", target=f"file:{path}")]
        chunks: list[KnowledgeChunk] = []

        class_types = {"class_declaration", "interface_declaration", "enum_declaration"}
        for declaration in self._descendants(root, class_types):
            name_node = declaration.child_by_field_name("name")
            if name_node is None:
                continue
            class_name = self._node_text(name_node, source)
            qualified_class = f"{package}.{class_name}" if package else class_name
            class_id = f"class:{qualified_class}"
            class_text = self._node_text(declaration, source)
            chunks.append(
                self._base_chunk(
                    repository,
                    team,
                    SourceType.CODE,
                    path,
                    class_text,
                    declaration.start_point.row + 1,
                    declaration.end_point.row + 1,
                    qualified_class,
                )
            )
            nodes.append(GraphNode(id=class_id, kind="Class", name=qualified_class))
            edges.append(GraphEdge(source=class_id, relation="DEFINED_IN", target=module_id))

            for import_node in self._descendants(root, {"import_declaration"}):
                imported = (
                    self._node_text(import_node, source).removeprefix("import").rstrip(";").strip()
                )
                if imported:
                    import_id = f"module:{imported}"
                    nodes.append(GraphNode(id=import_id, kind="Module", name=imported))
                    edges.append(
                        GraphEdge(source=class_id, relation="DEPENDS_ON", target=import_id)
                    )

            base_path = self._spring_mapping(class_text[: class_text.find(class_name)])
            for method in self._descendants(
                declaration, {"method_declaration", "constructor_declaration"}
            ):
                method_name_node = method.child_by_field_name("name")
                if method_name_node is None:
                    continue
                method_name = self._node_text(method_name_node, source)
                symbol = f"{qualified_class}.{method_name}"
                method_id = f"function:{symbol}"
                method_text = self._node_text(method, source)
                chunks.append(
                    self._base_chunk(
                        repository,
                        team,
                        SourceType.CODE,
                        path,
                        method_text,
                        method.start_point.row + 1,
                        method.end_point.row + 1,
                        symbol,
                    )
                )
                nodes.append(GraphNode(id=method_id, kind="Function", name=symbol))
                edges.append(GraphEdge(source=method_id, relation="DEFINED_IN", target=class_id))

                endpoint = self._spring_mapping(method_text[: min(len(method_text), 500)])
                if endpoint:
                    http_method, route = endpoint
                    if isinstance(base_path, tuple):
                        route = f"{base_path[1].rstrip('/')}/{route.lstrip('/')}"
                    api_name = f"{http_method} {route or '/'}"
                    api_id = f"api:{api_name}"
                    nodes.append(GraphNode(id=api_id, kind="API", name=api_name))
                    edges.append(GraphEdge(source=method_id, relation="EXPOSES", target=api_id))

                for invocation in self._descendants(method, {"method_invocation"}):
                    called = self._java_invocation_name(invocation, source)
                    if called:
                        called_id = f"symbol:{called}"
                        nodes.append(GraphNode(id=called_id, kind="Symbol", name=called))
                        edges.append(
                            GraphEdge(source=method_id, relation="CALLS", target=called_id)
                        )

            for field in self._descendants(declaration, {"field_declaration"}):
                for variable in self._descendants(field, {"variable_declarator"}):
                    variable_name = variable.child_by_field_name("name")
                    if variable_name is None:
                        continue
                    field_name = self._node_text(variable_name, source)
                    field_id = f"field:{qualified_class}.{field_name}"
                    nodes.append(
                        GraphNode(id=field_id, kind="Field", name=f"{qualified_class}.{field_name}")
                    )
                    edges.append(GraphEdge(source=class_id, relation="HAS_FIELD", target=field_id))

        if not chunks:
            chunks.append(
                self._base_chunk(
                    repository,
                    team,
                    SourceType.CODE,
                    path,
                    content,
                    1,
                    max(1, len(content.splitlines())),
                )
            )
        return chunks, nodes, edges

    def _parse_java_fallback(self, path, content, repository, team):
        """Minimal fallback used only when the optional native parser is unavailable."""
        package_match = re.search(r"(?m)^\s*package\s+([\w.]+)\s*;", content)
        package = package_match.group(1) if package_match else ""
        class_match = re.search(r"\b(?:class|interface|enum)\s+(\w+)", content)
        symbol = (
            f"{package}.{class_match.group(1)}"
            if package and class_match
            else class_match.group(1)
            if class_match
            else None
        )
        chunk = self._base_chunk(
            repository,
            team,
            SourceType.CODE,
            path,
            content,
            1,
            max(1, len(content.splitlines())),
            symbol,
        )
        file_node = GraphNode(id=f"file:{path}", kind="File", name=path)
        return [chunk], [file_node], []

    @staticmethod
    def _node_text(node, source: bytes) -> str:
        return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    @staticmethod
    def _descendants(node, types: set[str]):
        stack = list(reversed(node.children))
        while stack:
            current = stack.pop()
            if current.type in types:
                yield current
            stack.extend(reversed(current.children))

    @staticmethod
    def _first_descendant(node, types: set[str]):
        return next(RepositoryParser._descendants(node, types), None)

    @staticmethod
    def _spring_mapping(text: str) -> tuple[str, str] | None:
        match = re.search(
            r"@(Get|Post|Put|Patch|Delete|Request)Mapping\s*(?:\(\s*(?:value\s*=\s*)?[\"']([^\"']*)[\"'])?",
            text,
        )
        if not match:
            return None
        method = match.group(1).upper()
        return ("ANY" if method == "REQUEST" else method, match.group(2) or "")

    @staticmethod
    def _java_invocation_name(node, source: bytes) -> str | None:
        name = node.child_by_field_name("name")
        if name is None:
            return None
        object_node = node.child_by_field_name("object")
        method_name = RepositoryParser._node_text(name, source)
        if object_node is None:
            return method_name
        owner = RepositoryParser._node_text(object_node, source)
        return f"{owner}.{method_name}"

    def _parse_markdown(self, path, content, repository, team):
        lines = content.splitlines()
        headings = [index for index, line in enumerate(lines) if line.startswith("#")]
        if not headings:
            headings = [0]
        chunks = []
        for offset, start in enumerate(headings):
            end = headings[offset + 1] if offset + 1 < len(headings) else len(lines)
            section = "\n".join(lines[start:end]).strip()
            if section:
                title = lines[start].lstrip("# ") if lines[start].startswith("#") else path
                chunks.append(
                    self._base_chunk(
                        repository,
                        team,
                        SourceType.DOCUMENT,
                        path,
                        section,
                        start + 1,
                        end,
                        title,
                    )
                )
        return chunks

    def _parse_ddl(self, path, content, repository, team):
        matches = list(re.finditer(r"(?im)^\s*create\s+table\s+([\w.\"`]+)", content))
        if not matches:
            return [
                self._base_chunk(
                    repository, team, SourceType.DDL, path, content, 1, len(content.splitlines())
                )
            ]
        chunks = []
        for index, match in enumerate(matches):
            end_offset = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            section = content[match.start() : end_offset]
            start_line = content[: match.start()].count("\n") + 1
            chunks.append(
                self._base_chunk(
                    repository,
                    team,
                    SourceType.DDL,
                    path,
                    section,
                    start_line,
                    start_line + section.count("\n"),
                    match.group(1).strip('"`'),
                )
            )
        return chunks

    def _parse_structured(self, path, content, repository, team):
        source_type = (
            SourceType.OPENAPI
            if re.search(r"(?m)^\s*openapi\s*:", content)
            else SourceType.DOCUMENT
        )
        lines = content.splitlines()
        chunks = []
        # Split OpenAPI documents by path declarations while preserving exact names.
        starts = [i for i, line in enumerate(lines) if re.match(r"^\s{0,4}/[^:]+:", line)]
        if not starts:
            starts = list(range(0, len(lines), 120)) or [0]
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else min(len(lines), start + 160)
            section = "\n".join(lines[start:end])
            symbol = lines[start].strip().rstrip(":") if lines else path
            chunks.append(
                self._base_chunk(
                    repository, team, source_type, path, section, start + 1, end, symbol
                )
            )
        return chunks
