# Amendment to paste into Cline (before toggling to Act)

Copy the block below into Cline as-is.

---

```
IMPORTANT CORRECTION before you start Step 1 — one of your assumptions is wrong.

Trench_Summary.xlsx is NOT a Mudshark export. I created it by hand from
Results.xls, because my client wanted trench Cut/Fill/Length in a separate,
easy-to-read file. I also added the +/- collapsible row grouping myself so I can
expand and collapse different trenches, pipes and pits.

Results.xls is the real Mudshark export. This changes the plan:

1. RE-TARGET THE PRIMARY SOURCE
   Results.xls is the authoritative evidence for trench volumes, not
   Trench_Summary.xlsx. A hand-transcribed file must never be the sole evidence
   for a quantity — it can carry typos and go stale when the model is
   re-exported. Probe Results.xls FIRST. Installing xlrd is now the critical
   path, not a leftover blocker.

2. KEEP Trench_Summary AS AN INDEPENDENT CROSS-CHECK
   Do not discard it. Parse both, match on trench run / operation group, and
   compare totals. On mismatch beyond tolerance raise a CheckMate WARN finding
   ("hand-prepared summary disagrees with Mudshark export by X%") — NOT an
   exception. This catches transcription errors in a live client deliverable and
   is one of the most valuable checks in the whole ingest.

3. MODEL PROVENANCE EXPLICITLY
   Add provenance: Literal["machine_export", "hand_prepared"] to the document
   payload. In the graph:
     document  Results.xls          provenance=machine_export
     document  Trench_Summary.xlsx  provenance=hand_prepared
     edge      Trench_Summary --derived_from--> Results.xls
   Only machine_export rows create quantity nodes. hand_prepared rows create
   reconciliation records ONLY — otherwise every trench quantity is
   double-counted, since the summary rows are copies of the Results rows.
   Add a CheckMate rule: a claim whose only evidence is a hand_prepared document
   must FAIL.

4. READ THE +/- GROUPING, DO NOT INFER IT
   The collapsible panel is Excel outline metadata, not formatting. Read it
   directly:
       ws.row_dimensions[row].outline_level
   Verified behaviour: outline_level is 0 for group headers and 1+ for children,
   so hierarchy does not need to be guessed from 'Strata'/'Material' keywords or
   indentation. Use outline_level as authoritative for .xlsx, and fall back to
   keyword matching only for the legacy .xls files, where xlrd does not expose
   outline data as readily.
   My grouping also reflects how I organise trenches/pipes/pits in practice —
   treat it as real WBS domain knowledge and encode it in wbs.py rather than
   inventing a different breakdown.

Everything else in your revised plan is approved as written — the
Bulked/Compressed/Banked measurement-state table, summing leaf rows and
cross-checking against cached formula results, treating None in a numeric leaf
as a ParseError, mapping by header name rather than column index, dropping xlwt,
and pinning xlrd>=2.0.1.

Proceed with Step 1, probing Results.xls first, then STOP and report the
structure summary as agreed.
```
