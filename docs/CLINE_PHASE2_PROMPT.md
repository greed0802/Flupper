# Cline Prompt — Phase 2 Ingestion

Copy everything between the rulers into Cline, in the `Flupper` repo, on branch
`arena/01a076e7-flupper`.

Run the **Task 0 safety check** first and read its output before letting Cline
write anything.

---

## Task 0 — Safety check (run this alone, first)

```
Run this and show me the output. Do not modify any files yet.

  git status --porcelain | head -30
  git check-ignore -v "masterfile/2026/August/Aldi_Dandenong_Results_31082026.xls" "project/aldi-dandenong-north.zip"
  git ls-files | grep -iE "\.(xls|xlsx|bbx|zip|pdf)$" | head

The first command must NOT list anything under masterfile/ or project/.
The second must show both paths matching a .gitignore rule.
The third must print nothing at all.

If any of those three conditions fail, STOP and tell me — we have real client
data staged for commit and must fix .gitignore before doing anything else.
```

---

## Main prompt — Phase 2 ingestion

```
You are working in the Flupper repository on branch arena/01a076e7-flupper.
Read docs/ROADMAP.md and docs/DATA_ONBOARDING.md before writing any code.

## Absolute rules — violating these is a critical failure

1. NEVER `git add` anything under masterfile/ or project/. They contain real
   client tender data: rates, cost outcomes and confidential drawings. They are
   gitignored — keep it that way, and never use `git add -f` on them.
2. NEVER copy real data into backend/tests/. All test fixtures must be
   SYNTHETIC — hand-written files that mimic the structure you observe, with
   invented numbers and no client names.
3. NEVER paste cell values, quantities, rates or client names into your chat
   responses to me. Describe structure (sheet names, column headers, row
   counts) only. I already know what the numbers are.
4. Do not modify anything in backend/qsagent/contracts/, storage/, tools/ or
   checkmate/ without telling me why first. Phase 1 is complete, 72 tests pass,
   and it is the audited foundation everything else depends on.
5. Run `python -m pytest backend` before every commit. Never commit red tests.

## My actual data layout (Windows workstation)

D:\Flicker\Flupper\Flupper\
├── masterfile\<year>\<month>\<Project Name>\      Mudshark exports
└── project\<project-slug>.zip                     Drawing sets (~1.1 GB total)

Two shapes exist in masterfile/ and you must handle BOTH:

  A) Loose files in the project folder (e.g. 2026/August/Aldi Dandenong/):
       Aldi_Dandenong_Depth_Categories_31082026.xls     (~15 KB)
       Aldi_Dandenong_Measurements_31082026.xls         (~102 KB)
       Aldi_Dandenong_Results_31082026.xls              (~312 KB)
       Aldi_Dandenong_Trench_Summary_31082026.xlsx      (~30 KB)
       Aldi_Dandenong_Masterfile_31082026.bbx           (~35 MB)
       Aldi_Dandenong_Project_28082026(1..3).bbx        older snapshots
       Aldi_Dandenong_Masterfile_31082026.zip           the above, zipped

  B) A single zip per project (e.g. 2026/September/02. St Padre Pio .../):
       "<Project> - Stage 2_Masterfile_04092026.zip" containing:
         <Project>_Trench_Summary.xlsx
         <Project>_Results.xls
         <Project>_Masterfile_04092026.bbx
         <Project> Measurements.xls

Observations that matter:
- Four report types recur: Depth_Categories, Measurements, Results,
  Trench_Summary. Trench_Summary is .xlsx; the other three are legacy .xls
  (Excel.Sheet.8 / BIFF8) and need `xlrd`, not openpyxl.
- Filenames carry a DDMMYYYY date suffix and sometimes a Windows "(1)" copy
  suffix. Several .bbx snapshots per project are near-duplicates.
- Folder path encodes year/month; folder names sometimes carry a "02. " prefix
  and a "- Stage 2" suffix.

## What to build

### Step 1 — Probe first, code second
Run the existing probe (do NOT rewrite it):

  python tools/probe_export.py "D:\Flicker\Flupper\Flupper\masterfile" --out probe_masterfile.json
  python tools/probe_export.py "D:\Flicker\Flupper\Flupper\project" --out probe_drawings.json

It only walks .zip files. Extend it minimally to also accept loose .xls/.xlsx/
.bbx files in a directory tree so layout A is covered. Install `xlrd` for the
legacy .xls files and add legacy-.xls support to the probe.

Then show me, as a STRUCTURE SUMMARY ONLY (no cell values):
  - Every distinct sheet name per report type, and its column headers
  - Which report type carries: cut/fill volumes, depth categories, trench runs
  - The .bbx magic bytes and whether it is zip/SQLite/XML/opaque
  - For drawing zips: file counts by extension, folder depth, whether the PDFs
    have a text layer or are scanned images

STOP after this and wait for my confirmation before writing parsers. Do not
guess at column semantics — show me what is there and I will confirm the
mapping.

### Step 2 — Parsers (only after I confirm Step 1)
Create backend/qsagent/ingest/ with:

  paths.py       Parse project identity from the folder/file naming convention:
                 year, month, project name, report type, DDMMYYYY date, copy
                 index. Pick the LATEST date per report type and ignore stale
                 snapshots and "(1)" duplicates.
  mudshark.py    Read the four report types into typed rows. Handle both layout
                 A (loose) and layout B (zip) behind one interface. Handle the
                 preamble rows before the real header — probe_export.py already
                 detects these; reuse that logic, don't duplicate it.
  bbx.py         Whatever the probe reveals .bbx to be. If it is opaque, do NOT
                 reverse-engineer it — record it as an evidence document by hash
                 and path only, and rely on the Excel exports for quantities.
  wbs.py         Map Mudshark layers to Australian WBS: Topsoil Strip, Bulk
                 Cut, Bulk Fill, Rock Excavation, Backfill, Trench.
  drawings.py    Walk a project zip WITHOUT extracting it to disk. Extract
                 title-block data with PyMuPDF: drawing no, revision, sheet,
                 title. Classify discipline from the drawing number prefix.
  cli.py         `python -m qsagent.ingest.cli --db <path> --project <name>
                 --masterfile <dir-or-zip> --drawings <zip>`

Non-negotiable integration requirements:
- Every ingested figure becomes an EvidenceRef with the source file's SHA-256,
  the sheet/page, and the raw cell text or PDF bbox. Use the existing contracts
  in backend/qsagent/contracts/ — do not invent parallel models.
- Write everything through QSStore so ingestion is journalled. Never write raw
  SQL from the parsers.
- Link the graph: Document -> Drawing -> Element -> Quantity, using
  store.add_node() and store.link().
- Where Mudshark does not state something a quantity depends on (bedding type,
  soil classification, compaction spec), raise an Assumption via
  store.upsert_assumption() rather than silently defaulting.
- Drawing PDFs are referenced by hash and path. NEVER copy the ~1.1 GB of
  drawings into the database or the repo.

### Step 3 — Tests
Add backend/tests/test_ingest.py with SYNTHETIC fixtures in
backend/tests/fixtures/ (that path is deliberately un-ignored). Build tiny .xls
and .xlsx files that reproduce the structure you found — preamble rows, sheet
names, headers — with invented numbers. Cover:
  - Both layout A and layout B
  - Latest-date selection and "(1)" duplicate rejection
  - Legacy .xls vs modern .xlsx
  - A malformed sheet that fails to parse cleanly (must raise a clear error,
    never a silent wrong number)
  - Every parsed quantity carrying valid evidence

The existing 72 tests must stay green.

## Working style
- Work in the order above and STOP at the end of Step 1 for my confirmation.
- Small commits with clear messages; run pytest before each.
- If a parser hits a file it cannot read, fail loudly with the filename and
  reason. A wrong quantity is far worse than a missing one — this platform's
  whole premise is that numbers are trustworthy.
- Do not push. I will review and push myself.
```

---

## Why Step 1 stops for confirmation

Column headers like `Cut (m3)` are unambiguous; `Volume`, `Total` or `Depth
Range` are not. If Cline guesses that a column is in-situ when it is actually
bulked, every downstream quantity is silently wrong but still passes CheckMate —
because CheckMate verifies arithmetic and evidence, not whether the source
column meant what you assumed. Confirming the mapping once, up front, is the
cheapest possible insurance.

## Suggested follow-up prompts

**Housekeeping (worth doing early):**
```
The masterfile folders contain multiple near-duplicate .bbx snapshots — Aldi
Dandenong alone has 6 files of ~35 MB. Write tools/dedupe_report.py that hashes
every .bbx and reports identical-content duplicates and superseded snapshots
grouped by project. Report only — do not delete anything.
```

**When Step 2 is done:**
```
Run the ingest CLI against Aldi Dandenong and show me the resulting graph:
node counts by type, the assumptions raised, and the journal entries. Then run
CheckMate over the ingested quantities and show me the badge summary. Report
structure and counts only — no cell values.
```
