/// Bounds the workstation client enforces on the gateway's JSON.
///
/// These mirror the server's own constants rather than trusting them. The
/// server is the authority - it already forbids unknown keys and bounds every
/// string - but a client that renders whatever arrives inherits whatever the
/// server grows later. Both sides are asserted equal by
/// `backend/tests/test_workstation_contract_mirror.py`.
///
/// Every value here has one job: keep a hostile or buggy response from becoming
/// a megabyte of text in a widget, or a project id that cannot survive a round
/// trip through a Dart web integer.
library;

/// Mirrors `qsagent.api.contracts.MAX_PROJECT_NAME_CHARS`.
const int maxProjectNameChars = 200;

/// Mirrors `qsagent.api.contracts.MAX_TASK_ID_CHARS`. Not used by a 5A DTO; it
/// is asserted against the server so the mirror list stays honest.
const int maxTaskIdChars = 64;

/// Longest `status` string the client will render from `/api/v1/health`.
///
/// The server sends `"ok"`. The client does not need to accept more, and a
/// bound that only exists on the server becomes an unbounded label the moment
/// the server changes.
const int maxHealthStatusChars = 50;

/// Largest project id the client accepts.
///
/// `2^53 - 1`: the largest integer a Dart web build represents exactly. A
/// larger value would be silently rounded before the URL was even built, so it
/// is refused instead of sent as a different number.
const int maxProjectId = 9007199254740991;

/// Longest request path the client will construct, in characters.
const int maxRequestPathChars = 256;

// ── Phase 5B: revision diff ──────────────────────────────────────────────
// Every one of these mirrors `qsagent.api.contracts`, except the two marked
// client-only. A bound that exists on only one side of the wire is the drift
// `backend/tests/test_workstation_contract_mirror.py` is written to catch.

/// Mirrors `qsagent.api.contracts.MAX_DIFF_ITEMS`.
const int maxDiffItems = 500;

/// Mirrors `qsagent.api.contracts.MAX_DIFF_FIELDS`.
const int maxDiffFields = 32;

/// Mirrors `qsagent.api.contracts.MAX_DIFF_AFFECTED_CLAIMS`.
const int maxDiffAffectedClaims = 50;

/// Mirrors `qsagent.api.contracts.MAX_DIFF_WARNINGS`.
const int maxDiffWarnings = 8;

/// Mirrors `qsagent.api.contracts.MAX_DIFF_TEXT_CHARS`.
const int maxDiffTextChars = 255;

/// Mirrors `qsagent.api.contracts.MAX_DIFF_REASON_CHARS`.
const int maxDiffReasonChars = 200;

/// Mirrors `qsagent.api.contracts.MAX_DIFF_HASH_CHARS`. Also the bound on a
/// canonical identity, which is the same length.
const int maxDiffHashChars = 64;

/// Mirrors `qsagent.api.contracts.MAX_DIFF_CLAIM_ID_CHARS`.
const int maxDiffClaimIdChars = 64;

/// Largest document id the client accepts. Client-only: the server has no
/// equivalent constant because an integer needs no bound to be stored. The
/// bound exists here for the same reason [maxProjectId] does - `2^53 - 1` is
/// the largest integer a Dart web build represents exactly, and a larger value
/// would be silently rounded before the URL was built.
const int maxDocumentId = 9007199254740991;

/// Largest page number the client will render. Client-only; a PDF with more
/// than a million pages is not a document, and an unbounded integer from the
/// wire is a number a widget has to be willing to format.
const int maxPageNumber = 1000000;

/// Mirrors `qsagent.api.contracts.MAX_DIFF_SCANNED_NODES`. Bounds the
/// `evidence_rows` count, so a client cannot be told a document has more rows
/// than the server would ever read.
const int maxDiffScannedNodes = 2000;

/// Mirrors `qsagent.api.contracts.MAX_DIFF_SCANNED_CLAIMS`. Bounds the claim
/// counters.
const int maxDiffScannedClaims = 2000;

/// Largest count a diff response can report.
///
/// A footprint group holds rows from both sides, so the ceiling is twice one
/// side's read ceiling. Derived rather than mirrored, because it is a
/// consequence of two constants rather than a constant of its own.
const int maxDiffCount = maxDiffScannedNodes * 2;

// ── Phase 5C: evidence-backed rate proposals ─────────────────────────────
// The first response this client renders that carries money. Every bound below
// either mirrors `qsagent.api.contracts` or says, in its own words, why it is
// the client's own. A rate is quoted as a decimal string, so the string bounds
// are the ones that stop a value from becoming an unbounded label.

/// Mirrors `qsagent.api.contracts.MAX_RATE_NODE_IDS`.
///
/// The number of ids one request may name, and therefore the ceiling on the
/// proposal list: one proposal per id. It bounds *both* directions, which is why
/// the client validates the request list against it before sending.
const int maxRateNodeIds = 100;

/// Mirrors `qsagent.api.contracts.MAX_RATE_TEXT_CHARS`.
const int maxRateTextChars = 255;

/// Mirrors `qsagent.api.contracts.MAX_RATE_HASH_CHARS`.
const int maxRateHashChars = 64;

/// Mirrors `qsagent.api.contracts.MAX_RATE_AMOUNT_CHARS`.
///
/// The bound on `original_amount` and `normalized_amount`, which are decimal
/// *strings* rather than numbers. The client renders them as text and never
/// parses them: `Decimal` arithmetic belongs to the server, and a client that
/// recomputed a rate would be a second implementation of the thing the platform
/// exists to make single.
const int maxRateAmountChars = 64;

/// Mirrors `qsagent.api.contracts.MAX_RATE_DATE_CHARS`.
const int maxRateDateChars = 10;

/// Mirrors `qsagent.api.contracts.MAX_RATE_WARNINGS`.
const int maxRateWarnings = 8;

/// Largest value the client accepts for a rate field the server leaves
/// unbounded.
///
/// Client-only, and it covers two different jobs, which is why it is not named
/// after either of them:
///
/// * a **rate node id**. The server's own ceiling is `MAX_RATE_NODE_ID`
///   (`9223372036854775807`), which is larger than this one, so this is the
///   stricter of the two. A larger id would be silently rounded on the way into
///   a URL in a Dart web build, so it is refused here instead of being sent as a
///   different number;
/// * `duplicate_quote_count` and `source_age_days`, which the server declares as
///   plain `int` with no ceiling at all. An unbounded integer from the wire is a
///   number a widget has to be willing to format, so the client bounds it.
///
/// `2^53 - 1` is the largest integer a Dart web build represents exactly - the
/// same bound, for the same reason, as [maxProjectId].
const int maxRateExactInt = 9007199254740991;

/// Mirrors `qsagent.api.contracts.MAX_RATE_SCANNED_DOCUMENTS`. Not rendered by
/// this client; it is asserted against the server so the mirror list stays
/// honest, and it is the ceiling that makes the server's
/// `source_documents_truncated` warning meaningful rather than mysterious.
const int maxRateScannedDocuments = 2000;


