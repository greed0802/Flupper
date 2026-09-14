"""Cloudflare Tunnel launcher tests (Phase 4, task C).

Everything here is synthetic. The hostname is a reserved ``.invalid`` name, the
tunnel identifier is an invented UUID that belongs to no Cloudflare account, and
the credential file is an empty file under ``tmp_path`` that nothing ever reads.
No tunnel is created, no connection is made, and no real credential exists.

Driving the connector for real is impossible without ``cloudflared`` installed,
so that one integration test skips with a reason rather than quietly passing.
Every other assertion is about validation and supervision, which is pure Python
and runs everywhere.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from qsagent.api import API_TOKEN_ENV_VAR, run_local
from qsagent.api import tunnel as tunnel_mod
from qsagent.api.tunnel import (
    API_INGRESS_KEYS,
    CATCH_ALL_SERVICE,
    CLOUDFLARED_BINARY,
    MANIFEST_KEYS,
    TUNNEL_HOSTNAME_ENV_VAR,
    TUNNEL_ORIGIN_SERVICE,
    TunnelConfigError,
    load_manifest,
    main,
)

# Reserved for documentation and testing (RFC 2606). Never the owner's real name:
# that arrives through FLUPPER_TUNNEL_HOSTNAME at run time and is never committed.
HOSTNAME = "api.example.invalid"
OTHER_HOSTNAME = "api.other.example.invalid"
# Deliberately an all-zero placeholder so it cannot be mistaken for a real tunnel
# identifier in a scan. Well-formed enough that uuid validation accepts it.
TUNNEL_ID = "00000000-0000-4000-8000-000000000000"
API_TOKEN = "gateway-test-token-0123456789abcdef"

BACKEND = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------
@pytest.fixture()
def credentials_file(tmp_path) -> Path:
    """A stand-in for cloudflared's credential file. Contents are never read."""
    path = tmp_path / "tunnel-credentials.json"
    path.write_text("{}", encoding="utf-8")
    return path


@pytest.fixture()
def manifest(credentials_file) -> dict:
    return {
        "tunnel": TUNNEL_ID,
        "credentials-file": str(credentials_file),
        "ingress": [
            {"hostname": HOSTNAME, "service": TUNNEL_ORIGIN_SERVICE},
            {"service": CATCH_ALL_SERVICE},
        ],
    }


@pytest.fixture()
def env(monkeypatch):
    """The environment a correctly configured deployment would have."""
    monkeypatch.setenv(API_TOKEN_ENV_VAR, API_TOKEN)
    monkeypatch.setenv(TUNNEL_HOSTNAME_ENV_VAR, HOSTNAME)
    return {
        API_TOKEN_ENV_VAR: API_TOKEN,
        TUNNEL_HOSTNAME_ENV_VAR: HOSTNAME,
    }


def write_manifest(tmp_path, payload) -> Path:
    path = tmp_path / "tunnel-manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def refusal(payload, *, expected_hostname: str = HOSTNAME) -> str:
    """Return the refusal message for *payload*, asserting it is refused."""
    with pytest.raises(TunnelConfigError) as excinfo:
        tunnel_mod.validate_manifest(payload, expected_hostname=expected_hostname)
    return str(excinfo.value)


def spawn_sleeper() -> subprocess.Popen:
    """A stand-in connector, isolated from this process's group.

    The isolation is not optional. ``_terminate`` signals the child's process
    group on POSIX, and a child left in *this* group would take the test runner
    down with it.
    """
    extra: dict = {}
    if os.name == "nt":
        extra["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        extra["start_new_session"] = True
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], **extra
    )


