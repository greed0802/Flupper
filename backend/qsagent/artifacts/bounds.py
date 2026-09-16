"""Phase 5D bounds - one place, so no export ceiling is a hope.

Every number in this module is a ceiling on something a caller can observe: a
row the workbook may contain, a character a cell may hold, a byte on disk, a
second an artifact stays readable. They live here rather than inside the builder
because the route, the registry and the tests all have to agree on them, and a
bound that exists only inside the function that applies it is not a bound anyone
can check.

The workbook layout is also fixed here, and deliberately not negotiable: the
Claims sheet places Quantity in column C and the resolved rate in column E, so
the extended-amount formula is ``=C{row}*E{row}`` for a row index the server
computes. A layout constant that moved would silently change what every formula
in the file means, which is why the tests assert the header row verbatim.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Workbook layout - fixed, server-owned, never client-supplied
# --------------------------------------------------------------------------
#: Row 1 of every sheet carries the server's own non-deliverable banner. It is
#: present unconditionally so the layout never varies: a reader can always find
#: the headers on row 2, and a test can always find a data row from row 3.
BANNER_ROW = 1
HEADER_ROW = 2
#: Server-calculated. Nothing in the builder may assume this value instead of
#: reading it, and the formula tests assert it against the written cells.
FIRST_DATA_ROW = 3

SHEET_CLAIMS = "Claims"
SHEET_ASSUMPTIONS = "Assumptions"
SHEET_CHECKMATE = "CheckMate"
SHEET_NAMES: tuple[str, ...] = (SHEET_CLAIMS, SHEET_ASSUMPTIONS, SHEET_CHECKMATE)

#: Order is the schema: A Claim ID, B Description, C Quantity, D Unit,
#: E Normalized Rate, F Extended Amount, G Measurement State, H Status,
#: I Evidence / Provenance.
CLAIM_COLUMNS: tuple[str, ...] = (
    "Claim ID",
    "Description",
    "Quantity",
    "Unit",
    "Normalized Rate",
    "Extended Amount",
    "Measurement State",
    "Status",
    "Evidence / Provenance",
)

ASSUMPTION_COLUMNS: tuple[str, ...] = (
    "Assumption ID",
    "Statement",
    "Status",
    "Rationale",
    "Impact (AUD)",
    "Impact Quantity",
    "Impact Unit",
    "Evidence",
    "Raised At",
    "Resolved At",
)

CHECKMATE_COLUMNS: tuple[str, ...] = (
    "Subject",
    "Passed",
    "Findings",
    "Recorded At",
)

#: The widest sheet. A new column on any sheet is a test failure until this
#: moves with it.
MAX_EXPORT_COLUMNS = max(
    len(CLAIM_COLUMNS), len(ASSUMPTION_COLUMNS), len(CHECKMATE_COLUMNS)
)
MAX_EXPORT_SHEETS = len(SHEET_NAMES)

# --------------------------------------------------------------------------
# Row ceilings
# --------------------------------------------------------------------------
#: Claims one workbook may contain. Exceeding it is a refusal, not a clip: an
#: export that silently dropped rows would read as "this project has 500
#: claims", which is the one thing an evidence-first artifact must never say.
MAX_EXPORT_CLAIMS = 500
MAX_EXPORT_ASSUMPTIONS = 500
MAX_EXPORT_CHECKMATE_ROWS = 500

#: Caller-named claim-to-rate pairings one request may carry. It mirrors the
#: claim ceiling because a pairing is at most one per exported claim.
MAX_EXPORT_RATE_BINDINGS = MAX_EXPORT_CLAIMS

#: Evidence references written into one claim row. Only the first N are
#: included; more references exist in the store but the cell ceiling prevents
#: an unbounded assembly string.
MAX_EXPORT_EVIDENCE_PER_CLAIM = 5


# --------------------------------------------------------------------------
# Cell and formula ceilings
# --------------------------------------------------------------------------
#: Characters a single text cell may hold. Both the source text clipped at read
#: time and the assembled cell are bounded by it.
MAX_EXPORT_CELL_CHARS = 10_000
#: Characters of one source-derived field (a locator, a file name, a rationale)
#: before it is clipped. Well inside :data:`MAX_EXPORT_CELL_CHARS` so an
#: assembled cell cannot be cut in half.
MAX_EXPORT_TEXT_CHARS = 255
#: A single server-generated formula. The two shapes written here are
#: ``=C{row}*E{row}`` and ``=SUM(F{first}:F{last})``, both far inside this.
MAX_EXPORT_FORMULA_CHARS = 64
#: Formula cells per workbook: one per claim row, plus the total row.
MAX_EXPORT_FORMULA_CELLS = MAX_EXPORT_CLAIMS + 1

MAX_EXPORT_WARNINGS = 12

# --------------------------------------------------------------------------
# Artifact lifecycle
# --------------------------------------------------------------------------
#: Bytes a generated artifact may occupy. Charged against the in-memory
#: workbook before it is written, and again against the file on disk before any
#: byte is read back.
MAX_EXPORT_BYTES = 5 * 1024 * 1024
#: Seconds an artifact stays downloadable after creation.
MAX_ARTIFACT_AGE_SECONDS = 300
DEFAULT_ARTIFACT_TTL_SECONDS = float(MAX_ARTIFACT_AGE_SECONDS)
#: How often the application-owned cleanup task sweeps expired artifacts.
ARTIFACT_CLEANUP_INTERVAL_SECONDS = 30.0

#: ``uuid4()`` canonical form. The route parses the path segment as a UUID, and
#: the registry refuses any id that is not this length and shape.
ARTIFACT_ID_CHARS = 36

EXPORT_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# --------------------------------------------------------------------------
# Sentinel vocabulary
# --------------------------------------------------------------------------
#: A number the store could not establish. Never written as a formula cell.
VALUE_UNRESOLVED = "Unresolved"
#: A stored record that could not be read or resolved. Distinct from the above
#: on purpose: "no number" and "no row" are different findings.
VALUE_UNAVAILABLE = "Unavailable"

#: The one allowlisted export type. A request naming anything else is a 422
#: before any store read happens.
EXPORT_TYPE_BOQ_XLSX = "boq_xlsx"

#: Claim-level outcome vocabulary. ``REJECTED`` is deliberately absent: no
#: column in this store records a verdict against a quantity claim, so a row
#: can never honestly carry one. The artifact-level gate is where a stored
#: failed CheckMate result lands - see :data:`GATE_REJECTED`.
STATUS_ACCEPTED = "ACCEPTED"
STATUS_REVIEW = "REVIEW"
STATUS_UNRESOLVED = "UNRESOLVED"
CLAIM_STATUSES: tuple[str, ...] = (
    STATUS_ACCEPTED,
    STATUS_REVIEW,
    STATUS_UNRESOLVED,
)

#: Artifact-level gate, derived only from stored ``checkmate_results``.
GATE_PASSED = "PASSED"
GATE_REJECTED = "REJECTED"
GATE_NOT_RUN = "NOT_RUN"
