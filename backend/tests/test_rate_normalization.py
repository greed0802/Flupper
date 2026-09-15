"""Rate normalization tests (Phase 5C).

These drive the real route through the real app, against a real SQLite file,
with invented projects, documents, rate rows and quantity claims. Nothing here
touches a real rate, a real client, a real currency other than the one the
platform declares, or a path outside ``tmp_path``.

The assertions are written to be falsifiable rather than reassuring:

* the read-only test compares every table's row count, the journal length, the
  approval registry and the journal's own hash chain before and after, rather
  than asserting that nothing "looks wrong";
* the lock test tries to take the session lock from another thread *while the
  request is running* and requires that attempt to fail, with a control test
  proving the probe can succeed at all;
* the vocabulary test reads the normalizer's own tuples and compares them with
  the Pydantic literals, so a reason added in one place and not the other is a
  failure rather than a 500 at runtime;
* the no-egress test denies the process a socket and requires the route to
  answer anyway, because "no fabricated rate" includes "no rate fetched from
  somewhere else".

Run from ``backend/`` or the repo root.
"""

from __future__ import annotations

import json
import re
import socket
import threading
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable, get_args

import pytest
from fastapi.testclient import TestClient

import qsagent.api.rates as rates_module
from qsagent.api import create_app
from qsagent.api.contracts import (
    MAX_RATE_AMOUNT_CHARS,
    MAX_RATE_NODE_IDS,
    MAX_RATE_SCANNED_DOCUMENTS,
    MAX_RATE_TEXT_CHARS,
    RateCategory,
    RateProposalDTO,
    RateProposalRequest,
    RateProposalResponse,
    RateUnresolvedReason,
    RateWarning,
)
from qsagent.contracts.evidence import (
    Discipline,
    EvidenceNode,
    EvidenceRef,
    Quantity,
    QuantityClaim,
    Unit,
)
from qsagent.rates import (
    ALLOWED_PAYLOAD_KEYS,
    CANONICAL_RATE_UNITS,
    CATEGORY_UNITS,
    RATE_CATEGORIES,
    UNRESOLVED_REASONS,
    WARNINGS,
    NormalizedRate,
    RateNodeInput,
    SourceDocument,
    normalize_rate_nodes,
)
from qsagent.runtime import ModelRouter
from qsagent.storage import QSStore, ReadSnapshotError, read_snapshot

API = "/api/v1"

TEST_TOKEN = "rate-test-token-0123456789abcdef"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}

# Invented digests. A SHA-256 is 64 lowercase hex characters; these are three of
# them and they belong to no file that has ever existed.
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64

TODAY = date(2026, 9, 15)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
class _NoSecrets:
    """A router with no provider keys. Nothing in this file should need one."""

    def resolve(self, *args, **kwargs):
        return None


@pytest.fixture()
def store(tmp_path):
    # A file, not ``:memory:``: the lock and snapshot tests need a second
    # connection to the same database, which an in-memory store cannot offer.
    st = QSStore(tmp_path / "rates.db")
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


@dataclass(frozen=True)
class Plan:
    """One project holding two rate rows, and a second holding one more."""

    project_id: int
    document_id: int
    rate_id: int
    second_rate_id: int
    quantity_id: int
    foreign_rate_id: int
    foreign_document_id: int
    foreign_project_id: int


@pytest.fixture()
def plan(store):
    project_id = new_project(store)
    document_id = add_source_document(store, project_id)
    rate_id = add_rate_node(store, project_id, doc_id=document_id)
    second_rate_id = add_rate_node(
        store,
        project_id,
        label="Cart away synthetic (second quote)",
        payload=rate_payload(amount="255.00", rate_category="material_unit",
                             unit="kg"),
        doc_id=document_id,
    )
    quantity_id = store.add_node(
        EvidenceNode(
            project_id=project_id,
            node_type="quantity",
            label="Synthetic quantity",
            discipline=Discipline.CIVIL,
            payload={"wbs_item": "1.1"},
        )
    )
    foreign_project_id = new_project(store, name="synthetic-other-project")
    foreign_document = add_source_document(
        store, foreign_project_id, file_name="synthetic-other.pdf", file_hash=HASH_B
    )
    foreign_rate_id = add_rate_node(
        store, foreign_project_id, label="Other project rate",
        doc_id=foreign_document,
    )
    return Plan(
        project_id=project_id,
        document_id=document_id,
        rate_id=rate_id,
        second_rate_id=second_rate_id,
        quantity_id=quantity_id,
        foreign_rate_id=foreign_rate_id,
        foreign_document_id=foreign_document,
        foreign_project_id=foreign_project_id,
    )


# --------------------------------------------------------------------------
# Builders - invented data only
# --------------------------------------------------------------------------
def rates_url(project_id: int) -> str:
    return f"{API}/projects/{project_id}/rates/proposals/normalize"


def new_project(store: QSStore, name: str = "synthetic-project") -> int:
    return store.create_project(name, client=None, tender_no=None)


def add_source_document(
    store: QSStore,
    project_id: int,
    *,
    file_name: str = "synthetic-rates.pdf",
    file_hash: str = HASH_A,
) -> int:
    return store.add_document(
        project_id,
        file_name=file_name,
        file_hash=file_hash,
        media_type="application/pdf",
        discipline="CIVIL",
        drawing_no=None,
        revision=None,
        title="Synthetic Rate Sheet",
        page_count=1,
    )


def rate_payload(**overrides) -> dict:
    """A complete, valid rate payload. Overrides make one thing wrong.

    ``doc_id`` and ``file_hash`` are part of the payload because that is where a
    rate row's provenance lives: none of the ingest paths passes a ``ref`` to
    ``add_node``, so the provenance columns are null on every row the platform
    actually writes.
    """
    payload = {
        "amount": 250.5,
        "unit": "m^3",
        "currency": "AUD",
        "rate_category": "volume_cart_away",
        "effective_date": "2026-01-15",
        "provenance": "machine_export",
        "doc_id": 1,
        "file_hash": HASH_A,
        "raw_text": "Cart away 250.50/m3",
        "sheet": "S1",
    }
    payload.update(overrides)
    return payload


def without(*keys) -> "Callable[..., dict]":
    """A builder that drops named keys from a valid payload."""
    return lambda **overrides: {
        key: value
        for key, value in rate_payload(**overrides).items()
        if key not in keys
    }