# --------------------------------------------------------------------------
# Hostname policy: the manifest has to agree with the configured value
# --------------------------------------------------------------------------
class TestManifestHostnamePolicy:
    def test_manifest_hostname_must_match_configured_hostname(self, manifest):
        manifest["ingress"][0]["hostname"] = OTHER_HOSTNAME

        message = refusal(manifest)

        assert "does not match" in message
        # Neither value is echoed: the message describes the rule that failed.
        assert OTHER_HOSTNAME not in message
        assert HOSTNAME not in message

    def test_matching_hostname_is_accepted(self, manifest):
        accepted = tunnel_mod.validate_manifest(manifest, expected_hostname=HOSTNAME)

        assert accepted.hostname == HOSTNAME

    def test_hostname_match_ignores_case(self, manifest):
        """DNS is case-insensitive, so the comparison is too."""
        manifest["ingress"][0]["hostname"] = HOSTNAME.upper()

        accepted = tunnel_mod.validate_manifest(manifest, expected_hostname=HOSTNAME)

        assert accepted.hostname == HOSTNAME

    @pytest.mark.parametrize("bad", ["", "   ", None, 7])
    def test_missing_hostname_fails_closed(self, manifest, bad):
        manifest["ingress"][0]["hostname"] = bad

        # Pinned to the ingress rule that must fail. A looser assertion here
        # passed for the wrong reason: the mismatch check also mentions
        # "hostname", so it could mask a syntax validator that had been removed.
        assert "ingress rule 1 hostname" in refusal(manifest)

    def test_missing_configured_hostname_fails_closed(self, monkeypatch):
        monkeypatch.delenv(TUNNEL_HOSTNAME_ENV_VAR, raising=False)

        with pytest.raises(TunnelConfigError) as excinfo:
            tunnel_mod.read_configured_hostname()

        assert TUNNEL_HOSTNAME_ENV_VAR in str(excinfo.value)

    def test_malformed_configured_hostname_fails_closed(self, monkeypatch):
        monkeypatch.setenv(TUNNEL_HOSTNAME_ENV_VAR, "not a hostname")

        with pytest.raises(TunnelConfigError):
            tunnel_mod.read_configured_hostname()


class TestHostnameSyntax:
    @pytest.mark.parametrize(
        "bad",
        [
            "localhost",                    # no dot: not a tunnel hostname
            "*.example.invalid",            # wildcard
            "api.example.invalid:443",      # port
            "https://api.example.invalid",  # scheme
            "api.example.invalid/admin",    # path
            "api example.invalid",          # whitespace
            "api.exa\tmple.invalid",        # control character
            "-api.example.invalid",         # leading hyphen
            "api-.example.invalid",         # trailing hyphen
            "api..example.invalid",         # empty label
            "api.example.invalid.",         # trailing dot
            "a" * 64 + ".example.invalid",  # label over 63 characters
            "a" * 260 + ".invalid",         # name over 253 characters
        ],
    )
    def test_malformed_hostname_is_rejected(self, manifest, bad):
        manifest["ingress"][0]["hostname"] = bad

        # Pinned to the syntax check, not merely "some refusal mentioning
        # hostname". Verified by mutation: with ``_valid_hostname`` forced to
        # return True the earlier, looser assertion still passed, because the
        # hostname-mismatch rule fired instead.
        assert "ingress rule 1 hostname" in refusal(manifest)

    @pytest.mark.parametrize(
        "good",
        [
            "api.example.invalid",
            "api.other.example.invalid",
            "tunnel-1.sub.example.invalid",
            "a1.example.invalid",
        ],
    )
    def test_synthetic_hostname_is_accepted(self, manifest, good):
        manifest["ingress"][0]["hostname"] = good

        accepted = tunnel_mod.validate_manifest(manifest, expected_hostname=good)

        assert accepted.hostname == good


# --------------------------------------------------------------------------
# Nothing real is committed
# --------------------------------------------------------------------------
_REAL_LOOKING_TLD = re.compile(
    r"\b[a-z0-9-]+\.(?:com|net|org|io|au|dev|app|cloud)\b", re.IGNORECASE
)

# Named in the module docstring as the anonymous quick-tunnel form the launcher
# refuses to use, so it is the one real-looking name allowed to appear.
_DOCUMENTED_NON_USE = {"trycloudflare.com"}


