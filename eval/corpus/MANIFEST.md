# Oasis Eval Corpus — Manifest

~300 files across 7 formats for evaluating Oasis retrieval (precision/recall/MRR/NDCG).
Built July 2026. All contents are publicly redistributable (public domain, permissive-licensed,
or synthetic files generated for this corpus).

## Layout and counts

- `pdfs/` — 60 files (15.5 MB)
- `docx/` — 41 files (1.5 MB)
- `pptx/` — 30 files (1.0 MB)
- `xlsx/` — 30 files (0.2 MB)
- `csv/` — 40 files (5.6 MB)
- `md/` — 60 files (2.1 MB)
- `txt/` — 40 files (12.2 MB)

**Total: 301 files, 38 MB**

## Sources

| Prefix / dir | Source | License |
|---|---|---|
| `pdfs/paper-*` | papers-we-love GitHub repo (36 real CS papers across IR, ML, databases, distributed systems, crypto, OS, data structures) | Publicly hosted papers |
| `pdfs/gov-*`, `manual-*`, `memo-*`, `note-*`, `guide-*`, `history-*`, `marine-*`, `booklet-*`, `weird-*`, `scanned-*` | Generated for this corpus (reportlab) | CC0 / synthetic |
| `docx/sample-*`, `pptx/sample-*` | python-docx / python-pptx test suites (MIT) — real files with unusual internal structure | MIT |
| `docx/*`, `pptx/*`, `xlsx/*` (rest) | Generated for this corpus (python-docx, python-pptx, openpyxl) | CC0 / synthetic |
| `csv/seaborn-*` | mwaskom/seaborn-data | Public datasets |
| `csv/vega-*` | vega/vega-datasets | BSD-3 |
| `csv/538-*` | fivethirtyeight/data | CC-BY-4.0 |
| `csv/*` (rest) | datahub.io `datasets` org on GitHub | PDDL / ODC-BY |
| `txt/gutenberg-*` | Project Gutenberg selection via NLTK data (18 books) | Public domain |
| `txt/inaugural-*`, `txt/sotu-*` | US presidential addresses via NLTK data | Public domain (US gov) |
| `md/*` | READMEs/docs fetched from public GitHub repos + 5 synthetic note files | Per-repo OSS licenses |

## Deliberate corpus properties

- **Topical diversity (20+ topics):** finance, biology/marine science, public health, history, cooking,
  sports, music, philosophy, astronomy/space, transit, water policy, travel, photography, chess,
  neuroscience, economics, ML/IR/databases, legal/contracts, HR policy, literature, US politics.
- **Near-duplicates (reranker discrimination):** `contract-consulting-services-v1` vs `-v2-final` (docx);
  `expenses-team-offsite-draft` vs `-final` (xlsx); `gov-transit-authority-annual-report-2023` vs `-2024` (pdf);
  `town-hall-q1-results` vs `-q3-results` (pptx); multiple same-topic paper pairs in `pdfs/paper-*`.
- **Length variance (chunker stress):** one-line notes (`txt/edge-tiny-note.txt`) up to full books
  (`gutenberg-bible-kjv.txt` ~4 MB, `melville-moby_dick`), a ~17-page/69k-char technical report
  (`gov-regional-infrastructure-assessment-long.pdf`), and multi-hundred-page real papers.
- **Quality variance:** clean generated docs; real PDFs with imperfect internal objects; unusual-layout
  flyer with rotated text (`weird-layout-conference-flyer.pdf`); real library test files whose text lives
  in tables/shapes rather than paragraphs (`docx/sample-block-with-table.docx`, `pptx/sample-autoshapes.pptx`).
- **Edge cases (each exercises a specific Oasis code path):**
  - `pdfs/scanned-inspection-report.pdf` — image-only, no text layer → scanned-PDF `None` path
  - `txt/edge-latin1-menu.txt` — non-UTF8 → TextExtractor failure path
  - `xlsx/model-revenue-forecast.xlsx` — formula cells (never opened by Excel) → `data_only=True` yields None values
  - `docx/edge-nearly-empty.docx`, `xlsx/edge-empty-workbook.xlsx`, `pptx/edge-single-slide.pptx` — minimal content
  - `xlsx/edge-unicode-cities.xlsx`, CJK/diacritics content — unicode handling
  - `csv/*` includes latin-1-safe and trimmed large files
- **Metadata diversity:** generated docx/pptx/xlsx carry varied `author`/`title` core properties
  (10 distinct authors incl. departments); some files deliberately have none. File mtimes are spread
  deterministically across 2019–2026 (hash of filename) so date-range filters have real signal.
  Meeting-notes docx series spans 2023–2025 with dates in filenames.

## Notes for query-set construction (next step)
- Date-filter queries: `meeting-notes-product-sync-*` series; mtime spread supports `after:`/`before:`.
- File-type queries: every format has topic overlap with at least one other format (e.g. sourdough
  appears as pdf guide, md notes, pptx talk, xlsx costing sheet).
- Folder-filter queries: format subdirectories double as folder targets.
