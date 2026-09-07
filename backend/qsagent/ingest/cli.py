"""Ingest CLI."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _ingest_masterfile(store, project_id, masterfile):
    from .mudshark import MudsharkSource  # noqa
    from .bbx import record_bbx_set  # noqa
    from .wbs import annotate_wbs, WBSItem, infer_bulking_factor, bulked_to_insitu  # noqa
    from ..contracts import (  # noqa
        EvidenceRef, EvidenceNode, Discipline, Quantity, Unit, QuantityClaim, Assumption,
    )
    store.conn.execute("DELETE FROM evidence_nodes WHERE project_id=?", (project_id,))
    store.conn.execute("DELETE FROM quantity_claims WHERE project_id=?", (project_id,))


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

    r_node_id = store.add_node(EvidenceNode(
        project_id=project_id, node_type="document", label=results_wb.file_name,
        discipline=Discipline.CIVIL,
        payload={"provenance": "machine_export", "file_hash": results_wb.file_hash,
                 "doc_id": r_doc_id},
    ))

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

    for qr in results_wb.quantity_rows:
        op  = qr.operation_group
        wbs = qr.wbs_item or WBSItem.UNKNOWN
        mat = [{"name": m.name, "values": m.values} for m in qr.materials]

        qnode = store.add_node(EvidenceNode(
            project_id=project_id, node_type="quantity",
            label=f"{wbs}: {op}", discipline=Discipline.CIVIL,
            payload={"wbs_item": wbs, "sheet": qr.sheet, "row": qr.row_index,
                     "values": qr.values, "materials": mat},
        ))
        store.link(project_id, r_node_id, qnode, "has_quantity")

        if CUT_COL and CUT_COL in qr.values:
            bulked = qr.values[CUT_COL]
            bf_val, bf_key = (1.25, "general")
            for m in qr.materials:
                bf_val, bf_key = infer_bulking_factor(m.name)
                break
            insitu = bulked_to_insitu(bulked, bf_val)
            aid = store.next_assumption_id(project_id)
            store.upsert_assumption(Assumption(
                id=aid, project_id=project_id,
                statement=(f"Bulking factor {bf_val} ({bf_key}) for '{op}'. "
                           f"Bulked {bulked:.3f} m3 -> in-situ {insitu:.3f} m3.")))
            store.save_claim(QuantityClaim(
                project_id=project_id, description=f"Cut (in-situ) \u2013 {wbs}: {op}",
                quantity=Quantity(value=round(insitu, 3), unit=Unit.M3),
                method="mudshark.ingest.cut_bulked_to_insitu",
                evidence=[_ref(qr.sheet, qr.row_index, f"{CUT_COL}={bulked}")],
                assumption_ids=[aid],
                workings=[f"Bulked={bulked:.3f} / BF={bf_val} ({bf_key}) = {insitu:.3f}"],
            ))

    for lr in results_wb.linear_rows:
        if lr.sheet != "Trenches":
            continue
        store.save_claim(QuantityClaim(
            project_id=project_id, description=f"Trench length \u2013 {lr.label}",
            quantity=Quantity(value=round(lr.quantity_m, 3), unit=Unit.M),
            method="mudshark.ingest.trench_length",
            evidence=[_ref(lr.sheet, lr.row_index, f"Quantity(m)={lr.quantity_m}")],
        ))

    ts_wb = workbooks.get("Trench_Summary")
    if ts_wb:
        ts_id = store.add_document(project_id,
            file_name=ts_wb.file_name, file_hash=ts_wb.file_hash,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            title="Hand-prepared Trench Summary")
        ts_node_id = store.add_node(EvidenceNode(
            project_id=project_id, node_type="document", label=ts_wb.file_name,
            discipline=Discipline.CIVIL,
            payload={"provenance": "hand_prepared", "doc_id": ts_id}))
        store.link(project_id, ts_node_id, r_node_id, "derived_from")
        for w in ts_wb.warnings:
            log.warning("Trench_Summary: %s", w)
        for qr in ts_wb.quantity_rows:
            store.add_node(EvidenceNode(
                project_id=project_id, node_type="element",
                label=f"TS: {qr.operation_group}", discipline=Discipline.CIVIL,
                payload={"source": "hand_prepared", "sheet": "Trench_Summary",
                          "row": qr.row_index, "values": qr.values}))


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
