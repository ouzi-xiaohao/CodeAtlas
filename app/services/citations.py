from app.models import Citation, KnowledgeChunk


def citation_from_chunk(chunk: KnowledgeChunk) -> Citation:
    suffix = f"#L{chunk.line_start}-L{chunk.line_end}" if chunk.line_start else ""
    uri = chunk.metadata.get("uri") or f"repo://{chunk.repository}/{chunk.path}{suffix}"
    label = chunk.symbol or chunk.metadata.get("title") or chunk.path
    excerpt = " ".join(chunk.content.strip().split())[:240]
    return Citation(
        source_type=chunk.source_type,
        label=label,
        uri=uri,
        repository=chunk.repository,
        line_start=chunk.line_start,
        line_end=chunk.line_end,
        excerpt=excerpt,
    )


def unique_citations(chunks: list[KnowledgeChunk]) -> list[Citation]:
    seen: set[tuple[str, int]] = set()
    result: list[Citation] = []
    for chunk in chunks:
        key = (chunk.path, chunk.line_start)
        if key not in seen:
            result.append(citation_from_chunk(chunk))
            seen.add(key)
    return result