class TestNoRealIdentifiersAreCommitted:
    def test_tracked_tunnel_sources_carry_no_real_hostname(self):
        """Examples stay on a reserved name, so no real domain is committed."""
        for name in ("qsagent/api/tunnel.py", "tests/test_api_tunnel.py"):
            text = (BACKEND / name).read_text(encoding="utf-8")
            found = {
                match.group(0).lower() for match in _REAL_LOOKING_TLD.finditer(text)
            } - _DOCUMENTED_NON_USE
            assert found == set(), (name, sorted(found))

    def test_the_scanner_would_notice_a_real_hostname(self):
        """Non-vacuity: the absence asserted above is a real finding.

        The probe is assembled at run time so this file does not itself contain
        a contiguous real-looking name.
        """
        probe = "svc." + "not-a-real-domain" + ".com"

        assert _REAL_LOOKING_TLD.search(probe) is not None

    def test_the_module_states_the_reserved_example(self):
        """The scanner above is reading the file it claims to read."""
        text = (BACKEND / "qsagent/api/tunnel.py").read_text(encoding="utf-8")

        assert "api.example.invalid" in text
        assert "trycloudflare.com" in text


# --------------------------------------------------------------------------
# Ingress shape: exactly one hostname rule, then exactly one catch-all
# --------------------------------------------------------------------------
class TestIngressShape:
    def test_catch_all_before_the_api_route_is_rejected(self, manifest):
        """A catch-all first would swallow the hostname rule behind it."""
        manifest["ingress"].reverse()

        assert "rule 1" in refusal(manifest)

    def test_duplicate_hostname_ingress_is_rejected(self, manifest):
        manifest["ingress"].insert(1, dict(manifest["ingress"][0]))

        assert "exactly two" in refusal(manifest)

    def test_missing_catch_all_is_rejected(self, manifest):
        manifest["ingress"] = manifest["ingress"][:1]

        assert "exactly two" in refusal(manifest)

    def test_extra_ingress_rule_is_rejected(self, manifest):
        manifest["ingress"].insert(
            1, {"hostname": OTHER_HOSTNAME, "service": TUNNEL_ORIGIN_SERVICE}
        )

        assert "exactly two" in refusal(manifest)

    def test_catch_all_must_be_the_404_action(self, manifest):
        manifest["ingress"][1]["service"] = "http://127.0.0.1:9999"

        assert CATCH_ALL_SERVICE in refusal(manifest)

    def test_catch_all_may_not_carry_a_hostname(self, manifest):
        manifest["ingress"][1]["hostname"] = HOSTNAME

        assert "rule 2" in refusal(manifest)

    def test_unexpected_api_rule_key_is_rejected(self, manifest):
        manifest["ingress"][0]["path"] = "/admin"

        assert "exactly the keys" in refusal(manifest)

    @pytest.mark.parametrize("rules", ["not-a-list", {"a": 1}, [], [None, None]])
    def test_malformed_ingress_is_rejected(self, manifest, rules):
        manifest["ingress"] = rules

        assert "ingress" in refusal(manifest)


class TestOriginBinding:
    @pytest.mark.parametrize(
        "service",
        [
            "http://0.0.0.0:8000",           # every interface
            "http://192.168.1.5:8000",       # another host on the LAN
            "http://localhost:8000",         # resolves somewhere other than the bind
            "http://[::1]:8000",             # a different loopback address
            "https://127.0.0.1:8000",        # cloudflared terminates TLS
            "http://127.0.0.1:8000/",        # trailing path
            "http://127.0.0.1:8000/api/v1",  # a narrower path
        ],
    )
    def test_non_origin_service_is_rejected(self, manifest, service):
        manifest["ingress"][0]["service"] = service

        message = refusal(manifest)

        assert TUNNEL_ORIGIN_SERVICE in message
        # The refused value is never echoed back at the caller.
        assert service not in message

    @pytest.mark.parametrize("port", [8001, 8080, 3000, 443])
    def test_alternate_loopback_port_is_rejected(self, manifest, port):
        manifest["ingress"][0]["service"] = f"http://127.0.0.1:{port}"

        assert TUNNEL_ORIGIN_SERVICE in refusal(manifest)

    @pytest.mark.parametrize("service", ["", None, 8000, {"service": "x"}])
    def test_non_string_service_is_rejected(self, manifest, service):
        manifest["ingress"][0]["service"] = service

        assert TUNNEL_ORIGIN_SERVICE in refusal(manifest)


