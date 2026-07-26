# Oasis

**Does finding the right file feel like looking for an oasis in the Sahara desert? Look no further, and find the file you forgot you made.**

Oasis is a natural-language file search system that runs entirely on your machine. Ask it the way you'd ask a person, like — *"that tax PDF from last spring"*, *"powerpoints I made about ML last month"*, or *"the spreadsheet with Q3 revenue"* — and it finds the right file for you.

Oasis works by parsing your query into structured filters, running hybrid keyword and semantic retrieval, reranking the candidates with a cross-encoder, and returning ranked results, all locally, and in under a second.

---

## Why Oasis?

Most built-in OS search indexes shallowly and ranks poorly. `grep` can't help when you only half-remember what was actually in the file. Spotlight search on mac forgets the content from formats it doesn't natively understand. Where Oasis differs, is that it indexes the *contents* of PDFs, Word docs, slide decks, spreadsheets, CSVs, and Markdown files — then lets you query them the way you actually remember them, not the way the machine does.

---

## Quick startup guide

```bash
git clone https://github.com/MohamedElrefaei7/Oasis.git
cd oasis
pixi install

# Index a folder (incremental — re-runs skip unchanged files)
pixi run oasis index ~/Documents

# Search in plain English (examples)
pixi run oasis search "tax pdf from last spring"
pixi run oasis search "powerpoints about machine learning last month"
pixi run oasis search "spreadsheet with quarterly revenue"

# Directly open result #2 from the last search
pixi run oasis open 2
```

---

## How it actually works

```
  "powerpoints about ML from last month"
                  │
                  ▼
      ┌───────────────────────┐
      │  NL parser (Ollama)   │   file_types = [".pptx"]
      │  → ParsedQuery        │   date_range  = last month
      └───────────────────────┘   semantic    = "machine learning"
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
  ┌───────────┐       ┌───────────┐
  │  SQLite   │       │  LanceDB  │
  │  + FTS5   │       │  vectors  │
  │  BM25     │       │  cosine   │
  │  + filter │       │  + filter │
  └───────────┘       └───────────┘
        │                   │
        └────────┬──────────┘
                 ▼
         ┌──────────────┐
         │   RRF (k=60) │
         └──────────────┘
                 ▼
         ┌──────────────┐
         │ Cross-encoder│
         │   reranker   │
         └──────────────┘
                 ▼
            top results
```

Every component runs completely locally — embeddings, LLM, and storage. No telemetry, no cloud sync, no API keys required (which also means it's free to use)!

---

## Features

- **Three search modes** — keyword (BM25 with porter stemming), semantic (dense vectors), or hybrid (RRF fusion + cross-encoder rerank). Pick with `--mode`.
- **Natural language queries** — a local LLM (Ollama, `llama3.2:3b`) extracts file types, date ranges, folder hints, and exact-match keywords from your query into a typed schema. Auto-starts Ollama if installed; falls back gracefully if not.
- **Incremental indexing** — `(size, mtime)` hash skips files that haven't changed since the last run.
- **Format coverage** — `.txt`, `.md`, `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.csv`. Partial-success extraction means one corrupted page doesn't lose the whole document (Which is quite often the case).
- **Local by default** — every dependency runs on your machine. The only network call is to `localhost:11434` for Ollama.

---

## Commands

| Command | What it does |
|---|---|
| `oasis index <path>` | Walks a directory and indexes every supported file. `--force` rebuilds; `--verbose` prints each and every file. |
| `oasis search <query>` | Searches the indexed files. `--mode keyword\|semantic\|hybrid`, `--limit N`, `--raw` to bypass NL parsing. |
| `oasis open <n>` | Opens result `#n` from the last search in the system default app. |
| `oasis status` | Document count, DB size, last-indexed timestamp. |
| `oasis reset` | Deletes the index (gives a confirmation prompt; append `--yes` to skip). |

---

## Stack

| Layer | Tool |
|---|---|
| Language | Python 3.14+, managed with `pixi` (conda-forge + PyPI under one lock) |
| Keyword index | SQLite + FTS5 (BM25, porter stemmer) |
| Vector index | LanceDB (cosine, embedded) |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (384-dim) |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| NL query parsing | Ollama + `instructor` (JSON mode) |
| CLI | Typer + Rich |
| Inference device | CPU by default (torch from conda-forge, OpenBLAS); override with `OASIS_DEVICE` |
| Tests | pytest — ~940 tests covering extractors, index, retrieval, parser, CLI, HTTP API |

---

## Design decisions

A few of the trade-offs that shaped the project:

- **Reciprocal Rank Fusion instead of score normalization.** BM25 and cosine distance are on different scales; rank-based fusion sidesteps any needed calibration entirely, with the constant k=60 being the standard for ranking.
- **Cross-encoder rerank only on the top ~20.** Cross-encoders are pretty accurate but slow, so reranking a small candidate pool gets a large majority of the quality benefit at a fraction of the latency.
- **Local LLM for NL parsing, not cloud.** Privacy is a feature, as people are working with their own files. Ollama runs `llama3.2:3b` fast enough for Oasis on a laptop, and structured output via `instructor` makes it reliable.
- **Schema-first query parsing.** The LLM fills in a typed `ParsedQuery` (Pydantic), not free-form JSON. Validation therefore catches bad outputs before they reach the retrieval layer.
- **Partial-success extraction.** A bad page in a PDF doesn't kill the whole document (as formatting in PDFs is wacky sometimes) — failures are caught per-page, logged, and tracked in `extraction_errors` on the extracted document.

---

## Possible Future Roadmap

- **Local HTTP API** — `oasis serve`, a loopback-only FastAPI server that the native app spawns as a child process.
- **Native macOS app** — SwiftUI client over that API; global hotkey, menu bar, background indexing via FSEvents, Core ML / MLX embeddings for Apple Silicon.
- **More formats** — email (`.mbox`, `.msg`), images via CLIP, code via tree-sitter.
- **OCR fallback** for scanned PDFs.
- **Per-directory `.gitignore` loading** (currently root-level only).

---

## Current Status

Active development. Extraction, keyword index, semantic layer, natural-language query parsing, and the evaluation harness are complete and tested. I'm into the polishing phase (the local HTTP API, the native macOS client, and packaging), as an overwhelming majority of the actual system is completed.