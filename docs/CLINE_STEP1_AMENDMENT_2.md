# Amendment 2 — paste into Cline before toggling to Act

---

```
Two corrections before you start Step 1. Both concern .xls specifically, and
both would only surface in Step 2 if left alone.

1. YOUR OUTLINE-LEVEL TABLE WAS VALIDATED ON THE WRONG FILE

You verified outline_level on Trench_Summary.xlsx via openpyxl. But we agreed
Results.xls is now the primary source, and it is legacy BIFF8 read via xlrd,
where outline data behaves completely differently. Verified on xlrd 2.0.2:

    rowinfo_map populated without formatting_info:   False
    with formatting_info=True -> rowinfo_map entries: 3

So:
  - probe_xls() as you specified it (on_demand=True, no formatting_info) will
    return NO outline data at all. rowinfo_map is empty and the file will look
    like it has no grouping when it might.
  - on_demand=True and formatting_info=True do not combine well. Drop on_demand
    for this probe.
  - Wrap it in try/except: formatting_info=True raises on some real BIFF
    variants, and a crash must not be misread as "no grouping".

Most important: I added the +/- grouping to Trench_Summary.xlsx BY HAND. There
is no reason to assume the Mudshark export carries any grouping. So the single
most important question in Step 1 is:

    Does Results.xls contain row outline grouping at all?

Report len(sheet.rowinfo_map) and the distinct outline_level values found. If
they are all 0 or the map is empty, the four-level table does not apply to the
primary source, and hierarchy must instead come from label keywords, indentation
or column emptiness — report which of those signals is actually present in the
file.

2. ONE OF YOUR STEP 1 DELIVERABLES IS IMPOSSIBLE

You promised to report "which columns carry formula vs literal values in each
.xls sheet". xlrd cannot do this — it has no formula concept and exposes only
the cached result. Verified:

    xlrd cell types in a .xls containing a SUM formula:
      row 0: type=NUMBER  value=120.0     <- literal
      row 1: type=NUMBER  value=80.0      <- literal
      row 2: type=TEXT    value=''        <- the SUM formula

A formula cell is indistinguishable from a literal one. This breaks your
"sum leaves, cross-check against cached totals" rule for .xls, because you
cannot tell a total row from a leaf row by formula detection. For Results.xls,
totals must be identified STRUCTURALLY — label text, row position, outline level
if present. The formula-based approach still works for Trench_Summary.xlsx via
openpyxl; keep it there.

Replace that deliverable with:
    For each .xls sheet, report the cell ctype histogram per column, and flag
    any column mixing NUMBER with TEXT/EMPTY — those are the likely total/label
    rows.

3. REFINE THE ParseError RULE FOR .xls

"None in a numeric leaf -> ParseError" is right for .xlsx. In .xls, xlrd reports
XL_CELL_EMPTY (0) and XL_CELL_BLANK (6) for genuinely empty cells, and uncached
formulas land there too — so that rule would fire on legitimate spacer rows.
Refine to: a row is a data row if it has a numeric value in at least one
measurement column. Empty measurement cells WITHIN such a row -> ParseError.
Entirely empty rows -> skip as spacers.

4. TWO SMALL THINGS

- Add "Reused (Bulked m3)" and "Exported (Bulked m3)" to your measurement-state
  table — your column table jumps from col 3 to col 5 and omits both. Both are
  bulked. Reused matters for site balance and for not double-counting material
  that never leaves site.
- Decide NOW, not later: you wrote OL-3 material rows are "ingest as
  sub-quantity or skip per WBS". If both a run row and its material children
  become quantities, every trench volume is double-counted. Make material rows
  payload detail on the run's quantity node, not sibling quantities.

Everything else in your plan is approved as written — provenance model,
derived_from edge, machine_export-only quantities, bulked->in-situ conversion
with a raised Assumption, cross-check as WARN not exception, and header-name
mapping.

Proceed with Step 1, then STOP and report as agreed.
```
