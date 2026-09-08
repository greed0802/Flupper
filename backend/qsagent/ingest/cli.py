"""Ingest CLI."""
from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _ikey(*parts: str) -> str:
    """Deterministic ingest_key: sha256 of pipe-joined logical parts.

    The key must identify the LOGICAL ENTITY, not the file instance, so that
    a corrected Mudshark re-export (different file_hash, same model content)
    updates rows in place rather than orphaning old rows and inserting duplicates.

    Key composition:
        evidence node:    project_id | report_type | sheet | operation_group
        quantity claim:   project_id | report_type | sheet | operation_group | col_header

    file_hash is NOT in the key.  It is stored on the EvidenceRef as provenance
    so every ingest records which file version produced each quantity.

    operation_group is unique within each Mudshark volume sheet (confirmed:
    the reference project project has 4 OL=1 groups across 3 sheets; each (sheet, group) pair
    appears exactly once).  row_index is therefore not needed and is omitted —
    including it would make the key sensitive to rows inserted above in
    re-exports, which is exactly the instability this key is designed to prevent.
    """
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _ingest_masterfile(store, project_id, masterfile):
    from .mudshark import MudsharkSource  # noqa
    from .bbx import record_bbx_set  # noqa
    from .wbs import annotate_wbs, WBSItem, infer_bulking_factor, bulked_to_insitu  # noqa
    from ..contracts import (  # noqa
        EvidenceRef, EvidenceNode, Discipline, Quantity, Unit, QuantityClaim, Assumption,
    )
    # NOTE: no project-wide DELETE here.  Each row carries an ingest_key so
    # re-ingesting this source file upserts only the rows it owns.  Rows from
    # other sources (manual QS allowances, drawing-derived quantities) survive.


    bbx_paths = list(masterfile.rglob("*.bbx")) if masterfile.is_dir() else []
    if bbx_paths:
        bbx_records, bbx_dupes = record_bbx_set(bbx_paths)
        for rec in bbx_records:
            store.add_document(project_id, file_name=rec.file_name,
                               file_hash=rec.file_hash, media_type="application/bbx",
                               title=rec.mudshark_project_name)
        for a, b in bbx_dupes:
            log.warning("Duplicate BBX: %s == %s", a.file_name, b.file_name)

    with MudsharkSource(masterfile) as src:
        workbooks = src.parse_all()

    results_wb = workbooks.get("Results")
    if not results_wb:
        log.error("No Results.xls found in %s", masterfile)
        return

    r_doc_id = store.add_document(project_id,
        file_name=results_wb.file_name, file_hash=results_wb.file_hash,
        media_type="application/vnd.ms-excel", discipline="CIVIL",
        title="Mudshark Results Export")

    d_key = _ikey(str(project_id), "mudshark.results", "document")
    r_node_id = store.add_node(EvidenceNode(
        project_id=project_id, node_type="document", label=results_wb.file_name,
        discipline=Discipline.CIVIL,
        payload={"provenance": "machine_export", "file_hash": results_wb.file_hash,
                 "doc_id": r_doc_id},
    ), ingest_key=d_key)

    for name, xc in results_wb.cross_checks.items():
        if not xc.passed:
            log.warning("CheckMate WARN [%s]: %s", name, xc.message)
    for w in results_wb.warnings:
        log.warning(w)

    def _ref(sheet, row, raw):
        return EvidenceRef(file_hash=results_wb.file_hash,
                           file_name=results_wb.file_name,
                           sheet=sheet, raw_text=raw)

    annotate_wbs(results_wb.quantity_rows)

    all_headers = [h for qr in results_wb.quantity_rows for h in qr.values]
    CUT_COL  = next((h for h in all_headers if "Cut (Bulked"        in h), None)
    FILL_COL = next((h for h in all_headers if "Fill (Compressed"   in h), None)
    IMP_COL  = next((h for h in all_headers if "Imported (Banked"   in h), None)

    # Collision guard: within a single ingest run, two distinct rows must not
    # produce the same ingest_key with different values.  If they do, the
    # occurrence ordinal has failed to disambiguate them — raise immediately
    # rather than silently overwriting one with the other.
    _seen_claim_keys: dict[str, float] = {}   # key -> value from this run

    def _guard(key: str, value: float) -> None:
        if key in _seen_claim_keys:
            prev = _seen_claim_keys[key]
            if abs(prev - value) > 1e-9:
                raise ValueError(
                    f"ingest_key collision within run: key={key!r} "
                    f"first_value={prev} second_value={value}. "
                    f"Two rows with the same (sheet, operation_group, occurrence) "
                    f"produced different quantities — the occurrence ordinal is "
                    f"insufficient to disambiguate them."
                )
        _seen_claim_keys[key] = value

    for qr in results_wb.quantity_rows:
        op  = qr.operation_group
        wbs = qr.wbs_item or WBSItem.UNKNOWN
        mat = [{"name": m.name, "values": m.values} for m in qr.materials]

        n_key = _ikey(str(project_id), "mudshark.results", qr.sheet, qr.operation_group,
                      str(qr.occurrence))
        qnode = store.add_node(EvidenceNode(
            project_id=project_id, node_type="quantity",
            label=f"{wbs}: {op}", discipline=Discipline.CIVIL,
            payload={"wbs_item": wbs, "sheet": qr.sheet, "row": qr.row_index,
                     "values": qr.values, "materials": mat},
        ), ingest_key=n_key)
        store.link(project_id, r_node_id, qnode, "has_quantity")

        if CUT_COL and CUT_COL in qr.values:
            cut_val = qr.values[CUT_COL]
            # ── Measurement-state resolution ──────────────────────────────────
            # Mudshark column headers are role labels ('Cut (Bulked m³)' etc.)
            # but the emitted figures may all be in one unified state (e.g.
            # in-situ) if the project's BF/SF are both 1.0.  Until the project
            # settings file is parsed and the factors confirmed to be != 1.0,
            # applying a bulking-factor conversion would produce a wrong result.
            # The claim is therefore tagged UNRESOLVED and the raw column value
            # is stored without conversion.  A later resolution step will apply
            # the correct factor once BF is confirmed from the BBX project file.
            bf_val, bf_key = (1.25, "general")
            for m in qr.materials:
                bf_val, bf_key = infer_bulking_factor(m.name)
                break
            aid = store.next_assumption_id(project_id)
            store.upsert_assumption(Assumption(
                id=aid, project_id=project_id,
                statement=(f"Measurement state of Cut column for '{op}' is UNRESOLVED. "
                           f"Nominal BF={bf_val} ({bf_key}) from material; not applied until "
                           f"project BF/SF settings confirmed from BBX. "
                           f"Raw value={cut_val:.3f} m3 (column: {CUT_COL}).")))
            c_key = _ikey(str(project_id), "mudshark.results", qr.sheet, qr.operation_group,
                          str(qr.occurrence), CUT_COL)
            _guard(c_key, round(cut_val, 3))
            store.save_claim(QuantityClaim(
                project_id=project_id,
                description=f"Cut volume \u2013 {wbs}: {op} [state UNRESOLVED]",
                quantity=Quantity(value=round(cut_val, 3), unit=Unit.M3),
                measurement_state="UNRESOLVED",
                method="mudshark.ingest.cut_raw_unresolved",
                evidence=[_ref(qr.sheet, qr.row_index, f"{CUT_COL}={cut_val}")],
                assumption_ids=[aid],
                workings=[f"Raw={cut_val:.3f} m3; BF={bf_val} ({bf_key}) NOT APPLIED"
                          f" — state UNRESOLVED pending BBX project settings."],
            ), ingest_key=c_key)

    for lr in results_wb.linear_rows:
        if lr.sheet != "Trenches":
            continue
        c_key = _ikey(str(project_id), "mudshark.results", lr.sheet, lr.label, "Quantity(m)")
        store.save_claim(QuantityClaim(
            project_id=project_id, description=f"Trench length \u2013 {lr.label}",
            quantity=Quantity(value=round(lr.quantity_m, 3), unit=Unit.M),
            method="mudshark.ingest.trench_length",
            evidence=[_ref(lr.sheet, lr.row_index, f"Quantity(m)={lr.quantity_m}")],
        ), ingest_key=c_key)

    ts_wb = workbooks.get("Trench_Summary")
    if ts_wb:
        ts_id = store.add_document(project_id,
            file_name=ts_wb.file_name, file_hash=ts_wb.file_hash,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            title="Hand-prepared Trench Summary")
        td_key = _ikey(str(project_id), "mudshark.trench_summary", "document")
        ts_node_id = store.add_node(EvidenceNode(
            project_id=project_id, node_type="document", label=ts_wb.file_name,
            discipline=Discipline.CIVIL,
            payload={"provenance": "hand_prepared", "doc_id": ts_id}
        ), ingest_key=td_key)
        store.link(project_id, ts_node_id, r_node_id, "derived_from")
        for w in ts_wb.warnings:
            log.warning("Trench_Summary: %s", w)
        for qr in ts_wb.quantity_rows:
            n_key = _ikey(str(project_id), "mudshark.trench_summary", "trench_summary", qr.operation_group)
            store.add_node(EvidenceNode(
                project_id=project_id, node_type="element",
                label=f"TS: {qr.operation_group}", discipline=Discipline.CIVIL,
                payload={"source": "hand_prepared", "sheet": "Trench_Summary",
                          "row": qr.row_index, "values": qr.values}
            ), ingest_key=n_key)