def add_rate_node(
    store: QSStore,
    project_id: int,
    *,
    label: str = "Cart away synthetic",
    payload: dict | None = None,
    doc_id: int | None = None,
    file_hash: str | None = None,
    ref: EvidenceRef | None = None,
) -> int:
    """A ``rate`` node built the way the schema allows, not the way ingest runs.

    No ingest path writes ``node_type='rate'`` today, so there is no ingest shape
    to reproduce: the builder writes what the schema and the normalizer's payload
    contract define. The document link travels in the payload because that is the
    only link an ingest-written row would carry - ``add_node`` fills a
    provenance column only when a caller passes a ``ref``.
    """
    body = dict(rate_payload() if payload is None else payload)
    if doc_id is not None:
        body["doc_id"] = doc_id
    if file_hash is not None:
        body["file_hash"] = file_hash
    return store.add_node(
        EvidenceNode(
            project_id=project_id,
            node_type="rate",
            label=label,
            discipline=Discipline.CIVIL,
            ref=ref,
            payload=body,
        )
    )


def write_raw_node(
    store: QSStore, project_id: int, payload_text: str, *, node_type: str = "rate"
) -> int:
    """A row with payload text that never came through ``add_node``.

    ``add_node`` takes a mapping, so a payload that will not decode can only be
    stored by writing the column directly. That is exactly the state a corrupt
    or foreign-written database is in, and the route has to survive reading it.
    """
    cursor = store.conn.execute(
        "INSERT INTO evidence_nodes (project_id, node_type, label, discipline,"
        " payload) VALUES (?,?,?,?,?)",
        (int(project_id), node_type, "synthetic raw row", "CIVIL", payload_text),
    )
    store.conn.commit()
    return int(cursor.lastrowid)


def add_claim(
    store: QSStore,
    project_id: int,
    *,
    claim_id: str = "Q-1",
    file_name: str = "synthetic-rates.pdf",
    file_hash: str = HASH_A,
    value: float = 12.5,
) -> str:
    return store.save_claim(
        QuantityClaim(
            claim_id=claim_id,
            project_id=project_id,
            description="synthetic quantity for rate tests",
            quantity=Quantity(value=value, unit=Unit.M3),
            measurement_state="m3_insitu",
            conversion_applied=False,
            method="test.synthetic",
            evidence=[EvidenceRef(
                file_hash=file_hash,
                file_name=file_name,
                sheet="S1",
                raw_text="DN600",
            )],
            assumption_ids=[],
            workings=["synthetic"],
        )
    )


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


def post(client: TestClient, project_id: int, node_ids, **kwargs):
    return client.post(rates_url(project_id), json={"node_ids": node_ids}, **kwargs)


# --------------------------------------------------------------------------
# Pure normalization - no store, no route
# --------------------------------------------------------------------------
def source(hash_: str = HASH_A, document_id: int = 1) -> SourceDocument:
    return SourceDocument(document_id, "synthetic-rates.pdf", hash_)


def node(payload: dict | None = None, **columns) -> RateNodeInput:
    return RateNodeInput(
        node_id=columns.pop("node_id", 7),
        label=columns.pop("label", "synthetic rate row"),
        payload=json.dumps(rate_payload() if payload is None else payload),
        **columns,
    )


def one(
    payload: dict | None = None,
    *,
    documents: list[SourceDocument] | None = None,
    today: date = TODAY,
    **columns,
) -> NormalizedRate:
    return normalize_rate_nodes(
        [node(payload, **columns)],
        documents=documents if documents is not None else [source()],
        today=today,
    )[0]


# --------------------------------------------------------------------------
# Vocabulary: the normalizer and the wire contract must be the same list
# --------------------------------------------------------------------------
class TestVocabularyMirror:
    def test_every_reason_the_normalizer_can_emit_is_in_the_contract(self):
        assert set(UNRESOLVED_REASONS) == set(get_args(RateUnresolvedReason))

    def test_every_warning_the_normalizer_can_emit_is_in_the_contract(self):
        assert set(WARNINGS) == set(get_args(RateWarning))

    def test_every_category_is_in_the_contract(self):
        assert set(RATE_CATEGORIES) == set(get_args(RateCategory))

    def test_the_category_table_covers_exactly_the_categories(self):
        assert set(CATEGORY_UNITS) == set(RATE_CATEGORIES)

    def test_every_rate_unit_is_a_claim_unit(self):
        # A rate quoted in a unit no claim can be measured in would be a rate
        # nothing could ever be costed with.
        claim_units = {unit.value for unit in Unit}
        assert CANONICAL_RATE_UNITS <= claim_units
        assert CANONICAL_RATE_UNITS  # not vacuous

    def test_the_payload_key_set_is_closed(self):
        # Frozen here as well: widening the accepted payload is a decision, and
        # it should cost an edit in two files rather than happening quietly.
        assert ALLOWED_PAYLOAD_KEYS == {
            "amount",
            "unit",
            "currency",
            "rate_category",
            "effective_date",
            "doc_id",
            "file_hash",
            "provenance",
            "sheet",
            "page",
            "raw_text",
            "bbox",
            "source_file",
        }

    def test_money_is_quantized_to_two_places_half_up(self):
        from qsagent.rates.normalize import AMOUNT_EXPONENT, AMOUNT_QUANTUM_PLACES

        assert AMOUNT_EXPONENT == Decimal("0.01")
        assert AMOUNT_QUANTUM_PLACES == 2


