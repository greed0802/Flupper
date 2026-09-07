# Review 2 — Trench_Summary is a derived artefact, not a source

Cline's revised plan correctly absorbed all four earlier corrections. The
measurement-state table and the row-hierarchy classification are exactly what
Step 1 needed to produce.

But a clarification from the user invalidates one of its foundations.

---

## The clarification

> "The Trench Summary excel file on the Masterfile was created only by me,
> making the Trench Summary Cut / Fill and Length from the Result excel file
> since my client wanted a separate file for it... I also copied adding group
> with different trenches and pipes/pits so I can click +/- on side panel."

So:

- `Results.xls` is the **Mudshark-generated export** — the primary source.
- `Trench_Summary.xlsx` is a **hand-built human deliverable**, derived by the
  user from `Results.xls`, restructured for a client's convenience.

## Why this matters — evidence provenance

The whole platform rests on the claim that every quantity traces to a source
document by hash. Cline's plan makes `Trench_Summary` the primary parse target
for trench volumes. That would make the evidence chain:

```
Quantity ──► Trench_Summary.xlsx   ← hand-made, retyped, re-grouped
```

which is **not** the authoritative source. It is a transcription. Any typo,
stale copy-paste, or grouping error made while building it becomes an audited
"fact" with a VERIFIED CheckMate badge on it. Worse, it is the one file in the
set that is *not* reproducible from Mudshark — regenerate the export and the
summary does not update itself.

Correct chain:

```
Quantity ──► Results.xls (Mudshark export)          [PRIMARY EVIDENCE]
                 │
                 └──► Trench_Summary.xlsx           [DERIVED / CROSS-CHECK]
```

## Required changes to the plan

### 1. Re-target the parser: `Results.xls` is primary

`Results.xls` (~312 KB Aldi / ~807 KB St Padre) is the largest report and the
actual Mudshark output. Trench volumes must be parsed from it, not from
`Trench_Summary.xlsx`. This raises the priority of the `xlrd` work from
"remaining blocker" to **the critical path** — the three legacy `.xls` files are
the real sources, and none of them have been inspected yet.

### 2. Keep `Trench_Summary` — but as an independent reconciliation

Do not discard it. It is a genuinely valuable **second opinion**: a
human-prepared figure to reconcile against the machine source. Parse both, then:

- Match on trench run / operation group identity.
- Compare the derived total against the `Results.xls` total.
- On mismatch beyond tolerance, raise a **CheckMate finding**, not an exception:
  `WARN` — "hand-prepared summary disagrees with Mudshark export by X%".

That is a real QS control: it catches transcription errors in the client
deliverable. It is arguably the single most useful check in the whole ingest.

Model it explicitly in the graph:

```
node_type='document'  Results.xls          (source='mudshark_export')
node_type='document'  Trench_Summary.xlsx  (source='hand_prepared')
edge: Trench_Summary --derived_from--> Results.xls
```

Add `provenance: Literal["machine_export", "hand_prepared"]` to the document
payload. Never let a `hand_prepared` document be the sole evidence for a claim —
this belongs in CheckMate as a hard rule.

### 3. The +/- grouping is Excel outline metadata — read it, don't infer it

The user built the collapsible side panel deliberately. That grouping is stored
as `outlineLevel` on each row and is readable directly:

```
row 1: outline_level=0  'Operation Group'
row 2: outline_level=0  'Stormwater Drainage'
row 3: outline_level=1  '  SW Run 1'
row 4: outline_level=1  '  SW Run 2'
row 5: outline_level=0  'Sewer'
row 6: outline_level=1  '  SE Run 1'
```

Cline's plan infers hierarchy from label keywords (`Strata`, `Material`) and row
patterns. That is fragile and unnecessary for this file. Use:

```python
ws.row_dimensions[row].outline_level
```

as the authoritative depth, with keyword matching only as a fallback. Note this
works for `.xlsx` via openpyxl; the legacy `.xls` files need the keyword
approach, since `xlrd` exposes outline data far less readily.

The user's grouping also encodes **their own WBS** — how they mentally organise
trenches, pipes and pits. That is domain knowledge worth capturing directly into
`wbs.py` rather than re-deriving.

### 4. Do not double-count

`Trench_Summary` rows are *copies* of `Results.xls` rows. Ingesting both without
marking provenance would double every trench quantity in the graph. Only
`machine_export` rows create `quantity` nodes; `hand_prepared` rows create
reconciliation records.

---

## Question the user should answer before Act

1. **Which columns did you copy by hand, and which are live formulas** pointing
   back at `Results.xls`? If they are static values, transcription drift is
   possible and reconciliation is essential. If they are formulas, they may
   still be stale caches.
2. **Is `Length` in `Trench_Summary` from the Measurements section** of
   `Results.xls`, or measured separately? It is linear metres, not volume, and
   must not be mixed into the volume columns.
3. **Do the `+/-` groups map to your standard WBS**, or are they ad hoc per
   project? If standard, `wbs.py` should encode them as the canonical breakdown.

---

## Otherwise: the revised plan is sound

- Measurement-state table (Bulked / Compressed / Banked) is correct and is the
  fix to the 30% error risk.
- Sum-leaves-and-cross-check-cache is right, and the finding that both sample
  files *do* have caches while still not relying on them is exactly the right
  posture.
- "Map by header name, not column index" is a good catch.
- Dropping `xlwt` and pinning `xlrd>=2.0.1` are both actioned.

Approve Step 1 with the re-targeting above: **probe `Results.xls` first**, since
it is now known to be the primary evidence source.
