"""Canonical revision identity, serialization and hashing (Phase 5B).

Every function here is pure: no store access, no clock, no locale, no
randomness. That is the point of a canonical form - the same stored evidence
must produce the same bytes, and therefore the same hash, on every machine and
in every run.

What is compared, and what is not
---------------------------------
The comparable content of an evidence row is exactly the set of columns
``evidence_nodes`` actually has: ``revision``, ``raw_text``, ``bbox`` and
``payload``. There is no ``quantity`` column on an evidence row, so nothing in
this module compares a quantity between two revisions; quantities live on
``quantity_claims`` and are reached only through the affected-claim mapping.

``file_hash`` is treated as provenance, not content. A re-export of the same
model always has a different hash, so treating it as content would report every
row as changed on every revision and the diff would say nothing.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------

#: Any run of whitespace collapses to one space before comparison.
_WHITESPACE_RUN = re.compile(r"\s+")

#: Decimal places kept on a stored float. The ingest rounds QS dimensions to 3;
#: six leaves room for PDF point coordinates without inviting float noise into
#: the comparison.
FLOAT_PRECISION = 6

#: Payload keys that describe where a value came from rather than what it is.
#: They change on every re-export, so they are removed from the compared
#: content - but kept on the row, because identity depends on them.
PROVENANCE_PAYLOAD_KEYS = frozenset(
    {"file_hash", "doc_id", "ingested_at", "source_file"}
)

#: Payload keys whose value is a unit, folded through :func:`normalize_unit`.
UNIT_PAYLOAD_KEYS = frozenset({"unit", "units"})

#: Unit spellings folded onto the canonical value used by ``contracts.Unit``.
UNIT_ALIASES = {
    "m": "m", "metre": "m", "metres": "m", "meter": "m", "meters": "m", "lm": "m",
    "m2": "m2", "m^2": "m2", "sqm": "m2", "sq.m": "m2", "squaremetre": "m2",
    "m3": "m3", "m^3": "m3", "cum": "m3", "cu.m": "m3", "cubicmetre": "m3",
    "mm": "mm", "millimetre": "mm", "millimetres": "mm",
    "kg": "kg", "kilogram": "kg", "kilograms": "kg",
    "t": "t", "tonne": "t", "tonnes": "t", "ton": "t", "tons": "t",
    "ea": "ea", "each": "ea", "no": "ea", "nr": "ea", "unit": "ea",
    "item": "item", "hr": "hr", "hour": "hr", "hours": "hr",
    "l": "L", "litre": "L", "litres": "L", "liter": "L", "liters": "L",
}

#: An evidence row whose stored JSON does not decode, or is the wrong shape.
FLAG_MALFORMED_EVIDENCE = "malformed_evidence"

#: An evidence row with nothing comparable on it: no payload content beyond
#: provenance, no raw text and no bounding box. The row exists, but it cannot
#: support a statement about what changed.
FLAG_INSUFFICIENT_EVIDENCE = "insufficient_evidence"


def _flatten(value: Any) -> Optional[str]:
    """Collapse whitespace, strip, and treat empty as missing."""
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    collapsed = _WHITESPACE_RUN.sub(" ", text).strip()
    return collapsed or None


def normalize_identifier(value: Any) -> Optional[str]:
    """A case-insensitive identifier: drawing number, revision, sheet, zone.

    Case is folded because ``"c-204"``, ``"C-204"`` and ``"C-204 "`` are the
    same drawing to everyone except a string comparison. ``None``, ``""`` and
    whitespace all collapse to ``None``, because the store writes NULL for a
    value the source did not contain and an empty string means the same thing.
    """
    text = _flatten(value)
    return None if text is None else text.casefold()


def normalize_text(value: Any) -> Optional[str]:
    """Verbatim text with layout noise removed, but case preserved.

    ``raw_text`` and ``file_name`` are quoted from a source document, so case
    is part of the reading: ``"DN600"`` and ``"dn600"`` are not the same
    string, and folding them would hide a genuine re-labelling.
    """
    return _flatten(value)


def normalize_number(value: Any) -> Optional[float]:
    """A finite float rounded to :data:`FLOAT_PRECISION`, or ``None``.

    ``NaN`` and the infinities are not measurements, so they are reported as
    missing rather than propagated into a hash. ``-0.0`` and ``0.0`` compare
    equal, so they are stored as one value.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    rounded = round(number, FLOAT_PRECISION)
    return 0.0 if rounded == 0 else rounded