class TestNormalization:
    def test_a_complete_stored_rate_normalizes_exactly(self):
        result = one()
        assert result.status == "normalized"
        assert result.confidence == "exact"
        assert result.reason is None
        assert result.normalized_amount == "250.50"
        assert result.original_amount == "250.5"
        assert result.normalized_unit == "m3"
        assert result.currency == "AUD"
        assert result.rate_category == "volume_cart_away"
        assert result.effective_date == "2026-01-15"
        assert result.source_document_id == 1
        assert result.source_file_name == "synthetic-rates.pdf"
        assert result.source_file_hash == HASH_A
        assert result.source_age_days == (TODAY - date(2026, 1, 15)).days
        assert result.locator_present is True
        assert result.warnings == ()

    def test_a_normalized_amount_is_a_two_place_decimal_string(self):
        result = one()
        assert isinstance(result.normalized_amount, str)
        assert re.match(r"^\d+\.\d{2}$", result.normalized_amount or "")

    def test_an_unresolved_row_carries_no_partial_result(self):
        result = one(rate_payload(currency="USD"))
        assert result.status == "unresolved"
        assert result.confidence == "unresolved"
        assert result.reason == "unsupported_currency"
        assert result.normalized_amount is None
        assert result.original_amount is None
        assert result.normalized_unit is None
        assert result.currency is None
        assert result.rate_category is None
        assert result.effective_date is None
        assert result.source_file_hash is None
        assert result.source_document_id is None
        assert result.locator_present is False

    # -------------------------------------------------------------- amount
    def test_rounding_is_half_up_on_the_stored_digits(self):
        assert one(rate_payload(amount="1.005")).normalized_amount == "1.01"
        assert one(rate_payload(amount="2.675")).normalized_amount == "2.68"
        assert one(rate_payload(amount="250.5")).normalized_amount == "250.50"
        assert one(rate_payload(amount="0.01")).normalized_amount == "0.01"

    def test_a_float_payload_rounds_like_its_printed_digits(self):
        # ``str(1.005)`` is "1.005", so the Decimal parse is the value the row
        # shows. Going through float arithmetic instead would give "1.00".
        assert one(rate_payload(amount=1.005)).normalized_amount == "1.01"

    def test_an_amount_that_rounds_to_zero_is_refused(self):
        assert one(rate_payload(amount="0.004")).reason == "invalid_amount"

    @pytest.mark.parametrize(
        "amount",
        [None, "", "  ", "not a number", "nan", "inf", "-5", "0", "1e12", True,
         [1], {"value": 1}, "1" * 65],
    )
    def test_an_unusable_amount_is_refused(self, amount):
        payload = rate_payload()
        if amount is None:
            payload.pop("amount")
        else:
            payload["amount"] = amount
        result = one(payload)
        assert result.status == "unresolved"
        assert result.reason in {"missing_amount", "invalid_amount"}

    def test_a_large_but_bounded_amount_is_accepted(self):
        assert one(rate_payload(amount="1000000000")).normalized_amount == (
            "1000000000.00"
        )

    # ---------------------------------------------------------------- unit
    @pytest.mark.parametrize(
        "declared,expected",
        [
            ("m3", "m3"), ("M3", "m3"), ("m^3", "m3"), ("CUM", "m3"),
            ("cu.m", "m3"), ("m 3", "m3"), ("CubicMetre", "m3"),
        ],
    )
    def test_unit_aliases_fold_onto_one_canonical_spelling(self, declared, expected):
        assert one(rate_payload(unit=declared)).normalized_unit == expected

    def test_an_unknown_unit_is_refused_not_guessed(self):
        result = one(rate_payload(unit="crates"))
        assert result.reason == "unsupported_unit"

    def test_a_missing_unit_is_refused(self):
        assert one(without("unit")()).reason == "missing_unit"

    @pytest.mark.parametrize(
        "unit,category",
        [
            ("hr", "plant_time"),
            ("hr", "labour_time"),
            ("kg", "material_unit"),
            ("m3", "volume_cart_away"),
            ("item", "subcontract_lumpsum"),
        ],
    )
    def test_each_category_accepts_its_own_units(self, unit, category):
        assert one(
            rate_payload(unit=unit, rate_category=category)
        ).normalized_unit == unit

    @pytest.mark.parametrize(
        "unit,category",
        [
            ("hr", "volume_cart_away"),
            ("m3", "plant_time"),
            ("kg", "subcontract_lumpsum"),
            ("m3", "labour_time"),
        ],
    )
    def test_a_unit_the_category_cannot_use_is_refused_not_converted(
        self, unit, category
    ):
        result = one(rate_payload(unit=unit, rate_category=category))
        assert result.reason == "unit_category_mismatch"

    def test_an_unknown_category_is_refused_not_reinterpreted(self):
        result = one(rate_payload(rate_category="plant_hour"))
        assert result.reason == "unsupported_category"

    def test_a_missing_category_is_refused(self):
        assert one(without("rate_category")()).reason == "missing_category"

    # ------------------------------------------------------------ currency
    @pytest.mark.parametrize("declared", ["AUD", "aud", " Aud ", "aUd"])
    def test_aud_is_accepted_in_any_letter_case(self, declared):
        assert one(rate_payload(currency=declared)).currency == "AUD"

    @pytest.mark.parametrize("declared", ["$", "A$", "USD", "NZD", "GBP", "dollars"])
    def test_a_currency_that_is_not_aud_is_refused(self, declared):
        result = one(rate_payload(currency=declared))
        assert result.reason == "unsupported_currency"
        assert result.currency is None

    def test_a_missing_currency_is_refused(self):
        assert one(without("currency")()).reason == "missing_currency"

    # ---------------------------------------------------------------- dates
    def test_the_age_is_measured_from_the_stated_reference_date(self):
        result = one(rate_payload(effective_date="2026-09-14"), today=date(2026, 9, 15))
        assert result.source_age_days == 1

    def test_a_rate_from_today_is_not_stale(self):
        result = one(rate_payload(effective_date=TODAY.isoformat()))
        assert result.source_age_days == 0
        assert "stale_source" not in result.warnings

    def test_the_stale_boundary_is_exact(self):
        on_the_bound = TODAY - timedelta(days=365)
        just_past = TODAY - timedelta(days=366)
        assert "stale_source" not in one(
            rate_payload(effective_date=on_the_bound.isoformat())
        ).warnings
        assert "stale_source" in one(
            rate_payload(effective_date=just_past.isoformat())
        ).warnings

    def test_a_stale_rate_is_reported_not_escalated(self):
        # No factor is applied, because none has a verified source here. The
        # number is the stored number and the age is what changed.
        result = one(rate_payload(effective_date="2024-01-01"))
        assert result.normalized_amount == "250.50"
        assert result.source_age_days == (TODAY - date(2024, 1, 1)).days
        assert "stale_source" in result.warnings

    @pytest.mark.parametrize(
        "declared", [None, "", "not a date", "2026-1-5", "20260105", "15/01/2026",
                     "2026-13-01", "2026-02-30", "2027-01-01"],
    )
    def test_an_unusable_effective_date_is_refused(self, declared):
        payload = rate_payload()
        if declared is None:
            payload.pop("effective_date")
        else:
            payload["effective_date"] = declared
        result = one(payload)
        assert result.reason in {
            "missing_effective_date", "invalid_effective_date"
        }

    # ----------------------------------------------------------- provenance
    def test_a_row_that_will_not_declare_its_provenance_is_refused(self):
        assert one(without("provenance")()).reason == "missing_provenance"

    def test_an_unrecognised_provenance_is_refused(self):
        assert one(rate_payload(provenance="someone_said_so")).reason == (
            "unsupported_provenance"
        )

    def test_a_hand_prepared_row_is_inferred_and_flagged(self):
        result = one(rate_payload(provenance="hand_prepared"))
        assert result.status == "normalized"
        assert result.confidence == "inferred"
        assert "manual_provenance" in result.warnings

    # -------------------------------------------------------------- locator
    def test_a_row_with_no_locator_is_inferred_and_flagged(self):
        result = one(without("raw_text", "sheet")())
        assert result.status == "normalized"
        assert result.confidence == "inferred"
        assert result.locator_present is False
        assert "locator_unavailable" in result.warnings
        assert "sheet_or_page_unavailable" in result.warnings

    def test_a_locator_in_the_column_outranks_the_payload(self):
        result = one(
            without("raw_text", "sheet")(),
            raw_text="DN600 from the column",
            sheet="S9",
        )
        assert result.locator_present is True
        assert result.confidence == "exact"
        assert result.warnings == ()

    def test_a_page_reference_satisfies_the_sheet_or_page_half(self):
        # A page is a reference, not a locator: it narrows the source without
        # tying the number to a spot on it, so a row with a page and no locator
        # is inferred rather than exact.
        page_only = one(without("raw_text", "sheet")(), page=3)
        assert "sheet_or_page_unavailable" not in page_only.warnings
        assert "locator_unavailable" in page_only.warnings
        assert page_only.confidence == "inferred"

        both = one(without("sheet")(), page=3)
        assert both.warnings == ()
        assert both.confidence == "exact"

    def test_a_zero_or_negative_page_is_not_a_location(self):
        result = one(without("raw_text", "sheet")(), page=0)
        assert "sheet_or_page_unavailable" in result.warnings

    # --------------------------------------------------------------- source
    def test_a_row_naming_no_source_is_refused(self):
        assert one(without("doc_id", "file_hash")()).reason == "missing_source"

    def test_a_document_id_the_project_no_longer_holds_is_unavailable(self):
        # The source was replaced, or the row predates a re-ingest. Either way
        # there is no document to quote from and no rate to report.
        result = one(rate_payload(doc_id=999))
        assert result.reason == "evidence_unavailable"

    def test_a_hash_with_no_document_row_is_unverified(self):
        result = one(without("doc_id")(file_hash=HASH_C))
        assert result.reason == "unverified_source"

    def test_a_hash_that_contradicts_the_document_id_is_a_mismatch(self):
        result = one(rate_payload(file_hash=HASH_B))
        assert result.reason == "source_mismatch"

    def test_a_hash_that_is_not_a_hash_is_refused(self):
        result = one(rate_payload(file_hash="not-a-hash"))
        assert result.reason == "invalid_source_hash"

    def test_a_document_whose_stored_hash_is_not_a_hash_cannot_be_cited(self):
        # ``documents.file_hash`` is unconstrained TEXT. A row holding something
        # else cannot be named as the source of a rate.
        result = one(
            without("file_hash")(),
            documents=[SourceDocument(1, "synthetic.pdf", "not-a-hash")],
        )
        assert result.reason == "invalid_source_hash"

    @pytest.mark.parametrize("declared", [0, -1, "1", True, 1.5, [1]])
    def test_a_document_id_that_is_not_a_positive_integer_is_refused(
        self, declared
    ):
        result = one(rate_payload(doc_id=declared))
        assert result.reason == "invalid_document_id"

    def test_a_source_can_be_named_by_hash_alone(self):
        result = one(without("doc_id")())
        assert result.status == "normalized"
        assert result.source_document_id == 1
        assert result.source_file_hash == HASH_A

    def test_the_proposal_names_the_store_not_the_payload(self):
        # A hash in the row's own column is used when the payload names none,
        # and what comes back is the document's hash - the store decided.
        result = one(without("file_hash")(), file_hash=HASH_A)
        assert result.status == "normalized"
        assert result.source_document_id == 1
        assert result.source_file_hash == HASH_A

    # ------------------------------------------------------------- payload
    @pytest.mark.parametrize("raw", ["{not json", "[]", "null", '"a string"', ""])
    def test_a_payload_that_is_not_an_object_is_refused(self, raw):
        result = normalize_rate_nodes(
            [RateNodeInput(7, "raw row", raw)],
            documents=[source()],
            today=TODAY,
        )[0]
        assert result.reason == "malformed_payload"

    def test_an_unexpected_payload_key_is_refused(self):
        # A row that grew a field is a row whose meaning changed. Reading only
        # the fields we recognise would normalise a rate from a partly
        # understood row.
        result = one(rate_payload(rate=250.5))
        assert result.reason == "unknown_payload_keys"

    def test_a_source_file_path_is_accepted_and_ignored(self):
        result = one(rate_payload(source_file=r"C:\someone\elses\rates.xlsx"))
        assert result.status == "normalized"
        # The key is accepted so a row carrying it is not refused, and it goes
        # no further: no field on the proposal can hold a path from someone
        # else's machine.
        assert r"C:\someone" not in json.dumps(result.__dict__, default=str)

    # --------------------------------------------------------------- order
    def test_the_refusal_order_is_fixed(self):
        # This row breaks four rules at once. The order the module docstring
        # fixes decides which one is reported, and it must not drift.
        result = one({
            "amount": "not a number",
            "unit": "crates",
            "currency": "USD",
            "rate_category": "nonsense",
        })
        assert result.reason == "missing_source"

        result = one({
            "doc_id": 1,
            "file_hash": HASH_A,
            "amount": "not a number",
            "unit": "crates",
            "currency": "USD",
            "rate_category": "nonsense",
        })
        assert result.reason == "missing_provenance"

        result = one({
            "doc_id": 1,
            "file_hash": HASH_A,
            "provenance": "machine_export",
            "amount": "not a number",
            "unit": "crates",
            "currency": "USD",
            "rate_category": "nonsense",
        })
        assert result.reason == "invalid_amount"

        result = one(rate_payload(unit="crates", currency="USD",
                                  rate_category="nonsense"))
        assert result.reason == "unsupported_unit"

    # --------------------------------------------------------- determinism
    def test_the_same_row_yields_the_same_proposal(self):
        first = one()
        second = one()
        assert first == second

    def test_the_reference_date_is_an_argument_not_a_clock_read(self):
        earlier = one(today=date(2026, 9, 15))
        later = one(today=date(2026, 9, 16))
        assert earlier.normalized_amount == later.normalized_amount
        assert later.source_age_days == earlier.source_age_days + 1


