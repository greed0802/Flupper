"""Revision diff tests (Phase 5B).

These drive the real route through the real app, against a real SQLite file,
with invented projects, documents, evidence nodes and quantity claims. Nothing
here touches BBX, XLS, XLSX, PDF, a client name or a path outside ``tmp_path``.

The assertions are written to be falsifiable rather than reassuring:

* the snapshot test writes from a *second* connection while the first is inside
  a read snapshot, because a write issued through the shared connection is not
  a concurrency test at all;
* the lock test tries to take the session lock from another thread *while the
  route is running* and requires that attempt to fail;
* the read-only test compares journal length, every row count and the approval
  registry before and after, rather than asserting that nothing "looks wrong";
* the bound test builds more items than the ceiling and requires the response
  to be short *and* to say by how much.

Run from ``backend/`` or the repo root.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import qsagent.api.revisions as revisions_module
from qsagent.api import create_app
from qsagent.api.contracts import (
    MAX_DIFF_AFFECTED_CLAIMS,
    MAX_DIFF_FIELDS,
    MAX_DIFF_ITEMS,
    MAX_DIFF_TEXT_CHARS,
    MAX_DIFF_WARNINGS,
    RevisionChangeDTO,
    RevisionDiffResponse,
    RevisionDocumentDTO,
    RevisionEvidenceDTO,
)
from qsagent.contracts.evidence import (
    Discipline,
    EvidenceNode,
    EvidenceRef,
    Quantity,
    QuantityClaim,
    Unit,
)
from qsagent.revisions import (
    ADDED,
    AMBIGUOUS,
    CHANGED,
    REMOVED,
    UNCHANGED,
    UNRESOLVED,
    canonical_hash,
    canonical_json,
    diff_nodes,
    normalize_bbox,
    normalize_identifier,
    normalize_number,
    normalize_text,
    normalize_unit,
)
from qsagent.revisions.canonical import (
    FLAG_INSUFFICIENT_EVIDENCE,
    FLAG_MALFORMED_EVIDENCE,
    StoredEvidence,
)
from qsagent.revisions.diff import map_affected_claims
from qsagent.runtime import ModelRouter
from qsagent.storage import QSStore, ReadSnapshotError, read_snapshot

API = "/api/v1"

# The token every fixture builds the app with. Not a secret - it exists so the
# suite can prove a *different* value is refused - and it never leaves here.
TEST_TOKEN = "revision-diff-test-token-0123456789"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}

# Invented SHA-256 values. Distinct per role so a mix-up is visible.
HASH_BASE = "a" * 64
HASH_TARGET = "b" * 64
HASH_OTHER = "c" * 64
HASH_UNKNOWN = "d" * 64


class _NoSecrets:
    """Structural ``SecretProvider`` that resolves nothing - no route runs a model."""

    def get_key(self, provider: str) -> str | None:
        return None


@pytest.fixture()
def store(tmp_path):
    # A file, not ``:memory:``: the concurrency test needs a second connection
    # to the same database, which an in-memory store cannot offer.
    st = QSStore(tmp_path / "revision.db")
    yield st
    st.close()


@pytest.fixture()
def app(store, tmp_path):
    return create_app(
        store=store,
        router=ModelRouter(_NoSecrets()),
        api_token=TEST_TOKEN,
        sandbox_root=tmp_path / "sandboxes",
    )


@pytest.fixture()
def client(app):
    with TestClient(app, headers=dict(AUTH_HEADERS)) as c:
        yield c


# --------------------------------------------------------------------------
# Builders - invented data only
# --------------------------------------------------------------------------


def diff_url(project_id: int, base_document_id: int, target_document_id: int) -> str:
    return (
        f"{API}/projects/{project_id}/revisions/diff/"
        f"{base_document_id}/{target_document_id}"
    )


def new_project(store: QSStore, name: str = "synthetic-project") -> int:
    return store.create_project(name, client=None, tender_no=None)


def add_drawing_document(
    store: QSStore,
    project_id: int,
    *,
    file_name: str,
    file_hash: str,
    drawing_no: str | None,
    revision: str | None,
) -> int:
    """A document plus the one node ``ingest/cli._ingest_drawings`` writes.

    That caller passes no ``ref``, so every provenance column is null and the
    only link to the document is ``payload.doc_id``. The builder reproduces
    that exactly, because the diff's document partition has to work on the
    shape the ingest actually produces.
    """
    document_id = store.add_document(
        project_id,
        file_name=file_name,
        file_hash=file_hash,
        media_type="application/pdf",
        discipline="CIVIL",
        drawing_no=drawing_no,
        revision=revision,
        title="Synthetic Sheet",
        page_count=1,
    )
    store.add_node(EvidenceNode(
        project_id=project_id,
        node_type="drawing",
        label=file_name,
        discipline=Discipline.CIVIL,
        payload={
            "kind": "pdf",
            "file_hash": file_hash,
            "doc_id": document_id,
            "has_text_layer": True,
            "title_block_extracted": True,
            "drawing_no": drawing_no,
            "revision": revision,
        },
    ))
    return document_id


def add_element_node(
    store: QSStore,
    project_id: int,
    *,
    file_name: str,
    file_hash: str,
    sheet: str,
    raw_text: str,
    ingest_key: str | None = None,
    zone: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    label: str = "synthetic element",
) -> int:
    """A schema-supported located node - the shape ``add_node`` documents.

    No ingest path in this repository writes one yet, which is exactly why the
    affected-claim mapping has to be tested against it here: it is the only
    shape that can tie a claim to one specific evidence row.
    """
    reference = EvidenceRef(
        file_hash=file_hash,
        file_name=file_name,
        sheet=sheet,
        zone=zone,
        bbox=bbox,
        raw_text=raw_text,
    )
    return store.add_node(
        EvidenceNode(
            project_id=project_id,
            node_type="element",
            label=label,
            discipline=Discipline.CIVIL,
            ref=reference,
            payload={"synthetic": True},
        ),
        ingest_key=ingest_key,
    )


def add_claim(
    store: QSStore,
    project_id: int,
    *,
    claim_id: str,
    file_name: str,
    file_hash: str,
    sheet: str,
    raw_text: str,
    zone: str | None = None,
    value: float = 12.5,
) -> str:
    return store.save_claim(
        QuantityClaim(
            claim_id=claim_id,
            project_id=project_id,
            description="synthetic quantity for revision tests",
            quantity=Quantity(value=value, unit=Unit.M3),
            measurement_state="m3_insitu",
            conversion_applied=False,
            method="test.synthetic",
            evidence=[EvidenceRef(
                file_hash=file_hash,
                file_name=file_name,
                sheet=sheet,
                zone=zone,
                raw_text=raw_text,
            )],
            assumption_ids=[],
            workings=["synthetic"],
        )
    )


# --------------------------------------------------------------------------
# Canonical serialization
# --------------------------------------------------------------------------


class TestNormalization:
    def test_identifiers_fold_case_and_whitespace(self):
        assert normalize_identifier("  C-204  ") == "c-204"
        assert normalize_identifier("REV  3") == "rev 3"
        assert normalize_identifier("c-204") == normalize_identifier("C-204")

    def test_missing_and_empty_are_the_same_thing(self):
        for value in (None, "", "   ", "\t\n"):
            assert normalize_identifier(value) is None
            assert normalize_text(value) is None

    def test_verbatim_text_keeps_case(self):
        # A locator is quoted from a document: case is part of the reading.
        assert normalize_text("DN600") == "DN600"
        assert normalize_text("dn600") != normalize_text("DN600")
        assert normalize_text("  DN600\n") == "DN600"

    def test_numbers_are_rounded_to_a_fixed_precision(self):
        assert normalize_number(1.0000004) == 1.0
        assert normalize_number("2.5") == 2.5
        assert normalize_number(float("nan")) is None
        assert normalize_number(float("inf")) is None
        assert normalize_number(True) is None
        assert normalize_number("not a number") is None

    def test_negative_zero_and_zero_are_one_value(self):
        assert normalize_number(-0.0) == normalize_number(0.0)
        assert repr(normalize_number(-0.0)) == "0.0"

    def test_units_fold_onto_the_canonical_spelling(self):
        assert normalize_unit("M3") == "m3"
        assert normalize_unit("m^3") == "m3"
        assert normalize_unit("Cubic Metre") == "m3"
        assert normalize_unit("each") == "ea"
        assert normalize_unit(None) is None

    def test_bbox_requires_exactly_four_finite_numbers(self):
        assert normalize_bbox("[1,2,3,4]") == (1.0, 2.0, 3.0, 4.0)
        assert normalize_bbox([0, 0, 1.5, 2.5]) == (0.0, 0.0, 1.5, 2.5)
        assert normalize_bbox("[1,2,3]") is None
        assert normalize_bbox("not json") is None
        assert normalize_bbox("[1,2,3,null]") is None
        assert normalize_bbox("{}") is None

    def test_canonical_json_ignores_key_order(self):
        first = canonical_json({"b": [1, 2], "a": {"z": 1, "y": 2}})
        second = canonical_json({"a": {"y": 2, "z": 1}, "b": [1, 2]})
        assert first == second
        assert canonical_hash({"b": 1, "a": 2}) == canonical_hash({"a": 2, "b": 1})

    def test_canonical_json_is_ascii_so_the_digest_is_over_fixed_bytes(self):
        assert canonical_json({"a": "\u00e9"}) == '{"a":"\\u00e9"}'

    def test_the_identity_hash_is_a_64_character_hex_digest(self):
        digest = canonical_hash({"node_type": "element", "sheet": "1"})
        assert len(digest) == 64
        assert all(character in "0123456789abcdef" for character in digest)
        assert digest != canonical_hash({"node_type": "element", "sheet": "2"})


def raw_node_row(**overrides: object) -> dict[str, object]:
    """A row shaped like ``evidence_nodes``, for the pure identity layer.

    The store cannot produce every shape that layer must survive: the ingest
    writes a JSON object payload and never a locator column, so the malformed
    and located shapes are built here and handed to the reader directly.
    """
    row: dict[str, object] = {
        "id": 1,
        "project_id": 1,
        "node_type": "element",
        "label": "synthetic",
        "discipline": "CIVIL",
        "ingest_key": None,
        "file_hash": None,
        "drawing_no": None,
        "revision": None,
        "sheet": None,
        "page": None,
        "zone": None,
        "bbox": None,
        "raw_text": None,
        "payload": "{}",
        "created_at": "2026-01-01T00:00:00.000Z",
    }
    row.update(overrides)
    return row


class TestStoredEvidenceView:
    def test_provenance_is_read_from_the_payload_when_the_columns_are_null(self):
        # The shape every ingest path leaves behind: columns null, payload full.
        view = StoredEvidence.from_row(raw_node_row(
            node_type="drawing",
            label="synthetic.pdf",
            payload=json.dumps({
                "kind": "pdf", "file_hash": HASH_BASE, "doc_id": 7,
                "drawing_no": "C-204", "revision": "A", "sheet": "Sheet 1",
            }),
        ))
        assert view.file_hash == HASH_BASE
        assert view.drawing_no == "C-204"
        assert view.revision == "A"
        assert view.sheet == "Sheet 1"
        assert view.reliable is True
        assert view.has_locator is False

    def test_a_column_outranks_the_payload(self):
        view = StoredEvidence.from_row(raw_node_row(
            sheet="column sheet",
            payload=json.dumps({"file_hash": HASH_BASE, "sheet": "payload sheet"}),
        ))
        assert view.sheet == "column sheet"

    def test_file_hash_is_provenance_and_is_not_compared(self):
        base = StoredEvidence.from_row(raw_node_row(
            payload=json.dumps({"file_hash": HASH_BASE, "doc_id": 1, "sheet": "S1"})
        ))
        target = StoredEvidence.from_row(raw_node_row(
            payload=json.dumps({"file_hash": HASH_TARGET, "doc_id": 2, "sheet": "S1"})
        ))
        assert base.file_hash != target.file_hash
        assert base.content == target.content

    def test_a_row_with_nothing_comparable_is_unresolved(self):
        view = StoredEvidence.from_row(raw_node_row(
            payload=json.dumps({"file_hash": HASH_BASE, "doc_id": 1})
        ))
        assert view.comparable is False
        assert view.reliable is False
        assert view.reason == FLAG_INSUFFICIENT_EVIDENCE

    def test_a_payload_that_does_not_decode_is_reported_not_repaired(self):
        view = StoredEvidence.from_row(raw_node_row(payload="{not json"))
        assert view.payload is None
        assert view.reliable is False
        assert view.reason == FLAG_MALFORMED_EVIDENCE

    def test_a_bbox_that_is_not_four_numbers_is_reported_not_repaired(self):
        view = StoredEvidence.from_row(raw_node_row(bbox="[1,2,3]"))
        assert view.bbox is None
        assert view.reliable is False
        assert view.reason == FLAG_MALFORMED_EVIDENCE

    def test_the_pairing_key_excludes_everything_a_revision_changes(self):
        row = raw_node_row(sheet="S1", zone="C4", payload=json.dumps({"sheet": "S1"}))
        base = StoredEvidence.from_row(row)
        target = StoredEvidence.from_row(raw_node_row(
            id=2, label="renamed", revision="B", file_hash=HASH_TARGET,
            sheet="s1 ", zone=" c4", raw_text="DN600", payload=json.dumps({"sheet": "S1"}),
        ))
        assert base.pairing_key == target.pairing_key
        assert base.identity_id == target.identity_id


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


class TestDiffNodes:
    def test_a_footprint_only_in_the_target_is_added(self):
        records = diff_nodes([], [raw_node_row(sheet="S1", raw_text="DN600")])
        assert [record.status for record in records] == [ADDED]
        assert records[0].base is None
        assert records[0].target is not None

    def test_a_footprint_only_in_the_base_is_removed(self):
        records = diff_nodes([raw_node_row(sheet="S1", raw_text="DN600")], [])
        assert [record.status for record in records] == [REMOVED]
        assert records[0].target is None

    def test_a_changed_locator_is_changed_not_replaced(self):
        records = diff_nodes(
            [raw_node_row(sheet="S1", raw_text="DN600")],
            [raw_node_row(id=2, sheet="S1", raw_text="DN900")],
        )
        assert [record.status for record in records] == [CHANGED]
        assert "raw_text" in records[0].changed_fields

    def test_a_payload_key_change_is_named(self):
        records = diff_nodes(
            [raw_node_row(payload=json.dumps({"file_hash": HASH_BASE, "wbs": "1.2"}))],
            [raw_node_row(id=2, payload=json.dumps({"file_hash": HASH_TARGET, "wbs": "1.3"}))],
        )
        assert [record.status for record in records] == [CHANGED]
        assert records[0].changed_fields == ("payload.wbs",)

    def test_identical_content_in_a_new_file_is_unchanged(self):
        records = diff_nodes(
            [raw_node_row(sheet="S1", raw_text="DN600",
                          payload=json.dumps({"file_hash": HASH_BASE, "doc_id": 1}))],
            [raw_node_row(id=2, sheet="S1", raw_text="DN600",
                          payload=json.dumps({"file_hash": HASH_TARGET, "doc_id": 2}))],
        )
        assert [record.status for record in records] == [UNCHANGED]
        assert records[0].changed_fields == ()

    def test_whitespace_and_case_are_not_a_change(self):
        records = diff_nodes(
            [raw_node_row(sheet="S1", revision="A", raw_text="  DN600 ")],
            [raw_node_row(id=2, sheet="s1", revision="a", raw_text="DN600")],
        )
        assert [record.status for record in records] == [UNCHANGED]

    def test_duplicate_footprints_without_a_stable_key_are_ambiguous(self):
        base = [
            raw_node_row(id=1, sheet="S1", raw_text="A"),
            raw_node_row(id=2, sheet="S1", raw_text="B"),
        ]
        target = [
            raw_node_row(id=3, sheet="S1", raw_text="A2"),
            raw_node_row(id=4, sheet="S1", raw_text="B2"),
        ]
        records = diff_nodes(base, target)
        assert [record.status for record in records] == [AMBIGUOUS] * 4
        assert {record.reason for record in records} == {"ambiguous_identity"}
        assert {record.group_size for record in records} == {4}

    def test_a_stable_ingest_key_pairs_duplicates_across_revisions(self):
        base = [
            raw_node_row(id=1, sheet="S1", raw_text="A", ingest_key="k1"),
            raw_node_row(id=2, sheet="S1", raw_text="B", ingest_key="k2"),
        ]
        target = [
            raw_node_row(id=3, sheet="S1", raw_text="A", ingest_key="k1"),
            raw_node_row(id=4, sheet="S1", raw_text="B2", ingest_key="k2"),
        ]
        records = diff_nodes(base, target)
        assert [record.status for record in records] == [CHANGED, UNCHANGED]
        assert AMBIGUOUS not in {record.status for record in records}

    def test_rows_are_never_paired_across_node_types(self):
        records = diff_nodes(
            [raw_node_row(node_type="quantity", sheet="S1", raw_text="A")],
            [raw_node_row(id=2, node_type="element", sheet="S1", raw_text="A")],
        )
        assert sorted(record.status for record in records) == [ADDED, REMOVED]

    def test_insufficient_evidence_is_unresolved_on_either_side(self):
        base = [raw_node_row(sheet="S1", payload=json.dumps({"file_hash": HASH_BASE}))]
        target = [raw_node_row(id=2, sheet="S1", raw_text="DN600")]
        records = diff_nodes(base, target)
        assert [record.status for record in records] == [UNRESOLVED]
        assert records[0].reason == FLAG_INSUFFICIENT_EVIDENCE

    def test_malformed_evidence_is_unresolved(self):
        records = diff_nodes([raw_node_row(sheet="S1", payload="not json")], [])
        assert [record.status for record in records] == [UNRESOLVED]
        assert records[0].reason == FLAG_MALFORMED_EVIDENCE

    def test_the_record_order_does_not_depend_on_the_input_order(self):
        base = [
            raw_node_row(id=1, sheet="S1", raw_text="A"),
            raw_node_row(id=2, sheet="S2", raw_text="B"),
            raw_node_row(id=3, sheet="S3", raw_text="C"),
        ]
        target = [
            raw_node_row(id=4, sheet="S1", raw_text="A2"),
            raw_node_row(id=5, sheet="S9", raw_text="Z"),
        ]
        forward = diff_nodes(base, target)
        backward = diff_nodes(list(reversed(base)), list(reversed(target)))
        assert [(r.status, r.identity_id) for r in forward] == [
            (r.status, r.identity_id) for r in backward
        ]


def reference(**overrides: object) -> str:
    """One serialised ``EvidenceRef`` as ``quantity_claims.evidence`` holds it."""
    payload: dict[str, object] = {
        "file_hash": HASH_BASE,
        "file_name": "synthetic.xls",
        "sheet": "S1",
        "raw_text": "DN600",
    }
    payload.update(overrides)
    return json.dumps([payload])


class TestAffectedClaims:
    def changed_record_pair(self):
        return diff_nodes(
            [raw_node_row(id=1, sheet="S1", raw_text="DN600", file_hash=HASH_BASE)],
            [raw_node_row(id=2, sheet="S1", raw_text="DN900", file_hash=HASH_TARGET)],
        )

    def test_a_claim_citing_a_changed_row_is_affected(self):
        records = self.changed_record_pair()
        mapping = map_affected_claims(
            records,
            [("Q-1", reference())],
            {HASH_BASE: "synthetic.xls", HASH_TARGET: "synthetic.xls"},
        )
        assert mapping.matches[records[0].identity_id] == ("Q-1",)
        assert mapping.unassociated_claims == 0
        assert mapping.checked_claims == 1

    def test_a_partial_match_is_not_a_match(self):
        # Same sheet, same hash, same file name - a different reading. The
        # locator is what identifies the row, so this must not associate.
        records = self.changed_record_pair()
        mapping = map_affected_claims(
            records,
            [("Q-1", reference(raw_text="DN6000"))],
            {HASH_BASE: "synthetic.xls"},
        )
        assert mapping.matches == {}
        assert mapping.unassociated_claims == 1

    def test_a_row_without_a_locator_is_counted_not_associated(self):
        # The shape the ingest produces: real content in the payload, no
        # locator column. The change is reported; the association is not.
        records = diff_nodes(
            [raw_node_row(
                id=1, sheet="S1",
                payload=json.dumps({"file_hash": HASH_BASE, "wbs": "1.2"}),
            )],
            [raw_node_row(
                id=2, sheet="S1",
                payload=json.dumps({"file_hash": HASH_TARGET, "wbs": "1.3"}),
            )],
        )
        assert [record.status for record in records] == [CHANGED]
        mapping = map_affected_claims(
            records,
            [("Q-1", reference())],
            {HASH_BASE: "synthetic.xls"},
        )
        assert mapping.matches == {}
        assert mapping.unassociated_claims == 1

    def test_a_claim_citing_another_file_is_untouched(self):
        records = self.changed_record_pair()
        mapping = map_affected_claims(
            records,
            [("Q-1", reference(file_hash=HASH_OTHER, file_name="other.xls"))],
            {HASH_BASE: "synthetic.xls", HASH_OTHER: "other.xls"},
        )
        assert mapping.matches == {}
        assert mapping.unassociated_claims == 0

    def test_an_unchanged_row_affects_nothing(self):
        records = diff_nodes(
            [raw_node_row(id=1, sheet="S1", raw_text="DN600", file_hash=HASH_BASE)],
            [raw_node_row(id=2, sheet="S1", raw_text="DN600", file_hash=HASH_TARGET)],
        )
        assert [record.status for record in records] == [UNCHANGED]
        mapping = map_affected_claims(
            records, [("Q-1", reference())], {HASH_BASE: "synthetic.xls"}
        )
        assert mapping.matches == {}

    def test_a_reference_without_a_document_row_cannot_be_verified(self):
        records = self.changed_record_pair()
        mapping = map_affected_claims(records, [("Q-1", reference())], {})
        assert mapping.matches == {}
        assert mapping.unverified_references == 1

    def test_a_reference_with_no_source_hash_is_not_usable(self):
        records = self.changed_record_pair()
        mapping = map_affected_claims(
            records,
            [("Q-1", json.dumps([{"file_name": "synthetic.xls", "sheet": "S1"}]))],
            {HASH_BASE: "synthetic.xls"},
        )
        assert mapping.malformed_claims == 0
        assert mapping.checked_claims == 0
        assert mapping.unassociated_claims == 0

    def test_an_unreadable_evidence_column_is_counted(self):
        records = self.changed_record_pair()
        mapping = map_affected_claims(records, [("Q-1", "{not json")], {})
        assert mapping.malformed_claims == 1
        assert mapping.checked_claims == 0


# --------------------------------------------------------------------------
# Route surface
# --------------------------------------------------------------------------


def served_paths(app) -> set[str]:
    """Every path the real app serves, read from its own routing table."""
    found: set[str] = set()
    stack = list(getattr(app, "routes", ()))
    while stack:
        route = stack.pop()
        inner = getattr(route, "original_router", None)
        if inner is not None:
            stack.extend(getattr(inner, "routes", ()))
            continue
        path = getattr(route, "path", None)
        if path:
            found.add(str(path))
    return found


class TestRouteSurface:
    def test_the_route_is_served_under_the_authenticated_prefix(self, app):
        expected = (
            f"{API}/projects/{{project_id}}/revisions/diff/"
            "{base_document_id}/{target_document_id}"
        )
        assert expected in served_paths(app)

    def test_no_revision_route_accepts_client_supplied_provenance(self, app):
        # A hash, a file name or a path in the route would be a way to nominate
        # which file is compared, or to claim a hash is authoritative.
        forbidden = ("file_hash", "hash", "file_name", "filename", "path", "revision")
        offenders = [
            path
            for path in served_paths(app)
            if "/revisions/" in path
            and any(f"{{{name}}}" in path for name in forbidden)
        ]
        assert offenders == []

    def test_every_revision_path_parameter_is_an_integer(self, app):
        schema = app.openapi()
        path = (
            f"{API}/projects/{{project_id}}/revisions/diff/"
            "{base_document_id}/{target_document_id}"
        )
        parameters = schema["paths"][path]["get"]["parameters"]
        assert {parameter["name"] for parameter in parameters} == {
            "project_id",
            "base_document_id",
            "target_document_id",
        }
        assert {parameter["schema"]["type"] for parameter in parameters} == {"integer"}

    def test_a_non_integer_identifier_is_refused_before_any_read(self, client, store):
        project_id = new_project(store)
        response = client.get(f"{API}/projects/{project_id}/revisions/diff/abc/2")
        assert response.status_code == 422
        assert response.json()["error"] == "RequestValidationError"

    def test_the_route_requires_a_bearer_token(self, app, store):
        project_id = new_project(store)
        without_token = TestClient(app)
        response = without_token.get(diff_url(project_id, 1, 2))
        assert response.status_code == 401
        assert TEST_TOKEN not in response.text

    def test_the_route_refuses_a_different_token(self, app, store):
        project_id = new_project(store)
        wrong = TestClient(app, headers={"Authorization": "Bearer not-the-token"})
        assert wrong.get(diff_url(project_id, 1, 2)).status_code == 401


class TestNotFoundAndRefusal:
    def test_an_unknown_project_is_a_404(self, client, store):
        project_id = new_project(store)
        response = client.get(diff_url(project_id + 999, 1, 2))
        assert response.status_code == 404
        assert response.json()["message"] == "unknown project"

    def test_an_unknown_base_document_is_a_404(self, client, store):
        project_id = new_project(store)
        target_doc = add_drawing_document(
            store, project_id, file_name="synthetic.pdf", file_hash=HASH_TARGET,
            drawing_no="C-204", revision="B",
        )
        response = client.get(diff_url(project_id, 9999, target_doc))
        assert response.status_code == 404
        assert response.json()["message"] == "unknown base document for this project"

    def test_an_unknown_target_document_is_a_404(self, client, store):
        project_id = new_project(store)
        base_doc = add_drawing_document(
            store, project_id, file_name="synthetic.pdf", file_hash=HASH_BASE,
            drawing_no="C-204", revision="A",
        )
        response = client.get(diff_url(project_id, base_doc, 9999))
        assert response.status_code == 404
        assert response.json()["message"] == "unknown target document for this project"

    def test_a_document_from_another_project_is_a_404_not_a_cross_project_read(
        self, client, store
    ):
        first = new_project(store, "synthetic-project-one")
        second = new_project(store, "synthetic-project-two")
        base_doc = add_drawing_document(
            store, first, file_name="synthetic.pdf", file_hash=HASH_BASE,
            drawing_no="C-204", revision="A",
        )
        other_doc = add_drawing_document(
            store, second, file_name="synthetic.pdf", file_hash=HASH_TARGET,
            drawing_no="C-204", revision="B",
        )
        response = client.get(diff_url(first, base_doc, other_doc))
        assert response.status_code == 404
        assert response.json()["message"] == "unknown target document for this project"

    def test_one_document_against_itself_is_refused(self, client, store):
        project_id = new_project(store)
        base_doc = add_drawing_document(
            store, project_id, file_name="synthetic.pdf", file_hash=HASH_BASE,
            drawing_no="C-204", revision="A",
        )
        response = client.get(diff_url(project_id, base_doc, base_doc))
        assert response.status_code == 400
        assert response.json()["message"] == (
            "base and target document are the same document"
        )

    def test_different_drawings_are_refused(self, client, store):
        project_id = new_project(store)
        base_doc = add_drawing_document(
            store, project_id, file_name="synthetic.pdf", file_hash=HASH_BASE,
            drawing_no="C-204", revision="A",
        )
        target_doc = add_drawing_document(
            store, project_id, file_name="synthetic.pdf", file_hash=HASH_TARGET,
            drawing_no="C-999", revision="B",
        )
        response = client.get(diff_url(project_id, base_doc, target_doc))
        assert response.status_code == 400
        assert response.json()["message"] == (
            "base and target document are different drawings"
        )

    def test_a_document_with_no_source_hash_is_refused(self, client, store):
        # A document row with an empty hash cannot be one side of a comparison:
        # there is nothing to partition its evidence by.
        project_id = new_project(store)
        empty_doc = store.add_document(
            project_id, file_name="synthetic-unhashed.pdf", file_hash="",
            discipline="CIVIL", drawing_no="C-204", revision="A",
        )
        target_doc = add_drawing_document(
            store, project_id, file_name="synthetic.pdf", file_hash=HASH_TARGET,
            drawing_no="C-204", revision="B",
        )
        response = client.get(diff_url(project_id, empty_doc, target_doc))
        assert response.status_code == 400
        assert response.json()["message"] == (
            "base and target document do not name two distinct source files"
        )

    def test_a_document_of_another_project_is_not_reachable_by_id(self, client, store):
        first = new_project(store, "synthetic-project-one")
        second = new_project(store, "synthetic-project-two")
        doc = add_drawing_document(
            store, second, file_name="synthetic.pdf", file_hash=HASH_BASE,
            drawing_no="C-204", revision="A",
        )
        # The document id is real; the project it is asked about is a different one.
        response = client.get(diff_url(first, doc, doc + 1))
        assert response.status_code == 404


# --------------------------------------------------------------------------
# Comparison through the route
# --------------------------------------------------------------------------


class TestRevisionComparison:
    def test_two_revisions_of_one_drawing_are_compared(self, client, store):
        project_id = new_project(store)
        base_doc = add_drawing_document(
            store, project_id, file_name="synthetic-c204-a.pdf",
            file_hash=HASH_BASE, drawing_no="C-204", revision="A",
        )
        target_doc = add_drawing_document(
            store, project_id, file_name="synthetic-c204-b.pdf",
            file_hash=HASH_TARGET, drawing_no="C-204", revision="B",
        )

        response = client.get(diff_url(project_id, base_doc, target_doc))
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["relationship"] == "same_drawing"
        assert body["counts"]["changed"] == 1
        assert body["counts"]["added"] == 0
        assert body["base"]["document_id"] == base_doc
        assert body["target"]["document_id"] == target_doc
        assert body["base"]["file_hash"] == HASH_BASE
        assert body["target"]["file_hash"] == HASH_TARGET
        # The drawing identity is read back out of the payload, because the
        # columns are null for every row the ingest writes.
        assert body["base"]["revision"] == "A"
        assert body["target"]["revision"] == "B"
        assert body["base"]["evidence_rows"] == 1

        item = body["items"][0]
        assert item["status"] == "changed"
        assert set(item["changed_fields"]) == {
            "label", "revision", "payload.revision",
        }
        assert item["base"]["node_id"] != item["target"]["node_id"]
        assert item["base"]["file_hash"] == HASH_BASE
        assert item["target"]["file_hash"] == HASH_TARGET

    def test_a_drawing_with_no_drawing_number_is_reported_unverified(
        self, client, store
    ):
        project_id = new_project(store)
        base_doc = add_drawing_document(
            store, project_id, file_name="synthetic.pdf", file_hash=HASH_BASE,
            drawing_no=None, revision=None,
        )
        target_doc = add_drawing_document(
            store, project_id, file_name="synthetic-v2.pdf", file_hash=HASH_TARGET,
            drawing_no=None, revision=None,
        )
        body = client.get(diff_url(project_id, base_doc, target_doc)).json()
        assert body["relationship"] == "unverified_drawing"
        assert "drawing_identity_unverified" in body["warnings"]

    def test_an_unchanged_redraw_is_reported_unchanged(self, client, store):
        project_id = new_project(store)
        base_doc = add_drawing_document(
            store, project_id, file_name="synthetic.pdf", file_hash=HASH_BASE,
            drawing_no="C-204", revision="A",
        )
        target_doc = add_drawing_document(
            store, project_id, file_name="synthetic.pdf", file_hash=HASH_TARGET,
            drawing_no="C-204", revision="A",
        )
        body = client.get(diff_url(project_id, base_doc, target_doc)).json()
        assert body["counts"]["unchanged"] == 1
        assert body["items"][0]["changed_fields"] == []

    def test_added_and_removed_elements_are_reported(self, client, store):
        project_id = new_project(store)
        base_doc = store.add_document(
            project_id, file_name="synthetic.xls", file_hash=HASH_BASE,
            discipline="CIVIL", drawing_no="C-204", revision="A",
        )
        target_doc = store.add_document(
            project_id, file_name="synthetic.xls", file_hash=HASH_TARGET,
            discipline="CIVIL", drawing_no="C-204", revision="B",
        )
        add_element_node(
            store, project_id, file_name="synthetic.xls", file_hash=HASH_BASE,
            sheet="Only in the base", raw_text="DN600",
        )
        add_element_node(
            store, project_id, file_name="synthetic.xls", file_hash=HASH_TARGET,
            sheet="Only in the target", raw_text="DN900",
        )

        body = client.get(diff_url(project_id, base_doc, target_doc)).json()
        assert body["counts"] == {
            "added": 1, "removed": 1, "changed": 0,
            "unchanged": 0, "ambiguous": 0, "unresolved": 0,
        }
        assert {item["status"] for item in body["items"]} == {"added", "removed"}

    def test_a_document_with_no_evidence_rows_is_not_reported_as_all_removed(
        self, client, store
    ):
        # A base revision whose rows were superseded in place leaves nothing of
        # its own behind. Classifying that as a full set of removals would
        # invent a finding, so nothing is classified at all.
        project_id = new_project(store)
        base_doc = store.add_document(
            project_id, file_name="synthetic.xls", file_hash=HASH_BASE,
            discipline="CIVIL", drawing_no="C-204", revision="A",
        )
        target_doc = store.add_document(
            project_id, file_name="synthetic.xls", file_hash=HASH_TARGET,
            discipline="CIVIL", drawing_no="C-204", revision="B",
        )
        add_element_node(
            store, project_id, file_name="synthetic.xls", file_hash=HASH_TARGET,
            sheet="S1", raw_text="DN600",
        )
        body = client.get(diff_url(project_id, base_doc, target_doc)).json()
        assert body["relationship"] == "evidence_unavailable"
        assert body["items"] == []
        assert body["counts"]["removed"] == 0
        assert "base_document_has_no_evidence_rows" in body["warnings"]

    def test_a_duplicate_footprint_is_reported_ambiguous(self, client, store):
        project_id = new_project(store)
        base_doc = store.add_document(
            project_id, file_name="synthetic.xls", file_hash=HASH_BASE,
            discipline="CIVIL", drawing_no="C-204", revision="A",
        )
        target_doc = store.add_document(
            project_id, file_name="synthetic.xls", file_hash=HASH_TARGET,
            discipline="CIVIL", drawing_no="C-204", revision="B",
        )
        for raw_text in ("DN600", "DN900"):
            add_element_node(
                store, project_id, file_name="synthetic.xls", file_hash=HASH_BASE,
                sheet="S1", raw_text=raw_text,
            )
            add_element_node(
                store, project_id, file_name="synthetic.xls", file_hash=HASH_TARGET,
                sheet="S1", raw_text=raw_text,
            )

        body = client.get(diff_url(project_id, base_doc, target_doc)).json()
        assert body["counts"]["ambiguous"] == 4
        assert {item["reason"] for item in body["items"]} == {"ambiguous_identity"}
        assert all(item["group_size"] == 4 for item in body["items"])
        assert all(item["affected_claim_ids"] == [] for item in body["items"])


# --------------------------------------------------------------------------
# Affected claims through the route
# --------------------------------------------------------------------------


def located_revision(store, project_id: int, *, base_text: str, target_text: str):
    """Two revisions carrying located evidence, plus the documents they cite."""
    base_doc = store.add_document(
        project_id, file_name="synthetic.xls", file_hash=HASH_BASE,
        discipline="CIVIL", drawing_no="C-204", revision="A",
    )
    target_doc = store.add_document(
        project_id, file_name="synthetic.xls", file_hash=HASH_TARGET,
        discipline="CIVIL", drawing_no="C-204", revision="B",
    )
    add_element_node(
        store, project_id, file_name="synthetic.xls", file_hash=HASH_BASE,
        sheet="S1", raw_text=base_text,
    )
    add_element_node(
        store, project_id, file_name="synthetic.xls", file_hash=HASH_TARGET,
        sheet="S1", raw_text=target_text,
    )
    return base_doc, target_doc


class TestAffectedClaimsThroughTheRoute:
    def test_a_claim_citing_a_changed_row_is_named(self, client, store):
        project_id = new_project(store)
        base_doc, target_doc = located_revision(
            store, project_id, base_text="DN600", target_text="DN900"
        )
        add_claim(
            store, project_id, claim_id="Q-1", file_name="synthetic.xls",
            file_hash=HASH_BASE, sheet="S1", raw_text="DN600",
        )

        body = client.get(diff_url(project_id, base_doc, target_doc)).json()
        assert body["counts"]["changed"] == 1
        assert body["items"][0]["affected_claim_ids"] == ["Q-1"]
        assert body["items"][0]["affected_claims_omitted"] == 0
        assert body["unassociated_claims"] == 0
        assert body["checked_claims"] == 1
        assert (
            "claims_not_associated_with_a_specific_evidence_row"
            not in body["warnings"]
        )

    def test_a_claim_that_cites_the_document_but_no_row_is_counted_unassociated(
        self, client, store
    ):
        # The shape the ingest actually produces: the node carries its
        # provenance in the payload and no locator column, so there is nothing
        # tying this claim to one row. It is counted, never associated.
        project_id = new_project(store)
        base_doc = add_drawing_document(
            store, project_id, file_name="synthetic.xls", file_hash=HASH_BASE,
            drawing_no="C-204", revision="A",
        )
        target_doc = add_drawing_document(
            store, project_id, file_name="synthetic.xls", file_hash=HASH_TARGET,
            drawing_no="C-204", revision="B",
        )
        add_claim(
            store, project_id, claim_id="Q-1", file_name="synthetic.xls",
            file_hash=HASH_BASE, sheet="S1", raw_text="DN600",
        )

        body = client.get(diff_url(project_id, base_doc, target_doc)).json()
        assert body["items"][0]["affected_claim_ids"] == []
        assert body["unassociated_claims"] == 1
        assert (
            "claims_not_associated_with_a_specific_evidence_row" in body["warnings"]
        )

    def test_a_claim_citing_an_unknown_file_is_counted_and_not_matched(
        self, client, store
    ):
        project_id = new_project(store)
        base_doc, target_doc = located_revision(
            store, project_id, base_text="DN600", target_text="DN900"
        )
        add_claim(
            store, project_id, claim_id="Q-1", file_name="synthetic.xls",
            file_hash=HASH_UNKNOWN, sheet="S1", raw_text="DN600",
        )

        body = client.get(diff_url(project_id, base_doc, target_doc)).json()
        assert body["items"][0]["affected_claim_ids"] == []
        assert body["unverified_claim_references"] == 1
        assert body["unassociated_claims"] == 0
        assert "claim_references_without_document_lineage" in body["warnings"]

    def test_a_claim_citing_an_unchanged_row_is_not_named(self, client, store):
        project_id = new_project(store)
        base_doc, target_doc = located_revision(
            store, project_id, base_text="DN600", target_text="DN600"
        )
        add_claim(
            store, project_id, claim_id="Q-1", file_name="synthetic.xls",
            file_hash=HASH_BASE, sheet="S1", raw_text="DN600",
        )
        body = client.get(diff_url(project_id, base_doc, target_doc)).json()
        assert body["counts"]["unchanged"] == 1
        assert body["items"][0]["affected_claim_ids"] == []
        assert body["unassociated_claims"] == 0

    def test_a_claim_from_another_project_is_not_named(self, client, store):
        project_id = new_project(store, "synthetic-project-one")
        other_project = new_project(store, "synthetic-project-two")
        store.add_document(
            other_project, file_name="synthetic.xls", file_hash=HASH_OTHER,
            discipline="CIVIL", drawing_no="C-204", revision="A",
        )
        add_claim(
            store, other_project, claim_id="Q-OTHER", file_name="synthetic.xls",
            file_hash=HASH_OTHER, sheet="S1", raw_text="DN600",
        )
        base_doc, target_doc = located_revision(
            store, project_id, base_text="DN600", target_text="DN900"
        )
        body = client.get(diff_url(project_id, base_doc, target_doc)).json()
        assert body["items"][0]["affected_claim_ids"] == []
        assert body["checked_claims"] == 0

    def test_no_claim_detail_is_echoed_back(self, client, store):
        # The response names claims; it does not summarise them. A quantity, a
        # measurement state or a conversion flag in this body would be a claim
        # about a claim, and this route makes none.
        project_id = new_project(store)
        base_doc, target_doc = located_revision(
            store, project_id, base_text="DN600", target_text="DN900"
        )
        add_claim(
            store, project_id, claim_id="Q-1", file_name="synthetic.xls",
            file_hash=HASH_BASE, sheet="S1", raw_text="DN600", value=9876.5,
        )
        text = client.get(diff_url(project_id, base_doc, target_doc)).text
        assert "9876.5" not in text
        assert "m3_insitu" not in text
        # The body carries exactly the declared fields - no smuggled status, no
        # approval outcome, no checkmate verdict.
        assert set(json.loads(text)) == set(RevisionDiffResponse.model_fields)
        assert "approved" not in text.lower()
        assert "checkmate" not in text.lower()


# --------------------------------------------------------------------------
# Bounds
# --------------------------------------------------------------------------

TABLES = (
    "projects",
    "documents",
    "evidence_nodes",
    "evidence_edges",
    "assumptions",
    "quantity_claims",
    "tool_runs",
    "audit_journal",
    "checkmate_results",
)


def table_counts(store: QSStore) -> dict[str, int]:
    return {
        table: store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in TABLES
    }


def revision_pair(store, project_id: int):
    """A base and a target document in one project, with no evidence yet."""
    base_doc = store.add_document(
        project_id, file_name="synthetic.xls", file_hash=HASH_BASE,
        discipline="CIVIL", drawing_no="C-204", revision="A",
    )
    target_doc = store.add_document(
        project_id, file_name="synthetic.xls", file_hash=HASH_TARGET,
        discipline="CIVIL", drawing_no="C-204", revision="B",
    )
    return base_doc, target_doc


class TestBounds:
    def test_the_item_list_is_capped_and_says_by_how_much(self, client, store):
        project_id = new_project(store)
        base_doc, target_doc = revision_pair(store, project_id)
        per_side = MAX_DIFF_ITEMS // 2 + 10
        for index in range(per_side):
            add_element_node(
                store, project_id, file_name="synthetic.xls", file_hash=HASH_BASE,
                sheet=f"Base sheet {index}", raw_text="DN600",
            )
            add_element_node(
                store, project_id, file_name="synthetic.xls", file_hash=HASH_TARGET,
                sheet=f"Target sheet {index}", raw_text="DN900",
            )

        body = client.get(diff_url(project_id, base_doc, target_doc)).json()
        total = per_side * 2
        assert body["counts"]["removed"] == per_side
        assert body["counts"]["added"] == per_side
        assert len(body["items"]) == MAX_DIFF_ITEMS
        assert body["items_truncated"] is True
        assert body["items_omitted"] == total - MAX_DIFF_ITEMS

    def test_a_long_locator_is_clipped_and_flagged(self, client, store):
        project_id = new_project(store)
        base_doc, target_doc = located_revision(
            store, project_id, base_text="D" * 400, target_text="E" * 400
        )
        body = client.get(diff_url(project_id, base_doc, target_doc)).json()
        item = body["items"][0]
        assert len(item["base"]["raw_text"]) == MAX_DIFF_TEXT_CHARS
        assert item["base"]["raw_text_truncated"] is True
        assert len(item["target"]["raw_text"]) == MAX_DIFF_TEXT_CHARS

    def test_the_affected_claim_list_is_capped_and_says_by_how_much(
        self, client, store
    ):
        project_id = new_project(store)
        base_doc, target_doc = located_revision(
            store, project_id, base_text="DN600", target_text="DN900"
        )
        for index in range(MAX_DIFF_AFFECTED_CLAIMS + 5):
            add_claim(
                store, project_id, claim_id=f"Q-{index:03d}",
                file_name="synthetic.xls", file_hash=HASH_BASE,
                sheet="S1", raw_text="DN600",
            )
        item = client.get(diff_url(project_id, base_doc, target_doc)).json()["items"][0]
        assert len(item["affected_claim_ids"]) == MAX_DIFF_AFFECTED_CLAIMS
        assert item["affected_claims_omitted"] == 5

    def test_the_changed_field_list_is_capped(self, client, store):
        project_id = new_project(store)
        base_doc, target_doc = revision_pair(store, project_id)
        wide = {f"field_{index:03d}": index for index in range(MAX_DIFF_FIELDS + 20)}
        for file_hash, marker in ((HASH_BASE, 0), (HASH_TARGET, 1)):
            store.add_node(EvidenceNode(
                project_id=project_id,
                node_type="element",
                label="wide payload",
                discipline=Discipline.CIVIL,
                ref=EvidenceRef(
                    file_hash=file_hash, file_name="synthetic.xls",
                    sheet="S1", raw_text="DN600",
                ),
                payload={**wide, "file_hash": file_hash, "marker": marker},
            ))
        item = client.get(diff_url(project_id, base_doc, target_doc)).json()["items"][0]
        assert len(item["changed_fields"]) <= MAX_DIFF_FIELDS

    def test_the_warning_list_is_capped(self, client, store):
        project_id = new_project(store)
        base_doc = add_drawing_document(
            store, project_id, file_name="synthetic.pdf", file_hash=HASH_BASE,
            drawing_no=None, revision=None,
        )
        target_doc = add_drawing_document(
            store, project_id, file_name="synthetic-2.pdf", file_hash=HASH_TARGET,
            drawing_no=None, revision=None,
        )
        add_claim(
            store, project_id, claim_id="Q-1", file_name="synthetic.pdf",
            file_hash=HASH_UNKNOWN, sheet="S1", raw_text="DN600",
        )
        body = client.get(diff_url(project_id, base_doc, target_doc)).json()
        assert 0 < len(body["warnings"]) <= MAX_DIFF_WARNINGS

    def test_every_text_and_list_field_on_the_response_is_bounded(self):
        from typing import get_args

        def holds(annotation, kind) -> bool:
            if annotation is kind:
                return True
            return any(holds(argument, kind) for argument in get_args(annotation))

        def has_max_length(field) -> bool:
            return any(
                getattr(constraint, "max_length", None) is not None
                for constraint in field.metadata
            )

        models = (
            RevisionDiffResponse,
            RevisionDocumentDTO,
            RevisionEvidenceDTO,
            RevisionChangeDTO,
        )
        unbounded: list[str] = []
        for model in models:
            for name, field in model.model_fields.items():
                annotation = field.annotation
                if not (holds(annotation, str) or holds(annotation, list)):
                    continue
                if not has_max_length(field):
                    unbounded.append(f"{model.__name__}.{name}")
        assert unbounded == []


# --------------------------------------------------------------------------
# Read-only behaviour
# --------------------------------------------------------------------------


class TestReadOnly:
    def test_a_diff_writes_nothing_and_gates_nothing(self, client, store, app):
        project_id = new_project(store)
        base_doc, target_doc = located_revision(
            store, project_id, base_text="DN600", target_text="DN900"
        )
        add_claim(
            store, project_id, claim_id="Q-1", file_name="synthetic.xls",
            file_hash=HASH_BASE, sheet="S1", raw_text="DN600",
        )
        store.save_checkmate(project_id, "synthetic-subject", False, [])

        before_rows = table_counts(store)
        before_journal = len(store.journal_entries())
        before_approvals = len(app.state.approvals)
        assert store.verify_journal() is True

        assert client.get(diff_url(project_id, base_doc, target_doc)).status_code == 200

        assert table_counts(store) == before_rows
        assert len(store.journal_entries()) == before_journal
        assert len(app.state.approvals) == before_approvals
        assert store.verify_journal() is True

    def test_a_claim_keeps_its_measurement_state(self, client, store):
        project_id = new_project(store)
        base_doc, target_doc = located_revision(
            store, project_id, base_text="DN600", target_text="DN900"
        )
        add_claim(
            store, project_id, claim_id="Q-1", file_name="synthetic.xls",
            file_hash=HASH_BASE, sheet="S1", raw_text="DN600",
        )
        client.get(diff_url(project_id, base_doc, target_doc))
        row = store.conn.execute(
            "SELECT measurement_state, conversion_applied, value"
            " FROM quantity_claims WHERE claim_id='Q-1'"
        ).fetchone()
        assert row["measurement_state"] == "m3_insitu"
        assert row["conversion_applied"] == 0
        assert row["value"] == 12.5


class TestSharedStoreSurvival:
    def test_the_connection_is_open_and_unlocked_after_a_diff(self, client, store):
        project_id = new_project(store)
        base_doc, target_doc = located_revision(
            store, project_id, base_text="DN600", target_text="DN900"
        )
        assert client.get(diff_url(project_id, base_doc, target_doc)).status_code == 200

        # The store is the application's single connection. A closed one would
        # raise here; an open transaction would mean the snapshot never ended.
        assert store.conn.in_transaction is False
        assert store.get_project(project_id) is not None
        assert store.conn.execute("SELECT 1").fetchone()[0] == 1

    def test_a_later_request_still_works(self, client, store):
        project_id = new_project(store)
        base_doc, target_doc = located_revision(
            store, project_id, base_text="DN600", target_text="DN900"
        )
        for _ in range(3):
            assert client.get(diff_url(project_id, base_doc, target_doc)).status_code == 200
        health = client.get(f"{API}/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert client.get(f"{API}/projects/{project_id}").status_code == 200
        assert client.post(
            f"{API}/projects", json={"name": "synthetic-project-three"}
        ).status_code == 201

    def test_the_snapshot_refuses_to_begin_inside_a_transaction(self, store):
        project_id = new_project(store)
        base_doc, target_doc = located_revision(
            store, project_id, base_text="DN600", target_text="DN900"
        )
        store.conn.execute("INSERT INTO projects(name) VALUES('synthetic-hold')")
        assert store.conn.in_transaction is True
        try:
            with pytest.raises(ReadSnapshotError):
                with read_snapshot(store):
                    pass  # pragma: no cover - the refusal happens on entry
        finally:
            store.conn.rollback()
        assert store.conn.in_transaction is False
        assert base_doc != target_doc

    def test_an_open_transaction_becomes_a_sanitised_500(self, app, store):
        project_id = new_project(store)
        base_doc, target_doc = located_revision(
            store, project_id, base_text="DN600", target_text="DN900"
        )
        store.conn.execute("INSERT INTO projects(name) VALUES('synthetic-hold')")
        try:
            with TestClient(
                app, headers=dict(AUTH_HEADERS), raise_server_exceptions=False
            ) as raw:
                response = raw.get(diff_url(project_id, base_doc, target_doc))
        finally:
            store.conn.rollback()
        assert response.status_code == 500
        # Non-reflective: the class name, and a fixed sentence. No message, no
        # SQL, and nothing the caller sent comes back.
        assert response.json() == {
            "error": "ReadSnapshotError",
            "message": "internal error",
        }
        assert "synthetic-hold" not in response.text


# --------------------------------------------------------------------------
# Locking and consistency
# --------------------------------------------------------------------------


class TestLocking:
    def test_the_lock_probe_succeeds_when_nothing_holds_the_lock(self, app):
        # A control for the test below: a probe that always failed would look
        # like proof of locking while proving nothing at all.
        registry = app.state.sessions
        acquired = registry._lock.acquire(timeout=0.2)
        try:
            assert acquired is True
        finally:
            if acquired:
                registry._lock.release()

    def test_the_session_lock_is_held_for_the_whole_comparison(
        self, app, store, monkeypatch
    ):
        project_id = new_project(store)
        base_doc, target_doc = located_revision(
            store, project_id, base_text="DN600", target_text="DN900"
        )
        registry = app.state.sessions
        original = revisions_module.build_revision_diff
        attempts: list[bool] = []

        def spy(*args, **kwargs):
            # Another thread, because the route holds the lock in the thread it
            # runs on and an RLock would hand it straight back to itself.
            result: list[bool] = []

            def contender() -> None:
                acquired = registry._lock.acquire(timeout=0.4)
                result.append(acquired)
                if acquired:
                    registry._lock.release()

            thread = threading.Thread(target=contender)
            thread.start()
            thread.join()
            attempts.extend(result)
            return original(*args, **kwargs)

        monkeypatch.setattr(revisions_module, "build_revision_diff", spy)
        with TestClient(app, headers=dict(AUTH_HEADERS)) as c:
            assert c.get(diff_url(project_id, base_doc, target_doc)).status_code == 200

        assert attempts == [False]


class TestSnapshotConsistency:
    def test_a_writer_on_another_connection_is_invisible_inside_the_snapshot(
        self, store
    ):
        project_id = new_project(store)
        base_doc, _ = located_revision(
            store, project_id, base_text="DN600", target_text="DN900"
        )
        limit = 100

        with read_snapshot(store):
            first, _ = store.evidence_nodes_for_document(
                project_id, base_doc, HASH_BASE, limit
            )
            assert len(first) == 1

            # A second connection, because a write through the shared one is
            # not a concurrent writer - it is the same writer.
            writer = sqlite3.connect(store.path, timeout=5)
            try:
                writer.execute(
                    "INSERT INTO evidence_nodes"
                    " (project_id, node_type, label, discipline, payload)"
                    " VALUES (?,?,?,?,?)",
                    (
                        project_id,
                        "element",
                        "written while the diff was reading",
                        "CIVIL",
                        json.dumps({"doc_id": base_doc, "file_hash": HASH_BASE}),
                    ),
                )
                writer.commit()
            finally:
                writer.close()

            second, _ = store.evidence_nodes_for_document(
                project_id, base_doc, HASH_BASE, limit
            )

        # The snapshot did not move: both reads inside it see the same state.
        assert len(second) == len(first)
        # And once it ends, the committed write is visible - so the test above
        # is about the snapshot, not about a write that never landed.
        after, _ = store.evidence_nodes_for_document(
            project_id, base_doc, HASH_BASE, limit
        )
        assert len(after) == len(first) + 1

    def test_the_snapshot_leaves_no_transaction_behind(self, store):
        project_id = new_project(store)
        with read_snapshot(store):
            assert store.conn.in_transaction is True
            store.get_document(project_id, 1)
        assert store.conn.in_transaction is False

    def test_an_exception_inside_the_snapshot_still_releases_it(self, store):
        project_id = new_project(store)
        with pytest.raises(ValueError):
            with read_snapshot(store):
                store.get_project(project_id)
                raise ValueError("synthetic failure inside the snapshot")
        assert store.conn.in_transaction is False
        assert store.get_project(project_id) is not None
