# --------------------------------------------------------------------------
# Tunnel identifier
# --------------------------------------------------------------------------
class TestTunnelIdentifier:
    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "has space",
            "..",
            "../etc/passwd",
            "a/b",
            "tunnel;rm -rf /",
            "--flag",
            "a" * 64,
            None,
            7,
        ],
    )
    def test_invalid_tunnel_identifier_is_rejected(self, manifest, bad):
        manifest["tunnel"] = bad

        assert "tunnel must be" in refusal(manifest)

    @pytest.mark.parametrize(
        "good",
        [TUNNEL_ID, TUNNEL_ID.replace("-", ""), "tunnel-one", "tunnel_one", "T1"],
    )
    def test_uuid_or_simple_name_is_accepted(self, manifest, good):
        manifest["tunnel"] = good

        accepted = tunnel_mod.validate_manifest(manifest, expected_hostname=HOSTNAME)

        assert accepted.tunnel == good


# --------------------------------------------------------------------------
# Credential reference: path shape only, contents never touched
# --------------------------------------------------------------------------
class TestCredentialReference:
    def test_relative_credentials_path_is_rejected(self, manifest, credentials_file):
        manifest["credentials-file"] = credentials_file.name

        assert "absolute" in refusal(manifest)

    def test_missing_credentials_file_is_rejected(self, manifest, tmp_path):
        manifest["credentials-file"] = str(tmp_path / "absent.json")

        assert "does not exist" in refusal(manifest)

    def test_credentials_directory_is_rejected(self, manifest, tmp_path):
        manifest["credentials-file"] = str(tmp_path)

        assert "regular file" in refusal(manifest)

    @pytest.mark.parametrize("bad", ["", None, 7, ["/etc/passwd"]])
    def test_non_string_credentials_reference_is_rejected(self, manifest, bad):
        manifest["credentials-file"] = bad

        assert "credentials-file" in refusal(manifest)

    def test_credential_contents_are_never_parsed(self, manifest, credentials_file):
        """Validation is a path check, so the file's contents cannot matter.

        The file is filled with something that is not JSON and is not a valid
        credential document. If anything ever opened it, this would fail.
        """
        decoy = "decoy-credential-contents-must-not-be-parsed"
        credentials_file.write_text(decoy, encoding="utf-8")

        accepted = tunnel_mod.validate_manifest(manifest, expected_hostname=HOSTNAME)

        assert accepted.credentials_file == credentials_file

    def test_refusal_does_not_name_the_credentials_file(self, manifest, credentials_file):
        credentials_file.write_text("{}", encoding="utf-8")
        manifest["ingress"][0]["service"] = "http://127.0.0.1:9999"

        message = refusal(manifest)

        assert credentials_file.name not in message