class TestDuplicateQuotes:
    def build(self, *payloads: dict) -> list[NormalizedRate]:
        return normalize_rate_nodes(
            [
                node(payload, node_id=index + 1)
                for index, payload in enumerate(payloads)
            ],
            documents=[source()],
            today=TODAY,
        )

    def test_two_rows_quoting_one_source_and_category_are_both_flagged(self):
        proposals = self.build(rate_payload(), rate_payload(amount="1.00"))
        assert [p.duplicate_quote_count for p in proposals] == [1, 1]
        assert all("duplicate_source_quote" in p.warnings for p in proposals)
        # Neither is preferred: both keep their own number.
        assert [p.normalized_amount for p in proposals] == ["250.50", "1.00"]

    def test_three_rows_report_two_others_each(self):
        proposals = self.build(rate_payload(), rate_payload(), rate_payload())
        assert [p.duplicate_quote_count for p in proposals] == [2, 2, 2]

    def test_a_different_category_is_not_a_duplicate(self):
        proposals = self.build(
            rate_payload(),
            rate_payload(rate_category="material_unit", unit="kg"),
        )
        assert [p.duplicate_quote_count for p in proposals] == [0, 0]
        assert all(p.warnings == () for p in proposals)

    def test_a_different_source_is_not_a_duplicate(self):
        proposals = self.build(rate_payload())
        assert proposals[0].duplicate_quote_count == 0

    def test_a_different_unit_is_not_a_duplicate(self):
        proposals = self.build(
            rate_payload(),
            rate_payload(rate_category="material_unit", unit="m2"),
        )
        assert [p.duplicate_quote_count for p in proposals] == [0, 0]

    def test_two_unresolved_rows_are_not_duplicates_of_each_other(self):
        proposals = self.build(rate_payload(currency="USD"), rate_payload(
            currency="USD"
        ))
        assert [p.duplicate_quote_count for p in proposals] == [0, 0]
        assert all(p.warnings == () for p in proposals)

    def test_an_unresolved_row_does_not_join_a_resolved_group(self):
        proposals = self.build(rate_payload(), rate_payload(unit="crates"))
        assert [p.duplicate_quote_count for p in proposals] == [0, 0]

    def test_the_flag_is_not_repeated(self):
        proposals = normalize_rate_nodes(
            [node(rate_payload(), node_id=1), node(rate_payload(), node_id=2)],
            documents=[source()],
            today=TODAY,
        )
        assert proposals[0].warnings.count("duplicate_source_quote") == 1


