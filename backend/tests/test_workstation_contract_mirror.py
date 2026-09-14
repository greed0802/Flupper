"""Guard: the workstation client and the gateway must agree, and stay apart.

The Flutter client in ``workstation/`` is a second implementation of the same
contract. Two failure modes follow from that, and this file makes both of them
test failures rather than review notes:

* **Drift.** A hand-written Dart mirror of a route or a bound goes stale the
  moment the server changes. The bounds are compared against the Pydantic models
  themselves and the routes against the app's own routing table, so the mirror
  cannot pass by agreeing with a comment.
* **Scope creep in the client.** 5A is read-only and memory-only. The last tests
  scan the Dart sources for the things that would quietly change that - a
  browser storage API, a secure-storage or preferences dependency, a token read
  from the compile-time environment, or a bare ``print``.

What this file is *not*: a substitute for running ``flutter test``. A green run
here proves the shapes line up; only the Dart suite proves the behaviour.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from qsagent.api import create_app
from qsagent.api.contracts import (
    MAX_PROJECT_NAME_CHARS,
    MAX_TASK_ID_CHARS,
    HealthResponse,
    ProjectResponse,
)
from qsagent.runtime import ModelRouter
from qsagent.storage import QSStore

REPO = Path(__file__).resolve().parents[2]
WORKSTATION = REPO / "workstation"
DART_LIB = WORKSTATION / "lib"

# Not a secret: it exists so the app can be built, and it never leaves here.
TEST_TOKEN = "workstation-contract-test-token-0123456789"

# The one route literal in the Dart client that is not itself a route.
PREFIX_LITERAL = "/api/v1"


def _dart_sources() -> list[Path]:
    return sorted(DART_LIB.rglob("*.dart"))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class _NoSecrets:
    """Structural ``SecretProvider`` that resolves nothing.

    The app is built only to read its routing table; no route is called, so no
    provider key is ever needed and none is invented here.
    """

    def get_key(self, provider: str) -> str | None:
        return None


def _served_paths() -> set[str]:
    """Every path the real app serves, read from its own routing table."""
    store = QSStore(":memory:")
    try:
        app = create_app(
            store=store,
            router=ModelRouter(_NoSecrets()),
            api_token=TEST_TOKEN,
            sandbox_root=Path("sandbox_runs_never_created"),
        )
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
    finally:
        store.close()


@pytest.fixture(scope="module")
def served_paths() -> set[str]:
    return _served_paths()


def test_the_workstation_tree_is_present():
    # Without this, every scan below could pass by scanning nothing.
    assert DART_LIB.is_dir(), f"expected Dart sources under {DART_LIB}"
    assert len(_dart_sources()) >= 8


def test_read_only_routes_the_client_uses_are_still_served(served_paths: set[str]):
    assert "/api/v1/health" in served_paths
    assert "/api/v1/projects/{project_id}" in served_paths


def test_the_client_uses_no_route_the_gateway_does_not_serve(served_paths: set[str]):
    """Every route literal in the client resolves to a real route.

    A literal that is only the prefix constant, or that carries a Dart
    interpolation where the route declares a path parameter, is normalised
    first, so a typo under ``/api/v1`` fails here rather than at runtime.
    """
    text = _read(DART_LIB / "core" / "api_client.dart")
    literals = set(re.findall(r"'(/api/v1[^']*)'", text))
    assert literals, "no route literals found; the scan is looking at nothing"

    checked = 0
    for literal in literals:
        if literal.rstrip("/") == PREFIX_LITERAL:
            continue
        candidate = literal.replace("$projectId", "{project_id}")
        assert candidate in served_paths, f"{literal} is not a served route"
        checked += 1

    assert checked >= 2, f"expected at least two routes, saw {checked}"


def test_dart_bounds_mirror_the_server_constants():
    limits = _read(DART_LIB / "core" / "dto_limits.dart")

    def constant(name: str) -> int:
        match = re.search(rf"const int {name} = (\d+);", limits)
        assert match, f"{name} is not declared as an int literal"
        return int(match.group(1))

    assert constant("maxProjectNameChars") == MAX_PROJECT_NAME_CHARS
    assert constant("maxTaskIdChars") == MAX_TASK_ID_CHARS


def test_dart_dtos_declare_exactly_the_server_fields():
    pairs = (
        (HealthResponse, DART_LIB / "core" / "dto" / "health_response.dart"),
        (ProjectResponse, DART_LIB / "core" / "dto" / "project_response.dart"),
    )
    for model, path in pairs:
        text = _read(path)
        keys_match = re.search(r"keys = <String>\{([^}]*)\}", text)
        assert keys_match, f"{path.name} does not declare a key set"
        dart_keys = set(re.findall(r"'([^']+)'", keys_match.group(1)))
        assert dart_keys == set(model.model_fields), (
            f"{path.name} declares {sorted(dart_keys)}, "
            f"server models {sorted(model.model_fields)}"
        )
        # The readers must actually read those keys, or the key set is decoration.
        for field in model.model_fields:
            assert f"'{field}'" in text, f"{path.name} never reads {field}"


def test_the_client_calls_nothing_that_would_persist_a_token():
    """The 5A promise, made falsifiable.

    Scanning the Dart sources for these patterns is crude, and that is the
    point: each one is a single grep away from being reintroduced, so a test
    that fails on the pattern is the cheapest guard available.
    """
    forbidden = (
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "document.cookie",
        "dart:html",
        "flutter_secure_storage",
        "shared_preferences",
    )
    offenders: list[str] = []
    for path in _dart_sources():
        text = _read(path)
        for pattern in forbidden:
            if pattern in text:
                offenders.append(f"{path.relative_to(REPO).as_posix()}: {pattern}")
    assert offenders == [], f"token-persistence patterns found: {offenders}"


def test_no_token_is_read_from_the_compile_time_environment():
    """A base address may be compiled in; a credential may not."""
    offenders: list[str] = []
    for path in _dart_sources():
        for name in re.findall(
            r"String\.fromEnvironment\(\s*'([^']*)'", _read(path)
        ):
            if re.search(r"TOKEN|SECRET|PASSWORD|KEY", name, re.IGNORECASE):
                offenders.append(f"{path.relative_to(REPO).as_posix()}: {name}")
    assert offenders == [], f"compile-time credential reads found: {offenders}"

    # The one compile-time read that is allowed, asserted so the guard above is
    # not passing merely because the scan found no reads at all.
    client = _read(DART_LIB / "core" / "api_config.dart")
    assert "String.fromEnvironment('FLUPPER_API_URL')" in client


def test_the_dart_sources_do_not_print():
    offenders = [
        path.relative_to(REPO).as_posix()
        for path in _dart_sources()
        if re.search(r"(?<![\w.])print\(", _read(path))
    ]
    assert offenders == [], f"print() found in: {offenders}"


def test_persistence_is_declared_off_in_the_client():
    text = _read(DART_LIB / "core" / "auth_storage.dart")
    assert re.search(r"persistenceEnabled\s*=\s*false\s*;", text), (
        "AuthStorage must declare persistence disabled"
    )