def normalize_bbox(value: Any) -> Optional[tuple[float, float, float, float]]:
    """``(x0, y0, x1, y1)`` in PDF points, rounded, or ``None``.

    Accepts the JSON text the store holds, or an already-decoded sequence. A
    box that is not exactly four finite numbers is malformed; malformed is
    reported as ``None``, never repaired into a plausible box.
    """
    if value is None:
        return None
    decoded: Any = value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return None
    if isinstance(decoded, (str, bytes)) or not isinstance(decoded, Sequence):
        return None
    if len(decoded) != 4:
        return None
    numbers = [normalize_number(part) for part in decoded]
    if any(part is None for part in numbers):
        return None
    return (
        numbers[0],  # type: ignore[return-value]
        numbers[1],
        numbers[2],
        numbers[3],
    )


def normalize_unit(value: Any) -> Optional[str]:
    """A unit folded onto its canonical spelling (``"M3"`` -> ``"m3"``)."""
    text = _flatten(value)
    if text is None:
        return None
    compact = text.casefold().replace(" ", "")
    return UNIT_ALIASES.get(compact, compact)


def _canonical_value(value: Any) -> Any:
    """Recursively reduce a value to something ``json.dumps`` writes identically."""
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return normalize_number(value)
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_scalar(str(key), inner)
            for key, inner in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(inner) for inner in value]
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    inner = getattr(value, "value", None)
    if inner is not None and not isinstance(value, (bytes, bytearray)):
        return _canonical_value(inner)
    return str(value)