# --------------------------------------------------------------------------
# Manifest envelope
# --------------------------------------------------------------------------
class TestManifestEnvelope:
    @pytest.mark.parametrize("raw", [[], "text", 7, None, True])
    def test_manifest_must_be_an_object(self, raw):
        with pytest.raises(TunnelConfigError) as excinfo:
            tunnel_mod.validate_manifest(raw, expected_hostname=HOSTNAME)

        assert "JSON object" in str(excinfo.value)

    def test_unknown_manifest_key_is_rejected(self, manifest):
        manifest["debug"] = True

        message = refusal(manifest)

        assert "unexpected keys" in message
        # The keys we accept are listed; the offending key is not echoed, so a
        # manifest cannot smuggle a value out through an error message.
        for allowed in MANIFEST_KEYS:
            assert allowed in message
        assert "debug" not in message

    def test_malformed_json_is_rejected(self, tmp_path, env):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(TunnelConfigError) as excinfo:
            load_manifest(path)

        assert "not valid JSON" in str(excinfo.value)

    def test_unreadable_manifest_is_rejected(self, tmp_path, env):
        with pytest.raises(TunnelConfigError) as excinfo:
            load_manifest(tmp_path / "absent.json")

        assert "could not be read" in str(excinfo.value)

    def test_load_manifest_accepts_a_valid_file(self, tmp_path, env, manifest):
        path = write_manifest(tmp_path, manifest)

        accepted = load_manifest(path)

        assert accepted.hostname == HOSTNAME
        assert accepted.tunnel == TUNNEL_ID

    def test_manifest_keys_are_exactly_the_documented_schema(self):
        """The schema the errors advertise is the schema that is enforced."""
        assert MANIFEST_KEYS == {"tunnel", "credentials-file", "ingress"}
        assert API_INGRESS_KEYS == {"hostname", "service"}


# --------------------------------------------------------------------------
# The API-token sentinel
# --------------------------------------------------------------------------
class TestApiTokenSentinel:
    def test_missing_api_token_fails_closed(self, tmp_path, monkeypatch, manifest):
        monkeypatch.delenv(API_TOKEN_ENV_VAR, raising=False)
        monkeypatch.setenv(TUNNEL_HOSTNAME_ENV_VAR, HOSTNAME)
        path = write_manifest(tmp_path, manifest)

        with pytest.raises(TunnelConfigError) as excinfo:
            load_manifest(path)

        assert API_TOKEN_ENV_VAR in str(excinfo.value)

    def test_blank_api_token_fails_closed(self, monkeypatch):
        monkeypatch.setenv(API_TOKEN_ENV_VAR, "   ")

        with pytest.raises(TunnelConfigError):
            tunnel_mod.require_api_token_configured()

    def test_main_refuses_to_run_without_an_api_token(
        self, tmp_path, monkeypatch, manifest
    ):
        monkeypatch.delenv(API_TOKEN_ENV_VAR, raising=False)
        monkeypatch.setenv(TUNNEL_HOSTNAME_ENV_VAR, HOSTNAME)
        path = write_manifest(tmp_path, manifest)
        started: list[int] = []
        monkeypatch.setattr(
            tunnel_mod.shutil, "which", lambda name: "/usr/bin/cloudflared"
        )
        monkeypatch.setattr(tunnel_mod, "_spawn_cloudflared", lambda p: started.append(1))

        with pytest.raises(SystemExit) as excinfo:
            main([str(path)])

        assert API_TOKEN_ENV_VAR in str(excinfo.value)
        assert started == []

    def test_the_sentinel_only_checks_presence(self, env):
        """Presence is the whole test - the token is never compared.

        Anything stronger would duplicate the gateway's check and imply this
        process authorises requests. It does not: bearer auth at the gateway is
        the boundary, and a tunnel that is up but unauthenticated exposes
        nothing.
        """
        assert tunnel_mod.require_api_token_configured(env) is None
        assert (
            tunnel_mod.require_api_token_configured({API_TOKEN_ENV_VAR: "any-other"})
            is None
        )


