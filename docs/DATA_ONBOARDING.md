# Data Onboarding — where your real project data lives

## The rule

**Real project data never enters this repository.**

Your Mudshark masterfiles carry rates and cost outcomes from completed jobs.
Your drawing sets are client tender documents. Both are commercially sensitive,
and everything committed here is pushed to GitHub. Beyond confidentiality, the
cloud agent sandbox is ephemeral and small (~20 GB, wiped between sessions), so
uploading gigabytes there is both unsafe and futile.

Data lives on **your machine**. Parsers are developed **in this repo**. The two
meet through a *structure probe*, never through the data itself.

```
  YOUR MACHINE (private)                    THIS REPO (public)
  ┌────────────────────────┐                ┌────────────────────────┐
  │ ~/qs-data/             │                │ backend/qsagent/       │
  │   mudshark/*.zip       │──probe──►      │   ingest/  (parsers)   │
  │   drawings/*.zip       │  structure     │ tests/     (synthetic  │
  │   flupper.db           │  only          │             fixtures)  │
  └────────────────────────┘                └────────────────────────┘
            ▲                                          │
            └────────── parsers run locally ◄──────────┘
```

## Step 1 — Lay out a local data directory (outside the repo)

```
~/qs-data/                     # NOT inside the Flupper checkout
├── mudshark/
│   ├── ProjectA_Masterfile.zip
│   └── ProjectB_Masterfile.zip
├── drawings/
│   └── ProjectA_Drawings.zip
└── flupper.db                 # the SQLite store
```

Keeping it outside the checkout means no `.gitignore` mistake can ever commit it.

## Step 2 — Probe the structure

```bash
python tools/probe_export.py ~/qs-data/mudshark/ --out probe_report.json
python tools/probe_export.py ~/qs-data/drawings/ --out probe_drawings.json
```

Optional, improves the report:

```bash
pip install openpyxl pymupdf   # xlsx sheet detail + PDF title-block extraction
```

The probe emits **structure only** — extensions, folder depth, sheet names,
column headers, delimiters, preamble row counts, PDF page counts, whether a
drawing has a text layer or is scanned, and `.bbx` container magic bytes.
**No cell values, quantities, rates or client names.** Use `--redact-headers`
if even column names are sensitive.

Review `probe_report.json`, then paste it into the chat. That is enough to write
parsers against your real format instead of a guessed one.

## Step 3 — Key things the probe answers

| Question | Why it decides the parser design |
|---|---|
| Is `.bbx` zip-based, SQLite, XML or opaque? | Decides whether we read the model directly or rely on the Excel/CSV export |
| How many preamble rows before the header? | Mudshark writes title rows that break naive `read_csv` |
| One sheet per layer, or one sheet with a Layer column? | Determines the WBS mapping strategy |
| Do drawing PDFs have a text layer? | Text layer → PyMuPDF extraction. Scanned → OCR required (much slower) |
| Are drawings one multi-page PDF or one file per sheet? | Changes how sheets and revisions are enumerated |

## Step 4 — Continue in VS Code with Cline

This is the right move for the ingestion phase, because Cline runs against your
actual files.

```bash
git clone https://github.com/greed0802/Flupper.git
cd Flupper
git checkout arena/01a076e7-flupper
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/python -m pytest backend    # confirm the 72 Phase 1 tests pass
```

Point Cline at `docs/ROADMAP.md` for context, then work Phase 2. Keep the
existing discipline in place:

1. **Never** commit anything from `~/qs-data/`.
2. Test fixtures must be **synthetic** — hand-write a small CSV that mimics the
   structure the probe revealed. Never copy a real export into `tests/`.
3. Every parser writes through `QSStore`, so ingestion is journalled and every
   extracted figure gets an `EvidenceRef` with the file's SHA-256.
4. Run `pytest` before each commit.

## Step 5 — Ingesting for real (once parsers exist)

```bash
.venv/bin/python -m qsagent.ingest.cli \
    --db ~/qs-data/flupper.db \
    --project "Project A" \
    --mudshark ~/qs-data/mudshark/ProjectA_Masterfile.zip \
    --drawings ~/qs-data/drawings/ProjectA_Drawings.zip
```

Scale is a non-issue locally: SQLite handles millions of evidence nodes, and
drawing PDFs are referenced **by hash and path**, not copied into the database.
Your gigabytes stay on disk; the store holds pointers plus extracted structure.

## On sharing samples

If a parser misbehaves on a specific file and the probe isn't enough, sanitise
rather than sharing the original — keep the structure, replace the numbers:

- Keep: column headers, sheet names, row counts, layer naming conventions.
- Replace: all quantities, rates, client and site names, drawing titles.

A single sanitised 20-row CSV is usually enough to fix a parser bug, and it can
safely live in `tests/fixtures/`.
