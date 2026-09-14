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