# --------------------------------------------------------------------------
# Route surface
# --------------------------------------------------------------------------
def served_post_paths(app) -> set[str]:
    """Every path the app serves with POST, from its own routing table."""
    found: set[str] = set()
    stack = list(getattr(app, "routes", ()))
    while stack:
        route = stack.pop()
        inner = getattr(route, "original_router", None)
        if inner is not None:
            stack.extend(getattr(inner, "routes", ()))
            continue
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if path and "POST" in methods:
            found.add(str(path))
    return found


class TestRouteSurface:
    def test_the_route_is_served_under_the_authenticated_prefix(self, app):
        expected = f"{API}/projects/{{project_id}}/rates/proposals/normalize"
        assert expected in served_post_paths(app)

    def test_the_request_carries_node_ids_and_nothing_else(self):
        # Structural: there is no field here for a rate, a unit, a currency, a
        # category, a hash, a proof or a path, so a caller cannot assert one.
        assert set(RateProposalRequest.model_fields) == {"node_ids"}

    def test_the_path_has_no_string_parameter(self, app):
        schema = app.openapi()
        path = f"{API}/projects/{{project_id}}/rates/proposals/normalize"
        assert path in schema["paths"]
        parameters = schema["paths"][path]["post"]["parameters"]
        assert [(p["name"], p["in"], p["schema"]["type"]) for p in parameters] == [
            ("project_id", "path", "integer")
        ]


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------
class TestNotFoundAndRefusal:
    def test_an_unknown_project_is_a_404(self, client, store):
        response = post(client, 424242, [1])
        assert response.status_code == 404
        assert response.json()["message"] == "unknown project"

    def test_a_node_id_that_does_not_exist_is_a_404(self, client, store, plan):
        response = post(client, plan.project_id, [999999])
        assert response.status_code == 404
        assert response.json()["message"] == "unknown rate node for this project"

    def test_a_node_that_is_not_a_rate_is_a_404(self, client, store, plan):
        # A quantity node in the right project: the route is for rate rows, and
        # an id that is not one resolves to nothing rather than being handed
        # back for the caller to re-classify.
        response = post(client, plan.project_id, [plan.quantity_id])
        assert response.status_code == 404

    def test_a_node_in_another_project_is_a_404(self, client, store, plan):
        response = post(client, plan.project_id, [plan.foreign_rate_id])
        assert response.status_code == 404

    def test_the_three_404_causes_are_indistinguishable(self, client, store, plan):
        # Otherwise the route is an oracle for which node ids exist elsewhere.
        bodies = {
            json.dumps(post(client, plan.project_id, [identity]).json())
            for identity in (999999, plan.quantity_id, plan.foreign_rate_id)
        }
        assert len(bodies) == 1

    def test_a_mixed_request_fails_whole_rather_than_in_part(
        self, client, store, plan
    ):
        # The good id is never answered. A response that quietly dropped the id
        # it could not resolve would look complete to a reader.
        response = post(client, plan.project_id, [plan.rate_id, 999999])
        assert response.status_code == 404
        assert "proposals" not in response.json()

    def test_an_unknown_project_does_not_reveal_a_node(
        self, client, store, plan
    ):
        response = post(client, 424242, [plan.rate_id])
        assert response.status_code == 404
        assert response.json()["message"] == "unknown project"

    def test_no_token_is_a_401(self, app, store, plan):
        with TestClient(app) as anonymous:
            response = anonymous.post(
                rates_url(plan.project_id), json={"node_ids": [plan.rate_id]}
            )
        assert response.status_code == 401

    def test_a_wrong_token_is_a_401(self, app, store, plan):
        with TestClient(app, headers={"Authorization": "Bearer nope"}) as wrong:
            response = wrong.post(
                rates_url(plan.project_id), json={"node_ids": [plan.rate_id]}
            )
        assert response.status_code == 401

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"node_ids": []},
            {"node_ids": [0]},
            {"node_ids": [-1]},
            {"node_ids": ["1"]},
            {"node_ids": [1.5]},
            {"node_ids": [True]},
            {"node_ids": [1], "amount": 999},
            {"node_ids": list(range(1, MAX_RATE_NODE_IDS + 2))},
            {"node_ids": None},
        ],
    )
    def test_a_malformed_request_is_a_422(self, client, store, plan, body):
        response = client.post(rates_url(plan.project_id), json=body)
        assert response.status_code == 422

    def test_an_id_too_large_for_the_store_is_a_422_not_a_500(
        self, client, store, plan
    ):
        # A value the driver cannot bind must be refused by the request model.
        response = post(client, plan.project_id, [2**63])
        assert response.status_code == 422

    def test_a_422_body_carries_a_field_path_and_not_the_value(
        self, client, store, plan
    ):
        response = client.post(
            rates_url(plan.project_id), json={"node_ids": ["synthetic-secret"]}
        )
        assert response.status_code == 422
        assert response.json()["fields"] == ["body.node_ids.0"]
        assert "synthetic-secret" not in response.text

    def test_an_id_is_a_json_integer_and_not_a_lookalike(self, client, store, plan):
        # ``"1"`` and ``true`` would both select row 1 under lax coercion, and a
        # caller that sent the wrong type would be answered about the wrong row.
        for lookalike in ("1", True, 1.0):
            response = post(client, plan.project_id, [lookalike])
            assert response.status_code == 422, lookalike

    def test_duplicate_ids_are_deduplicated_not_rejected(self, client, store, plan):
        response = post(client, plan.project_id, [plan.rate_id, plan.rate_id])
        assert response.status_code == 200
        body = response.json()
        assert [p["node_id"] for p in body["proposals"]] == [plan.rate_id]
        assert body["normalized"] == 1