def _canonical_scalar(key: str, value: Any) -> Any:
    """One payload entry: units folded, everything else canonicalized."""
    if key.casefold() in UNIT_PAYLOAD_KEYS:
        return normalize_unit(value)
    return _canonical_value(value)


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no spaces, ASCII only.

    ASCII-only matters because the digest is computed over these exact bytes.
    """
    return json.dumps(
        _canonical_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def canonical_hash(value: Any) -> str:
    """Lowercase 64-character SHA-256 over :func:`canonical_json`."""
    return hashlib.sha256(canonical_json(value).encode("ascii")).hexdigest()


def canonical_payload(payload: Any) -> Optional[dict[str, Any]]:
    """The canonical form of an evidence payload, or ``None`` if malformed.

    Returns ``None`` when the stored value is not a JSON object - a payload
    that is not a mapping is malformed, and the caller reports it as
    unresolved instead of guessing at its contents. Every key is kept:
    provenance keys are needed to place the row in its document, so stripping
    them here would lose information the identity step depends on. The
    compared content is :attr:`StoredEvidence.content_payload`, which is where
    provenance is removed.
    """
    decoded: Any = payload
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except ValueError:
            return None
    if not isinstance(decoded, Mapping):
        return None
    return {
        str(key): _canonical_scalar(str(key), inner)
        for key, inner in decoded.items()
    }


# --------------------------------------------------------------------------
# Canonical revision identity
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredEvidence:
    """One ``evidence_nodes`` row, normalized. Internal - not an API DTO.

    Only columns that exist in the store appear here, and every field is
    normalized on construction, so two views of byte-identical rows compare
    equal and two views of rows differing only in whitespace or case also
    compare equal.
    """

    node_id: int
    node_type: str
    label: str
    ingest_key: Optional[str]
    file_hash: Optional[str]
    drawing_no: Optional[str]
    revision: Optional[str]
    sheet: Optional[str]
    page: Optional[int]
    zone: Optional[str]
    bbox: Optional[tuple[float, float, float, float]]
    raw_text: Optional[str]
    payload: Optional[dict[str, Any]]
    flags: tuple[str, ...]

    # ---------------------------------------------------------------- identity

    @property
    def pairing_key(self) -> tuple[Any, ...]:
        """What must be equal for two rows to describe the same thing.

        Deliberately excludes everything a new revision is expected to change -
        the file hash, the revision, the label (a re-export is commonly
        renamed) and the locator, which is the difference being reported. The
        page is kept: page 3 of a drawing is not page 4 of it.
        """
        return (
            normalize_identifier(self.node_type),
            normalize_identifier(self.drawing_no),
            normalize_identifier(self.sheet),
            normalize_identifier(self.zone),
            self.page,
        )

    @property
    def identity_id(self) -> str:
        """Stable, bounded id for this pairing key - what a client keys on."""
        node_type, drawing_no, sheet, zone, page = self.pairing_key
        return canonical_hash(
            {
                "node_type": node_type,
                "drawing_no": drawing_no,
                "sheet": sheet,
                "zone": zone,
                "page": page,
            }
        )

    @property
    def content_payload(self) -> Optional[dict[str, Any]]:
        """The payload with provenance removed - what is actually compared."""
        if self.payload is None:
            return None
        return {
            key: value
            for key, value in self.payload.items()
            if key.casefold() not in PROVENANCE_PAYLOAD_KEYS
        }

    @property
    def content(self) -> dict[str, Any]:
        """The compared fields, as canonical comparable values."""
        return {
            "label": self.label,
            "revision": normalize_identifier(self.revision),
            "raw_text": normalize_text(self.raw_text),
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "payload": self.content_payload,
        }

    @property
    def comparable(self) -> bool:
        """True when the row holds anything at all that can be compared."""
        if self.raw_text is not None or self.bbox is not None:
            return True
        return bool(self.content_payload)

    @property
    def has_locator(self) -> bool:
        """True when the row carries the exact spot a number came from.

        This is the column-level locator, not the payload. No ingest path in
        this repository writes one, so in practice every machine row answers
        False - which is why the affected-claim mapping reports unassociated
        claims rather than matching them on the source hash alone.
        """
        return self.raw_text is not None or self.bbox is not None

    @property
    def reliable(self) -> bool:
        """False when the row cannot support a claim about what changed."""
        return not self.flags

    @property
    def reason(self) -> Optional[str]:
        """Why this row is not reliable, for the response."""
        return self.flags[0] if self.flags else None

    @property
    def sort_key(self) -> tuple[str, str, int]:
        """Total order over rows, so grouping never depends on row order."""
        return (self.ingest_key or "", self.identity_id, self.node_id)

    # ---------------------------------------------------------------- reading

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "StoredEvidence":
        """Build a view from a ``sqlite3.Row`` (or any mapping of the columns).

        Provenance is read with a documented precedence: the column first, the
        payload second. No ingest path in this repository populates the
        provenance columns - ``ingest/cli.py`` writes ``file_hash``, ``doc_id``,
        ``sheet`` and the drawing identity into ``payload`` and passes no
        ``ref`` to ``add_node`` - so the payload is where the values actually
        are. The column is still consulted first so that a caller which does
        populate it is not overruled.
        """
        payload = canonical_payload(row["payload"])

        def sourced(column: str, key: Optional[str] = None) -> Any:
            value = row[column]
            if value is not None:
                return value
            if payload is None:
                return None
            return payload.get(key or column)

        raw_text = normalize_text(row["raw_text"])
        bbox_text = row["bbox"]
        bbox = normalize_bbox(bbox_text)

        page = sourced("page")
        if page is not None:
            try:
                page = int(page)
            except (TypeError, ValueError):
                page = None

        flags: list[str] = []
        if payload is None or (bbox_text is not None and bbox is None):
            flags.append(FLAG_MALFORMED_EVIDENCE)

        view = cls(
            node_id=int(row["id"]),
            node_type=normalize_text(row["node_type"]) or "unknown",
            label=normalize_text(row["label"]) or "",
            ingest_key=normalize_text(row["ingest_key"]),
            file_hash=normalize_identifier(sourced("file_hash")),
            drawing_no=normalize_text(sourced("drawing_no")),
            revision=normalize_text(sourced("revision")),
            sheet=normalize_text(sourced("sheet")),
            page=page,
            zone=normalize_text(sourced("zone")),
            bbox=bbox,
            raw_text=raw_text,
            payload=payload,
            flags=(),
        )

        if not view.comparable:
            flags.append(FLAG_INSUFFICIENT_EVIDENCE)
        return replace(view, flags=tuple(flags))




@dataclass(frozen=True)
class ClaimReference:
    """One ``EvidenceRef`` from a claim, canonicalized for exact matching.

    Field names follow ``contracts.evidence.EvidenceRef``. ``file_name`` is the
    exception to the rule that everything here maps to an ``evidence_nodes``
    column: no such column exists, and ``label`` is not one either - drawing
    nodes happen to carry a file name there while quantity nodes carry
    ``"<WBS>: <operation>"``. The name is therefore matched against the
    ``documents`` row for the same file hash, which is the lineage the ingest
    actually writes.
    """

    file_hash: Optional[str]
    file_name: Optional[str]
    drawing_no: Optional[str]
    revision: Optional[str]
    sheet: Optional[str]
    page: Optional[int]
    zone: Optional[str]
    bbox: Optional[tuple[float, float, float, float]]
    raw_text: Optional[str]

    @classmethod
    def from_json(cls, value: Any) -> Optional["ClaimReference"]:
        """Parse one stored reference, or ``None`` if it is not usable.

        A reference with no file hash cannot be attributed to a source
        document, so it is not usable and is reported as unresolved rather than
        matched loosely.
        """
        if not isinstance(value, Mapping):
            return None
        file_hash = normalize_identifier(value.get("file_hash"))
        if not file_hash:
            return None
        page = value.get("page")
        if page is not None:
            try:
                page = int(page)
            except (TypeError, ValueError):
                page = None
        return cls(
            file_hash=file_hash,
            file_name=normalize_text(value.get("file_name")),
            drawing_no=normalize_text(value.get("drawing_no")),
            revision=normalize_text(value.get("revision")),
            sheet=normalize_text(value.get("sheet")),
            page=page,
            zone=normalize_text(value.get("zone")),
            bbox=normalize_bbox(value.get("bbox")),
            raw_text=normalize_text(value.get("raw_text")),
        )

    def matches_node(self, node: StoredEvidence, file_name: Optional[str]) -> bool:
        """Exact match on every field the node actually carries.

        The node is the authority on which fields exist: a field it holds a
        value for must agree with the reference, and a field it leaves empty
        says nothing about this claim, so an empty field is not invented as a
        mismatch.

        Two conditions are absolute. The source hash must match, and the node
        must carry a locator - a stored ``raw_text`` or ``bbox``. Without one
        there is nothing tying the reference to *this* row rather than to any
        other row from the same file, and an association made on the source
        hash alone would mark every claim citing the document as affected by
        every change in it. :func:`map_affected_claims` counts those claims as
        unassociated rather than guessing.
        """
        if node.file_hash != self.file_hash:
            return False
        if not node.has_locator:
            return False
        if node.pairing_key != (
            normalize_identifier(node.node_type),
            normalize_identifier(self.drawing_no),
            normalize_identifier(self.sheet),
            normalize_identifier(self.zone),
            self.page,
        ):
            return False
        if normalize_identifier(node.revision) != normalize_identifier(self.revision):
            return False
        if normalize_text(node.raw_text) != self.raw_text:
            return False
        if node.bbox != self.bbox:
            return False
        return normalize_text(file_name) == self.file_name


