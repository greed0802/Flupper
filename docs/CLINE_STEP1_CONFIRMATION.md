# Step 1 confirmed — with three corrections before Step 2

The structure summary answered all four questions. Two findings materially
de-risk the build:

- **`Results.xls` does carry outline grouping** (`rowinfo_map_size` 421 / 246 /
  127), so hierarchy is metadata, not inference. It uses `{0,1,2}` on volume
  sheets, not the four levels seen in `Trench_Summary.xlsx` — the plan correctly
  noted the table does not transfer.
- **`Depth_Categories.xls` is effectively empty** (1 row, 1 col). Warn-and-skip
  is right. That file would otherwise have looked like a missing-data bug later.

Three corrections before parsers.

---

## Correction 1 — CRITICAL: the "All ..." sheets are unions. Do not ingest them.

The sheet inventory contains both aggregate and component sheets. The row counts
show the aggregates are unions of their components:

```
Operations: components sum = 430  vs  'All Strata Operations' = 426   (ratio 1.009)
     249  Ground Layer Operations
      53  Structure Strata Operations
     128  Trench Run Strata Operations

Materials:  components sum = 808  vs  'All Materials'         = 796   (ratio 1.015)
     459  Ground Layer Materials
     149  Structure Materials
     200  Trench Run Materials
```

Within 1–2% — the residual is section headers and spacers that collapse when
merged. These are the *same operations* presented twice.

The plan lists all 12 sheets with no ingest/skip decision attached, and the
existing rule ("OL-1 rows with numbers → ingest as quantity node") would fire on
**both** `All Strata Operations` and `Ground Layer Operations`. That doubles
every earthworks quantity on the project.

This is the same failure already caught twice — for `Trench_Summary` vs
`Results`, and for material rows vs run rows. It is the dominant risk in this
ingest.

**Required — an explicit per-sheet decision table before any parser runs:**

| Sheet | Decision |
|---|---|
| `Ground Layer Operations` | **INGEST** — earthworks quantities |
| `Structure Strata Operations` | **INGEST** — structure cut/fill |
| `Trench Run Strata Operations` | **INGEST** — trench bedding/import |
| `All Strata Operations` | **SKIP** — union of the three above; use only as a total cross-check |
| `Ground Layer Materials` | payload on parent quantity |
| `Structure Materials` | payload on parent quantity |
| `Trench Run Materials` | payload on parent quantity |
| `All Materials` | **SKIP** — union |
| `Trenches` | **INGEST** as `linear_m` (lengths, never volumes) |
| `Trench Depth Categories` | **INGEST** as `linear_m` per depth band — but see Correction 2 |
| `Areas and Perimeters` | INGEST as `m2` / `m` |
| `Structure Thickness Breakdown` | payload / cross-check against structure volumes |

Add a CheckMate rule: if the summed component sheets disagree with the
corresponding `All ...` sheet by more than tolerance, raise a WARN. That turns
the redundancy into a free verification instead of a hazard.

## Correction 2 — `Trenches` and `Trench Depth Categories` overlap

The summary states these have the same `TrenchNetwork → TrenchRun →
TrenchSegment` hierarchy, differing only in grouping (by segment vs by depth
category). If both are ingested as `linear_m` quantities, **every trench length
is counted twice**.

Decide which is canonical. Recommend `Trenches` as the quantity source (it is
the natural segment-level breakdown), with `Trench Depth Categories` ingested as
a *depth-band attribute* on those segments — depth bands drive rate selection,
so the data is needed, but as a dimension, not a second quantity.

Then cross-check: total length in `Trenches` must equal total length across all
depth bands. Mismatch → CheckMate WARN.

## Correction 3 — "All PDFs have text layers" is an extrapolation

The probe deep-inspects at most **2 files per extension per archive**. Across
555 PDFs that is ~12 sampled, or **2.2%**. This is a limitation of the probe
tool as written, not of the analysis — but the conclusion "all PDFs have text
layers, no OCR needed" is not supported by a 2.2% sample.

It matters because a scanned drawing hiding in that 98% will not fail loudly. It
will yield an empty title block, and unless handled it becomes a document with
no drawing number silently absent from the graph.

Required:

- Raise the per-archive sample or add a cheap full scan that checks only
  `has_text_layer` for every PDF (no title-block parse) and reports the count of
  text-layer PDFs vs image-only PDFs per archive.
- Regardless of the result, `drawings.py` must treat "no extractable title
  block" as an explicit, reported outcome — a document recorded with
  `title_block_extracted=False` and a warning — never a silent skip.

Also note `nerang-self-storage` contains 27 `.jpg` and 4 `.xlsx`, and
`st-padre-pio` a `.docx`. Decide whether those are drawings, site photos or
specifications; classify or explicitly ignore them, but do not let them fall
through unclassified.

---

## Confirmed as correct — proceed with these as written

- Measurement-state mapping across all 8 columns, including `Exported` and
  `Reused`.
- `Cut (Bulked m³) ÷ BF → insitu_m3` with an Assumption raised for the factor.
- OL=0 skip / OL=1 ingest / OL=2 payload on volume sheets.
- Structural (not formula-based) total identification in `.xls`.
- `Measurements.xls` 9-row preamble, header at row 10, `linear_m`.
- `Depth_Categories.xls` warn-and-skip.
- `.bbx` by hash and path only; XML `<Project><Name>` as a folder cross-check;
  duplicate detection by content hash.
- `Trench_Summary.xlsx` as `hand_prepared` reconciliation only, matched on
  operation-group labels against `Trench Run Strata Operations`.

## Step 2 exit criteria

Ingesting Aldi Dandenong twice must produce identical graph output, and the
total of every ingested quantity must reconcile against the corresponding
`All ...` sheet within tolerance. If those two hold, the double-counting class
of bug is closed.