# --------------------------------------------------------------------------
# Nothing sensitive is echoed or logged
# --------------------------------------------------------------------------
class TestNoEcho:
    def test_refusal_never_echoes_the_credentials_path(self, manifest, tmp_path):
        decoy = str(tmp_path / "decoy-credentials-path.json")

        message = refusal({**manifest, "credentials-file": decoy})

        assert "credentials-file" in message
        assert decoy not in message

    def test_refusal_never_echoes_the_hostname(self, manifest):
        manifest["ingress"][0]["hostname"] = OTHER_HOSTNAME

        message = refusal(manifest)

        assert OTHER_HOSTNAME not in message
        assert HOSTNAME not in message

    def test_refusal_never_echoes_the_manifest_path(self, tmp_path, env):
        path = tmp_path / "decoy-manifest-path.json"
        path.write_text("{broken", encoding="utf-8")

        with pytest.raises(TunnelConfigError) as excinfo:
            load_manifest(path)

        assert "decoy-manifest-path" not in str(excinfo.value)

    def test_no_output_carries_the_api_token(
        self, tmp_path, monkeypatch, capsys, caplog, manifest
    ):
        decoy = "decoy-api-token-4f2a"
        monkeypatch.setenv(API_TOKEN_ENV_VAR, decoy)
        monkeypatch.setenv(TUNNEL_HOSTNAME_ENV_VAR, HOSTNAME)
        path = write_manifest(tmp_path, manifest)

        with caplog.at_level(logging.DEBUG):
            assert main(["--check", str(path)]) == 0

        captured = capsys.readouterr()
        logged = "\n".join(record.getMessage() for record in caplog.records)
        for channel in (captured.out, captured.err, logged):
            assert decoy not in channel

    def test_no_output_carries_the_configured_hostname(
        self, tmp_path, monkeypatch, capsys, caplog, manifest
    ):
        """The one operator value this process knows does not reach a log."""
        monkeypatch.setenv(API_TOKEN_ENV_VAR, API_TOKEN)
        monkeypatch.setenv(TUNNEL_HOSTNAME_ENV_VAR, OTHER_HOSTNAME)
        manifest["ingress"][0]["hostname"] = OTHER_HOSTNAME
        path = write_manifest(tmp_path, manifest)

        with caplog.at_level(logging.DEBUG):
            assert main(["--check", str(path)]) == 0

        captured = capsys.readouterr()
        logged = "\n".join(record.getMessage() for record in caplog.records)
        for channel in (captured.out, captured.err, logged):
            assert OTHER_HOSTNAME not in channel