def _ingest_drawings(store, project_id, drawings_zip):
    from .drawings import probe_drawing_zip  # noqa
    from ..contracts import EvidenceNode, Discipline  # noqa

    records = probe_drawing_zip(drawings_zip)
    for rec in records:
        disc_str = rec.discipline if rec.discipline != "UNKNOWN" else "UNKNOWN"
        try:
            disc = Discipline[disc_str]
        except KeyError:
            disc = Discipline.UNKNOWN

        doc_id = store.add_document(project_id,
            file_name=rec.file_name, file_hash=rec.file_hash,
            media_type=f"application/{rec.kind}", discipline=disc_str,
            drawing_no=rec.drawing_no, revision=rec.revision,
            title=rec.sheet_title, page_count=rec.page_count)

        node_id = store.add_node(EvidenceNode(
            project_id=project_id,
            node_type="drawing" if rec.kind == "pdf" else "document",
            label=rec.file_name, discipline=disc,
            payload={
                "kind": rec.kind, "file_hash": rec.file_hash, "doc_id": doc_id,
                "has_text_layer": rec.has_text_layer,
                "title_block_extracted": rec.title_block_extracted,
                "drawing_no": rec.drawing_no, "revision": rec.revision,
            },
        ))

        for w in rec.warnings:
            log.warning(w)
        if rec.error:
            log.error("Drawing parse error %s: %s", rec.file_name, rec.error)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--masterfile", required=True)
    ap.add_argument("--drawings", required=False)
    ap.add_argument("--client", default=None)
    args = ap.parse_args(argv)

    from ..storage import QSStore  # noqa

    store = QSStore(args.db)
    project_id = store.get_or_create_project(args.project, client=args.client)
    log.info("Project '%s' id=%d", args.project, project_id)

    masterfile = Path(args.masterfile)
    if not masterfile.exists():
        log.error("masterfile path not found: %s", masterfile)
        return 1
    _ingest_masterfile(store, project_id, masterfile)

    if args.drawings:
        drawings = Path(args.drawings)
        if not drawings.exists():
            log.error("drawings zip not found: %s", drawings)
            return 1
        _ingest_drawings(store, project_id, drawings)

    store.close()
    log.info("Ingest complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