# --------------------------------------------------------------------------
# Through the route
# --------------------------------------------------------------------------
class TestProposalsThroughTheRoute:
    def test_a_valid_row_is_answered_with_its_normalized_rate(
        self, client, store, plan
    ):
        response = post(client, plan.project_id, [plan.rate_id])
        assert response.status_code == 200
        body = response.json()
        assert body["project_id"] == plan.project_id
        assert body["normalized"] == 1
        assert body["unresolved"] == 0
        assert body["warnings"] == []

        proposal = body["proposals"][0]
        assert proposal["node_id"] == plan.rate_id
        assert proposal["status"] == "normalized"
        assert proposal["confidence"] == "exact"
        assert proposal["reason"] is None
        assert proposal["normalized_amount"] == "250.50"
        assert proposal["normalized_unit"] == "m3"
        assert proposal["currency"] == "AUD"
        assert proposal["rate_category"] == "volume_cart_away"
        assert proposal["source_document_id"] == plan.document_id
        assert proposal["source_file_hash"] == HASH_A
        assert proposal["source_file_name"] == "synthetic-rates.pdf"
        assert proposal["locator_present"] is True

    def test_the_reference_date_is_todays_utc_date(self, client, store, plan):
        response = post(client, plan.project_id, [plan.rate_id])
        reference = date.fromisoformat(response.json()["reference_date"])
        assert abs((reference - date.today()).days) <= 1

    def test_the_response_is_validated_by_its_own_contract(self, client, store, plan):
        response = post(client, plan.project_id, [plan.rate_id])
        # Round-tripping through the model is what proves the route emitted the
        # shape it promised, rather than something that merely parses as JSON.
        model = RateProposalResponse.model_validate(response.json())
        assert model.proposals[0].node_id == plan.rate_id

    def test_the_order_is_the_callers_order(self, client, store, plan):
        response = post(
            client, plan.project_id, [plan.second_rate_id, plan.rate_id]
        )
        assert [
            proposal["node_id"] for proposal in response.json()["proposals"]
        ] == [plan.second_rate_id, plan.rate_id]

    def test_both_rows_are_answered_and_counted(self, client, store, plan):
        body = post(
            client, plan.project_id, [plan.rate_id, plan.second_rate_id]
        ).json()
        assert body["normalized"] == 2
        assert body["unresolved"] == 0
        assert body["normalized"] + body["unresolved"] == len(body["proposals"])

    def test_an_unresolved_row_is_a_200_with_a_reason_not_an_error(
        self, client, store, plan
    ):
        # The node exists; it is the proposal that cannot be made. A 4xx here
        # would say the request was wrong, and the request was fine.
        node_id = add_rate_node(
            store, plan.project_id, label="Unpriced row",
            payload=rate_payload(currency="USD"), doc_id=plan.document_id,
        )
        response = post(client, plan.project_id, [node_id])
        assert response.status_code == 200
        body = response.json()
        assert body["normalized"] == 0
        assert body["unresolved"] == 1
        proposal = body["proposals"][0]
        assert proposal["status"] == "unresolved"
        assert proposal["confidence"] == "unresolved"
        assert proposal["reason"] == "unsupported_currency"
        assert proposal["normalized_amount"] is None

    def test_a_row_whose_payload_does_not_decode_is_answered_unresolved(
        self, client, store, plan
    ):
        node_id = write_raw_node(store, plan.project_id, "{not json")
        response = post(client, plan.project_id, [node_id])
        assert response.status_code == 200
        proposal = response.json()["proposals"][0]
        assert proposal["reason"] == "malformed_payload"
        assert proposal["normalized_amount"] is None

    def test_a_row_whose_document_is_gone_is_answered_unresolved(
        self, client, store, plan
    ):
        node_id = add_rate_node(
            store, plan.project_id, label="Orphaned row",
            payload=rate_payload(doc_id=999),
        )
        proposal = post(client, plan.project_id, [node_id]).json()["proposals"][0]
        assert proposal["reason"] == "evidence_unavailable"

    def test_a_document_in_another_project_is_not_a_source(self, client, store, plan):
        # The hash and the id both exist in this store - in the other project.
        # Source resolution is project-scoped, so a row in this project naming
        # either is unresolved rather than borrowing another project's evidence.
        by_hash = add_rate_node(
            store, plan.project_id, label="Borrowed hash",
            payload=without("doc_id")(file_hash=HASH_B),
        )
        assert post(client, plan.project_id, [by_hash]).json()["proposals"][0][
            "reason"
        ] == "unverified_source"

        by_id = add_rate_node(
            store, plan.project_id, label="Borrowed id",
            payload=rate_payload(doc_id=plan.foreign_document_id, file_hash=None),
        )
        assert post(client, plan.project_id, [by_id]).json()["proposals"][0][
            "reason"
        ] == "evidence_unavailable"

    def test_two_quotes_of_one_source_are_both_flagged(self, client, store, plan):
        duplicate = add_rate_node(
            store, plan.project_id, label="Second copy",
            payload=rate_payload(amount="251.00"), doc_id=plan.document_id,
        )
        body = post(client, plan.project_id, [plan.rate_id, duplicate]).json()
        assert [p["duplicate_quote_count"] for p in body["proposals"]] == [1, 1]
        assert all(
            "duplicate_source_quote" in p["warnings"] for p in body["proposals"]
        )

    def test_a_hand_prepared_row_travels_as_inferred(self, client, store, plan):
        node_id = add_rate_node(
            store, plan.project_id, label="Typed in by hand",
            payload=rate_payload(provenance="hand_prepared"),
            doc_id=plan.document_id,
        )
        proposal = post(client, plan.project_id, [node_id]).json()["proposals"][0]
        assert proposal["confidence"] == "inferred"
        assert "manual_provenance" in proposal["warnings"]

    def test_no_workstation_path_reaches_the_response(self, client, store, plan):
        node_id = add_rate_node(
            store, plan.project_id, label="Row with a path",
            payload=rate_payload(source_file=r"C:\Users\someone\rates.xlsx"),
            doc_id=plan.document_id,
        )
        response = post(client, plan.project_id, [node_id])
        assert response.status_code == 200
        assert "someone" not in response.text
        assert "xlsx" not in response.text


