# Oasis

A natural-language file search tool with hybrid keyword + semantic retrieval.

## Stack
- Python 3.14, managed with `uv`
- SQLite + FTS5 for keyword index and metadata
- LanceDB for vector store
- sentence-transformers (all-MiniLM-L6-v2 to start) for embeddings
- Anthropic Claude API + Ollama (local) for natural language query parsing
- Typer + Rich for CLI
- FastAPI + HTMX for web UI
- pytest for tests, ruff for lint/format

## Architecture
- `src/oasis/extractors/` — one module per file format, uniform `Extractor` interface
- `src/oasis/index/` — indexing pipeline, change detection, both index backends
- `src/oasis/query/` — NL query parser, hybrid retrieval, reranking, score fusion
- `src/oasis/cli/` — Typer commands
- `tests/` — pytest, fixture files under `tests/fixtures/`

## Conventions
- Use Python 3.14+ syntax: built-in generics (`list[str]`, not `List[str]`), `X | None` not `Optional[X]`.
- Type hints everywhere. Use Pydantic for any data passed across module boundaries.
- Prefer pure functions; isolate side effects (filesystem, API calls) in dedicated modules.
- No print statements in library code — use Python's `logging` module.
- Tests for any new extractor or query parser change.

## Don't
- Don't add async until there's a measured need.
- Don't introduce new dependencies without checking if existing ones cover it.
- Don't write to the database directly from CLI handlers — go through the index layer.