# Review 3 — the outline-level strategy does not survive the move to Results.xls

The plan is nearly executable. The provenance model, measurement-state table and
formula rules are all correct as written. But two of its own conclusions now
contradict each other, and the contradiction only bites in Step 2 — after the
Step 1 stop, when it is expensive to discover.

---

## The contradiction

Review 2 established: **`Results.xls` is the primary evidence source.**

This plan establishes: **`outline_level` is the authoritative row-hierarchy
signal**, with a four-level table (0 = header/grand total, 1 = divider/op-group,
2 = run leaf, 3 = material) driving which rows become quantities.

Those cannot both hold. `outline_level` was verified on **`Trench_Summary.xlsx`**
— the hand-prepared file, read through openpyxl. `Results.xls` is legacy BIFF8,
read through `xlrd`, where outline data behaves very differently.

Verified on xlrd 2.0.2:

```
rowinfo_map populated without formatting_info:  False
with formatting_info=True -> rowinfo_map entries: 3
```

So outline levels are only reachable if the workbook is opened with
`formatting_info=True`. That has three consequences the plan does not account
for:

1. It is **not the default**, and `probe_xls()` as specified
   (`on_demand=True`, no `formatting_info`) will return **no outline data at
   all** — `rowinfo_map` is empty. The probe would silently report nothing and
   look like the file simply has no grouping.
2. `formatting_info=True` **materially increases memory and parse time**. Your
   `Results.xls` files are 312 KB and 807 KB — survivable, but it must be a
   deliberate choice, not an accident.
3. Most importantly: **Mudshark's export may not contain outline grouping at
   all.** The `+/-` grouping in `Trench_Summary.xlsx` was added *by the user, by
   hand*. There is no reason to expect a machine export to carry it. If it does
   not, the entire four-level table is inapplicable to the primary source.

**The row-hierarchy strategy has been validated only against the file that is no
longer the primary source.**

## Required change

`probe_xls()` must answer this explicitly, and it is the single most important
question in Step 1:

> Does `Results.xls` contain row outline grouping at all?

Concretely:

- Open with `xlrd.open_workbook(file_contents=data, formatting_info=True)`.
  Note that `on_demand=True` and `formatting_info=True` do not combine well —
  drop `on_demand` for this probe.
- Report `len(sheet.rowinfo_map)` and the distinct `outline_level` values found.
- If all levels are 0, or `rowinfo_map` is empty: **there is no grouping in the
  primary source**, and hierarchy must come from label keywords, indentation or
  column emptiness instead. Report which of those signals is actually present.
- Wrap the whole thing in try/except. `formatting_info=True` raises on some
  real-world BIFF variants; a probe crash must not be read as "no grouping".

## Second problem — one Step 1 deliverable is impossible

The plan promises to report:

> "Which columns carry formula vs literal values in each .xls sheet"

`xlrd` cannot do this. It has no formula concept for `.xls`; it exposes only the
cached result. Verified:

```
xlrd cell types in a .xls containing a SUM formula:
  row 0: type=NUMBER  value=120.0     <- literal
  row 1: type=NUMBER  value=80.0      <- literal
  row 2: type=TEXT    value=''        <- the SUM formula
```

A formula cell is **indistinguishable from a literal cell** — it either presents
as `NUMBER` carrying the cached result, or, where no cache was written, as an
empty/blank cell that a parser could easily misread as a legitimately empty row.

This matters because the plan's formula rule ("always sum leaf rows, cross-check
against cached totals") depends on telling totals from leaves. In `.xlsx` that
works — openpyxl exposes the formula string. **In `.xls` it does not.** For
`Results.xls`, total-vs-leaf must be identified structurally (label text, row
position, outline level if present), never by formula detection.

Replace that deliverable with:

> For each `.xls` sheet, report the cell `ctype` histogram per column, and flag
> any column mixing `NUMBER` with `TEXT`/`EMPTY` — those are the likely
> total/label rows.

## Third — the empty-vs-failed distinction

The rule "`None` in a numeric leaf → `ParseError`" is right for `.xlsx`. For
`.xls`, `xlrd` reports `XL_CELL_EMPTY` (ctype 0) and `XL_CELL_BLANK` (ctype 6)
for genuinely empty cells, and an uncached formula also lands there. So a
`ParseError` on every empty cell will fire on rows that are legitimately blank —
spacers, section gaps.

Refine: a leaf row identified as a data row must have a numeric value in at
least one measurement column. Empty *measurement* cells within an otherwise
populated data row → `ParseError`. Entirely empty rows → skip as spacers.

---

## Minor

- Add `Reused (Bulked m³)` and `Exported (Bulked m³)` to the measurement-state
  table. They are listed in the header but omitted from the plan's column table,
  which jumps from col 3 to col 5. Both are bulked; `Reused` in particular is
  needed to compute a correct site balance and to avoid double-counting material
  that never leaves site.
- The plan says OL-3 material rows are "ingest as sub-quantity **or skip per
  WBS**". Decide before Act. If both the run row and its material children are
  ingested as quantities, every trench volume is double-counted — exactly the
  failure mode already identified for the two-document case. Recommend: material
  rows become `payload` detail on the run's quantity node, not sibling
  quantities.

---

## Verdict

Approve Step 1 **with `probe_xls()` amended** to answer the outline-grouping
question with `formatting_info=True`, and with the impossible formula-detection
deliverable replaced by the ctype histogram.

Everything else — provenance model, `derived_from` edge, machine_export-only
quantities, bulked→in-situ conversion with a raised Assumption, cross-check as
WARN not exception, header-name mapping — is correct and should proceed as
written.