# --------------------------------------------------------------------------
# Supervision: the config on disk, the process group, termination
# --------------------------------------------------------------------------
class TestSupervision:
    def test_origin_constant_matches_the_address_the_gateway_binds(self):
        assert run_local.BIND_HOST == "127.0.0.1"
        assert TUNNEL_ORIGIN_SERVICE == (
            f"http://{run_local.BIND_HOST}:{run_local.BIND_PORT}"
        )

    def test_temporary_config_holds_only_the_validated_document(self, manifest):
        accepted = tunnel_mod.validate_manifest(manifest, expected_hostname=HOSTNAME)
        temporary = tunnel_mod._TempConfigFile(accepted.to_cloudflared_config())
        try:
            on_disk = json.loads(temporary.path.read_text(encoding="utf-8"))
        finally:
            temporary.cleanup()

        assert set(on_disk) == {"tunnel", "credentials-file", "ingress"}
        assert on_disk["ingress"] == [
            {"hostname": HOSTNAME, "service": TUNNEL_ORIGIN_SERVICE},
            {"service": CATCH_ALL_SERVICE},
        ]

    def test_temporary_config_is_owner_only_on_posix(self, manifest):
        if os.name == "nt":
            pytest.skip("POSIX file modes are not meaningful on Windows")
        accepted = tunnel_mod.validate_manifest(manifest, expected_hostname=HOSTNAME)
        temporary = tunnel_mod._TempConfigFile(accepted.to_cloudflared_config())
        try:
            mode = temporary.path.stat().st_mode & 0o777
        finally:
            temporary.cleanup()

        assert mode == 0o600

    def test_cleanup_is_idempotent(self, manifest):
        accepted = tunnel_mod.validate_manifest(manifest, expected_hostname=HOSTNAME)
        temporary = tunnel_mod._TempConfigFile(accepted.to_cloudflared_config())
        assert temporary.path.exists()

        temporary.cleanup()
        temporary.cleanup()

        assert not temporary.path.exists()

    def test_spawn_puts_the_connector_in_its_own_process_group(
        self, monkeypatch, tmp_path
    ):
        """Without isolation the wrapper shares the group it signals."""
        recorded: dict = {}

        class Recorder:
            def __init__(self, argv, **kwargs):
                recorded["argv"] = argv
                recorded["kwargs"] = kwargs

        monkeypatch.setattr(
            tunnel_mod.shutil, "which", lambda name: "/usr/bin/cloudflared"
        )
        monkeypatch.setattr(tunnel_mod.subprocess, "Popen", Recorder)

        tunnel_mod._spawn_cloudflared(tmp_path / "config.json")

        assert recorded["argv"][:3] == [CLOUDFLARED_BINARY, "tunnel", "--config"]
        if os.name == "nt":
            assert (
                recorded["kwargs"]["creationflags"]
                == subprocess.CREATE_NEW_PROCESS_GROUP
            )
            assert "start_new_session" not in recorded["kwargs"]
        else:
            assert recorded["kwargs"]["start_new_session"] is True
            assert "creationflags" not in recorded["kwargs"]
        # Streams are inherited, never piped: an undrained pipe deadlocks.
        assert "stdout" not in recorded["kwargs"]
        assert "stderr" not in recorded["kwargs"]

    def test_spawn_refuses_when_the_binary_is_absent(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tunnel_mod.shutil, "which", lambda name: None)

        with pytest.raises(FileNotFoundError):
            tunnel_mod._spawn_cloudflared(tmp_path / "config.json")

    def test_terminate_stops_a_running_connector(self):
        process = spawn_sleeper()
        try:
            code = tunnel_mod._terminate(process)

            assert process.poll() is not None
            assert code is not None
        finally:
            if process.poll() is None:
                process.kill()

    def test_terminate_is_idempotent(self):
        process = spawn_sleeper()
        try:
            first = tunnel_mod._terminate(process)
            second = tunnel_mod._terminate(process)

            assert process.poll() is not None
            assert first == second
        finally:
            if process.poll() is None:
                process.kill()

    def test_terminate_on_a_finished_connector_returns_its_code(self):
        isolated = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        process = subprocess.Popen(
            [sys.executable, "-c", "raise SystemExit(3)"], **isolated
        )
        process.wait()

        assert tunnel_mod._terminate(process) == 3

    def test_startup_failure_removes_the_temporary_config(
        self, tmp_path, monkeypatch, env, manifest
    ):
        path = write_manifest(tmp_path, manifest)
        created: list[Path] = []
        real_temp = tunnel_mod._TempConfigFile

        class Recording(real_temp):
            def __init__(self, payload):
                super().__init__(payload)
                created.append(self.path)

        def boom(_path):
            raise OSError("decoy-startup-failure-detail")

        monkeypatch.setattr(tunnel_mod, "_TempConfigFile", Recording)
        monkeypatch.setattr(
            tunnel_mod.shutil, "which", lambda name: "/usr/bin/cloudflared"
        )
        monkeypatch.setattr(tunnel_mod, "_spawn_cloudflared", boom)

        with pytest.raises(SystemExit) as excinfo:
            main([str(path)])

        message = str(excinfo.value)
        assert "could not start" in message
        # The OSError's own text is not surfaced either.
        assert "decoy-startup-failure-detail" not in message
        assert created and not created[0].exists()

    def test_check_mode_never_looks_for_the_connector(
        self, tmp_path, monkeypatch, env, manifest
    ):
        """``--check`` is pure validation, so a bare machine can run it."""

        def exploded(name):
            raise AssertionError("--check must not look for the connector")

        monkeypatch.setattr(tunnel_mod.shutil, "which", exploded)
        path = write_manifest(tmp_path, manifest)

        assert main(["--check", str(path)]) == 0

    def test_run_fails_closed_when_cloudflared_is_absent(
        self, tmp_path, monkeypatch, env, manifest
    ):
        monkeypatch.setattr(tunnel_mod.shutil, "which", lambda name: None)
        path = write_manifest(tmp_path, manifest)

        with pytest.raises(SystemExit) as excinfo:
            main([str(path)])

        assert CLOUDFLARED_BINARY in str(excinfo.value)

    def test_a_rejected_manifest_never_starts_the_connector(
        self, tmp_path, monkeypatch, env, manifest
    ):
        manifest["ingress"][0]["service"] = "http://0.0.0.0:8000"
        path = write_manifest(tmp_path, manifest)
        started: list[int] = []
        monkeypatch.setattr(
            tunnel_mod.shutil, "which", lambda name: "/usr/bin/cloudflared"
        )
        monkeypatch.setattr(tunnel_mod, "_spawn_cloudflared", lambda p: started.append(1))

        with pytest.raises(SystemExit):
            main([str(path)])

        assert started == []

    def test_usage_error_when_no_manifest_is_given(self):
        with pytest.raises(SystemExit) as excinfo:
            main([])

        assert "usage" in str(excinfo.value)


