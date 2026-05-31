from dataclasses import dataclass

import tiktoken

CHUNK_SIZE = 500
OVERLAP = 50

# Loaded once at import time; tiktoken caches the encoding file on disk after
# the first download so subsequent imports are fast.
_ENC: tiktoken.Encoding = tiktoken.get_encoding("cl100k_base")


@dataclass
class Chunk:
    chunk_index: int
    text: str
    token_count: int


def chunk_document(
    text: str,
    *,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = OVERLAP,
) -> list[Chunk]:
    """Split *text* into overlapping token-window chunks.

    Returns an empty list for empty or whitespace-only text.
    Raises ValueError if overlap >= chunk_size or chunk_size <= 0.
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be positive, got {chunk_size}")
    if overlap >= chunk_size:
        raise ValueError(f"overlap ({overlap}) must be less than chunk_size ({chunk_size})")

    if not text or not text.strip():
        return []

    tokens = _ENC.encode(text)
    if not tokens:
        return []

    step = chunk_size - overlap
    chunks: list[Chunk] = []
    start = 0

    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(Chunk(
            chunk_index=len(chunks),
            text=_ENC.decode(chunk_tokens),
            token_count=len(chunk_tokens),
        ))
        if end == len(tokens):
            break
        start += step

    return chunks