# --------------------------------------------------------------------------
# Read-only, and the gates it must not touch
# --------------------------------------------------------------------------
class TestReadOnly:
    def test_a_proposal_writes_nothing_and_gates_nothing(
        self, client, store, app, plan
    ):
        add_claim(store, plan.project_id)
        store.save_checkmate(plan.project_id, "synthetic-subject", False, [])

        before_rows = table_counts(store)
        before_journal = len(store.journal_entries())
        before_approvals = len(app.state.approvals)
        assert store.verify_journal() is True

        assert post(client, plan.project_id, [plan.rate_id]).status_code == 200

        assert table_counts(store) == before_rows
        assert len(store.journal_entries()) == before_journal
        assert len(app.state.approvals) == before_approvals
        assert store.verify_journal() is True

    def test_a_claim_keeps_its_number_unit_and_state(self, client, store, plan):
        add_claim(store, plan.project_id, value=12.5)
        post(client, plan.project_id, [plan.rate_id])
        row = store.conn.execute(
            "SELECT value, unit, measurement_state, conversion_applied"
            " FROM quantity_claims WHERE claim_id='Q-1'"
        ).fetchone()
        assert row["value"] == 12.5
        assert row["unit"] == "m3"
        assert row["measurement_state"] == "m3_insitu"
        assert row["conversion_applied"] == 0

    def test_a_checkmate_result_is_not_re_graded(self, client, store, plan):
        store.save_checkmate(plan.project_id, "synthetic-subject", False, [])
        before = store.conn.execute(
            "SELECT subject, passed FROM checkmate_results"
        ).fetchall()
        post(client, plan.project_id, [plan.rate_id])
        after = store.conn.execute(
            "SELECT subject, passed FROM checkmate_results"
        ).fetchall()
        assert [tuple(row) for row in after] == [tuple(row) for row in before]

    def test_no_approval_is_created_or_consumed(self, client, app, store, plan):
        assert len(app.state.approvals) == 0
        post(client, plan.project_id, [plan.rate_id])
        assert len(app.state.approvals) == 0

    def test_the_route_leaves_no_transaction_open(self, client, store, plan):
        assert store.conn.in_transaction is False
        post(client, plan.project_id, [plan.rate_id])
        assert store.conn.in_transaction is False

    def test_a_refused_request_leaves_no_transaction_open(
        self, client, store, plan
    ):
        post(client, plan.project_id, [999999])
        assert store.conn.in_transaction is False


class TestSharedStoreSurvival:
    def test_the_snapshot_refuses_to_begin_inside_a_transaction(self, store, plan):
        # An open transaction belongs to someone else. Starting a read inside it
        # would either join it or end it, and both are wrong for a reader.
        store.conn.execute("BEGIN")
        try:
            with pytest.raises(ReadSnapshotError):
                with read_snapshot(store):
                    store.get_project(plan.project_id)
            still_open = store.conn.in_transaction
        finally:
            store.conn.rollback()
        # The reader did not commit someone else's work and did not roll it
        # back either: the transaction it refused to join is still there.
        assert still_open is True

    def test_the_store_still_works_after_a_refused_snapshot(self, client, store, plan):
        store.conn.execute("BEGIN")
        try:
            with pytest.raises(ReadSnapshotError):
                with read_snapshot(store):
                    store.get_project(plan.project_id)
        finally:
            store.conn.rollback()
        assert post(client, plan.project_id, [plan.rate_id]).status_code == 200

    def test_a_read_failure_is_reported_without_the_detail(
        self, app, store, plan, monkeypatch
    ):
        def explode(*args, **kwargs):
            raise ReadSnapshotError("snapshot failed at C:/secret/path/rates.db")

        monkeypatch.setattr(rates_module, "read_snapshot", explode)
        # ``raise_server_exceptions=False`` so the 500 is observable as a
        # response: the point here is what the client would receive, and the
        # default would hand the exception back to the test instead.
        with TestClient(
            app, headers=dict(AUTH_HEADERS), raise_server_exceptions=False
        ) as c:
            response = post(c, plan.project_id, [plan.rate_id])

        assert response.status_code == 500
        body = response.json()
        assert body["error"] == "ReadSnapshotError"
        assert body["message"] == "internal error"
        assert "secret" not in response.text
        assert "rates.db" not in response.text


# --------------------------------------------------------------------------
# Locking
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

    def test_the_session_lock_is_held_for_the_whole_request(
        self, app, store, plan, monkeypatch
    ):
        registry = app.state.sessions
        original = rates_module.build_rate_proposals
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

        monkeypatch.setattr(rates_module, "build_rate_proposals", spy)
        with TestClient(app, headers=dict(AUTH_HEADERS)) as c:
            assert post(c, plan.project_id, [plan.rate_id]).status_code == 200

        assert attempts == [False]

    def test_the_route_runs_inside_the_app_wide_lock(
        self, app, store, plan, monkeypatch
    ):
        # One lock for every project, because the store is one shared SQLite
        # connection. A per-project lock would let two projects interleave
        # commits on it.
        registry = app.state.sessions
        original = rates_module.build_rate_proposals
        owned: list[bool] = []

        def spy(*args, **kwargs):
            owned.append(registry._lock._is_owned())
            return original(*args, **kwargs)

        monkeypatch.setattr(rates_module, "build_rate_proposals", spy)
        with TestClient(app, headers=dict(AUTH_HEADERS)) as c:
            assert post(c, plan.project_id, [plan.rate_id]).status_code == 200
        assert owned == [True]


