from llama_index.core.node_parser import SentenceSplitter

from app.models import KnowledgeChunk


splitter = SentenceSplitter(chunk_size=384, chunk_overlap=32)


def compress_chunks(chunks: list[KnowledgeChunk], max_chars: int = 8_000) -> list[KnowledgeChunk]:
    """Use LlamaIndex sentence-aware splitting to keep evidence inside the context budget."""
    result: list[KnowledgeChunk] = []
    remaining = max_chars
    for chunk in chunks:
        if remaining <= 0:
            break
        parts = splitter.split_text(chunk.content)
        content = (parts[0] if parts else chunk.content)[:remaining]
        result.append(chunk.model_copy(update={"content": content}))
        remaining -= len(content)
    return result
