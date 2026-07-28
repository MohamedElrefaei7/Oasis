from dataclasses import dataclass

import tiktoken

CHUNK_SIZE = 500
OVERLAP = 50

ENCODING_NAME = "cl100k_base"

_ENC: tiktoken.Encoding | None = None


def encoding() -> tiktoken.Encoding:
    """The BPE encoding, loaded on first use and cached for the process.

    **Deliberately lazy, and the reason is the frozen binary.** This used to run
    at import time, which meant every entry point that so much as imported
    ``oasis.cli.app`` paid for it — ``oasis search``, ``oasis status``, and the
    server's startup, none of which chunk anything. Two concrete costs:

    1. ``get_encoding`` resolves through the ``tiktoken_ext`` namespace package
       and, on a cold cache, **downloads the BPE file**. At import time that put
       a network round trip on the startup path of commands that never needed
       it, and it is the wrong thing for an app that must work offline.
    2. In the PyInstaller bundle it failed *before the handshake was printed* —
       ``ValueError: Unknown encoding cl100k_base. Plugins found: []`` raised
       while importing the CLI module, so the server died with no handshake and
       no clue. Deferring it moves any such failure to the indexing path, where
       it is attributable and where the caller is already prepared to report a
       failure per file.

    (The bundle still needs ``--collect-submodules tiktoken_ext``; laziness
    changes *when* the plugin scan happens, not whether it must succeed.)
    """
    global _ENC
    if _ENC is None:
        _ENC = tiktoken.get_encoding(ENCODING_NAME)
    return _ENC


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

    enc = encoding()
    tokens = enc.encode(text)
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
            text=enc.decode(chunk_tokens),
            token_count=len(chunk_tokens),
        ))
        if end == len(tokens):
            break
        start += step

    return chunks