# --------------------------------------------------------------------------
# Bounds
# --------------------------------------------------------------------------
class TestBounds:
    def test_the_request_ceiling_is_the_documented_one(self):
        assert MAX_RATE_NODE_IDS == 100
        schema = RateProposalRequest.model_json_schema()["properties"]["node_ids"]
        assert schema["maxItems"] == MAX_RATE_NODE_IDS
        assert schema["minItems"] == 1
        assert schema["items"]["minimum"] == 1
        assert schema["items"]["maximum"] == 9223372036854775807

    def test_one_id_over_the_ceiling_is_refused(self, client, store, plan):
        response = post(
            client, plan.project_id, list(range(1, MAX_RATE_NODE_IDS + 2))
        )
        assert response.status_code == 422

    def test_the_ceiling_itself_is_accepted_shape_wise(self, client, store, plan):
        # The named ids are mostly absent, so the answer is a 404 - the point is
        # that the shape at the ceiling passed the bound rather than hitting it.
        response = post(
            client, plan.project_id, list(range(1, MAX_RATE_NODE_IDS + 1))
        )
        assert response.status_code == 404

    def test_duplicates_cannot_breach_the_ceiling(self, client, store, plan):
        # Deduplication happens after the length bound, so a list of 101 copies
        # is refused rather than quietly collapsing to one id.
        response = post(client, plan.project_id, [plan.rate_id] * 101)
        assert response.status_code == 422

    def test_one_proposal_per_id_and_no_more(self, client, store, plan):
        body = post(client, plan.project_id, [plan.rate_id] * MAX_RATE_NODE_IDS)
        assert len(body.json()["proposals"]) == 1

    def test_a_truncated_document_read_is_reported_not_hidden(
        self, client, store, plan, monkeypatch
    ):
        # With the ceiling at 1 the source lookup cannot reach the second
        # document, so the row comes back unresolved. The request-level warning
        # is what stops that from reading as "this project has no such
        # document" - the row is unresolved because of a bound, not because the
        # document is gone.
        second = add_source_document(
            store, plan.project_id, file_name="synthetic-second.pdf",
            file_hash=HASH_C,
        )
        node_id = add_rate_node(
            store, plan.project_id, payload=rate_payload(file_hash=HASH_C),
            doc_id=second,
        )
        monkeypatch.setattr(rates_module, "MAX_RATE_SCANNED_DOCUMENTS", 1)
        body = post(client, plan.project_id, [node_id]).json()
        assert body["warnings"] == ["source_documents_truncated"]
        assert body["proposals"][0]["reason"] == "evidence_unavailable"

    def test_an_ordinary_project_reports_no_truncation(self, client, store, plan):
        body = post(client, plan.project_id, [plan.rate_id]).json()
        assert body["warnings"] == []
        assert MAX_RATE_SCANNED_DOCUMENTS > 1

    def test_a_long_label_is_cut_to_the_bound_and_still_answers(
        self, client, store, plan
    ):
        node_id = add_rate_node(
            store, plan.project_id, label="L" * (MAX_RATE_TEXT_CHARS + 50),
            doc_id=plan.document_id,
        )
        response = post(client, plan.project_id, [node_id])
        assert response.status_code == 200
        proposal = response.json()["proposals"][0]
        assert len(proposal["label"]) == MAX_RATE_TEXT_CHARS
        assert proposal["normalized_amount"] == "250.50"

    def test_a_long_source_file_name_is_cut_to_the_bound(
        self, client, store, plan
    ):
        document_id = add_source_document(
            store, plan.project_id, file_name="S" * (MAX_RATE_TEXT_CHARS + 50),
            file_hash=HASH_C,
        )
        node_id = add_rate_node(
            store, plan.project_id,
            payload=rate_payload(file_hash=HASH_C), doc_id=document_id,
        )
        response = post(client, plan.project_id, [node_id])
        assert response.status_code == 200
        assert len(response.json()["proposals"][0]["source_file_name"]) == (
            MAX_RATE_TEXT_CHARS
        )

    def test_every_rendered_string_is_inside_its_bound(self, client, store, plan):
        proposal = post(client, plan.project_id, [plan.rate_id]).json()[
            "proposals"
        ][0]
        assert len(proposal["source_file_hash"]) <= 64
        assert len(proposal["normalized_amount"]) <= MAX_RATE_AMOUNT_CHARS
        assert len(proposal["effective_date"]) <= 10
        assert len(proposal["source_file_name"]) <= MAX_RATE_TEXT_CHARS
        assert len(proposal["warnings"]) <= 8
        RateProposalDTO.model_validate(proposal)


class TestNoEgress:
    def test_the_rate_modules_import_no_network_client(self):
        # The static half of the guard: a module that cannot open a socket does
        # not need the runtime half to be trusted.
        normalize_path = (
            Path(rates_module.__file__).resolve().parent.parent
            / "rates" / "normalize.py"
        )
        source = Path(rates_module.__file__).read_text(encoding="utf-8")
        source += normalize_path.read_text(encoding="utf-8")
        for module in (
            "requests", "urllib", "httpx", "aiohttp", "socket",
            "http.client", "urllib3",
        ):
            assert module not in source, module

    def test_a_proposal_is_computed_with_no_socket_available(
        self, store, plan, monkeypatch
    ):
        # The runtime half, driven through the whole server-side path - project
        # lookup, rate read, document lookup, normalization and rendering - with
        # the process denied a socket. A rate that needs the network to be
        # normalized is not evidence-backed, it is somebody else's number.
        #
        # Called directly rather than through the HTTP client: the test
        # transport lives in the same process, and denying the process a socket
        # would disturb it in ways that have nothing to do with this route.
        def refuse(*args, **kwargs):
            raise AssertionError("the rate route opened a socket")

        monkeypatch.setattr(socket, "socket", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        response = rates_module.build_rate_proposals(
            store, plan.project_id, [plan.rate_id], today=TODAY
        )
        assert response.normalized == 1
        assert response.proposals[0].normalized_amount == "250.50"

