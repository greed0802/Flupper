# Paste into Cline — Step 1 confirmed, corrections for Step 2

---

```
Step 1 accepted. The outline-grouping answer and the Depth_Categories finding
are exactly what was needed. Three corrections, then proceed to Step 2.

1. CRITICAL — THE "All ..." SHEETS ARE UNIONS. DO NOT INGEST THEM.

Your own row counts prove it:

  Operations: 249 + 53 + 128 = 430  vs  'All Strata Operations' = 426  (1.009)
  Materials:  459 + 149 + 200 = 808  vs  'All Materials'        = 796  (1.015)

Within 1-2% — the residual is headers and spacers collapsing on merge. These are
the same operations presented twice. You listed all 12 sheets with no
ingest/skip decision, and your rule "OL-1 rows with numbers -> ingest as
quantity node" would fire on BOTH All Strata Operations AND Ground Layer
Operations. That doubles every earthworks quantity.

Use this decision table:

  Ground Layer Operations         INGEST  (earthworks)
  Structure Strata Operations     INGEST  (structure cut/fill)
  Trench Run Strata Operations    INGEST  (trench bedding/import)
  All Strata Operations           SKIP    (union; total cross-check only)
  Ground Layer Materials          payload on parent quantity
  Structure Materials             payload on parent quantity
  Trench Run Materials            payload on parent quantity
  All Materials                   SKIP    (union)
  Trenches                        INGEST  as linear_m
  Trench Depth Categories         depth-band attribute, NOT a quantity (see 2)
  Areas and Perimeters            INGEST  as m2 / m
  Structure Thickness Breakdown   payload / cross-check

Add a CheckMate rule: summed component sheets vs the corresponding 'All ...'
sheet, mismatch beyond tolerance -> WARN. That turns the redundancy into free
verification instead of a hazard.

2. Trenches AND Trench Depth Categories OVERLAP

You said both carry the same TrenchNetwork -> TrenchRun -> TrenchSegment
hierarchy, differing only in grouping. Ingesting both as linear_m counts every
trench length twice. Make 'Trenches' canonical for length, and ingest
'Trench Depth Categories' as a depth-band ATTRIBUTE on those segments — depth
bands drive rate selection so the data is needed, but as a dimension, not a
second quantity. Cross-check: total length in Trenches must equal total across
all depth bands; mismatch -> WARN.

3. "ALL PDFs HAVE TEXT LAYERS" IS NOT SUPPORTED YET

probe_export.py deep-inspects at most 2 files per extension per archive. Across
555 PDFs that is ~12 sampled = 2.2%. That is a limit of my probe tool, not your
analysis — but the conclusion is an extrapolation. A scanned drawing hiding in
the other 98% will not fail loudly; it will produce an empty title block.

Add a cheap full scan that checks ONLY has_text_layer for every PDF (no
title-block parse) and reports text-layer vs image-only counts per archive.
Regardless of the result, drawings.py must treat "no extractable title block" as
an explicit reported outcome — record the document with
title_block_extracted=False plus a warning — never a silent skip.

Also: nerang-self-storage has 27 .jpg and 4 .xlsx, st-padre-pio has a .docx.
Classify them (drawing / site photo / specification) or explicitly ignore them,
but do not let them fall through unclassified.

CONFIRMED AS CORRECT — build these as written:
  - Measurement states across all 8 columns incl. Exported and Reused
  - Cut (Bulked m3) / BF -> insitu_m3, raising an Assumption for the factor
  - OL=0 skip / OL=1 ingest / OL=2 payload on volume sheets
  - Structural (not formula-based) total identification in .xls
  - Measurements.xls: 9-row preamble, header row 10, linear_m
  - Depth_Categories.xls: warn and skip
  - .bbx by hash+path only; XML <Project><Name> as folder cross-check;
    duplicates by content hash
  - Trench_Summary.xlsx as hand_prepared reconciliation only, matched on
    operation-group labels against Trench Run Strata Operations

STEP 2 EXIT CRITERIA — do not call it done until both hold:
  a) Ingesting Aldi Dandenong twice produces identical graph output.
  b) Every ingested quantity total reconciles against the corresponding
     'All ...' sheet within tolerance.

Proceed with Step 2. Commit in small steps, run pytest before each, do not push.
```