# --------------------------------------------------------------------------
# The connector itself
# --------------------------------------------------------------------------
class TestConnectorIntegration:
    @pytest.mark.skipif(
        shutil.which(CLOUDFLARED_BINARY) is None,
        reason=f"{CLOUDFLARED_BINARY} is not installed here; the connector is untested",
    )
    def test_cloudflared_reports_a_version(self):
        """Runs only where the connector exists, and never starts a tunnel.

        A skip here is a skip, not a pass: nothing in this suite claims the
        socket-level public path works, because establishing one would mean
        publishing a real hostname and contacting Cloudflare.
        """
        completed = subprocess.run(
            [CLOUDFLARED_BINARY, "--version"],
            capture_output=True,
            timeout=30,
        )

        assert completed.returncode == 0


# --------------------------------------------------------------------------
# Opt-in is structural, not a matter of remembering
# --------------------------------------------------------------------------
class TestOptInIsStructural:
    def test_nothing_in_the_serving_path_imports_the_launcher(self):
        """The tunnel cannot start itself: no serving module can reach it."""
        for name in ("api/main.py", "api/run_local.py", "api/__init__.py"):
            text = (BACKEND / "qsagent" / name).read_text(encoding="utf-8")
            for forbidden in ("import tunnel", "tunnel import", "api.tunnel"):
                assert forbidden not in text, (name, forbidden)

    def test_the_guard_would_notice_an_import(self):
        """Non-vacuity: a real import really would be spotted."""
        probe = "from ." + "tunnel import main"

        assert "tunnel import" in probe

    def test_the_launcher_is_a_separate_entry_point(self):
        """It is a module you run, not a function the gateway calls."""
        source = (BACKEND / "qsagent/api/tunnel.py").read_text(encoding="utf-8")

        assert 'if __name__ == "__main__":' in source
        # No quick tunnel, so no anonymous public URL can be produced.
        assert "--url" not in source.replace("--url``", "")


class TestDeferredToProductionReadiness:
    """Guardrails against scope creep while the tunnel is still opt-in."""

    def test_the_launcher_never_binds_anything_itself(self):
        """The gateway binds; this module only points at it."""
        source = (BACKEND / "qsagent/api/tunnel.py").read_text(encoding="utf-8")

        for forbidden in ("BIND_HOST =", "BIND_PORT =", "uvicorn.run", "workers="):
            assert forbidden not in source, forbidden

    def test_no_supervisor_or_daemon_integration_is_present(self):
        """No systemd unit, no service registration, no auto-restart."""
        source = (BACKEND / "qsagent/api/tunnel.py").read_text(encoding="utf-8")

        for forbidden in ("systemd", "launchd", "sc.exe", "schtasks"):
            assert forbidden not in source, forbidden
