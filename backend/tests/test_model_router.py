"""Tests for the Phase 3B model router.

All providers are fake in-process objects. No network calls. No SDK imports.
No credentials in source.

Fake providers track call counts so every test can assert whether a provider
was actually invoked (non-hollow: not just 0==0).
"""

from __future__ import annotations

import traceback

import pytest

from qsagent.runtime.router import (
    MalformedResponseError,
    MissingCredentialError,
    ModelRouter,
    ModelTier,
    PolicyViolationError,
    ProviderFailureError,
    ProviderResponse,
    UnknownTaskError,
)


# ---------------------------------------------------------------------------
# Fake infrastructure
# ---------------------------------------------------------------------------

class FakeSecretProvider:
    """Returns keys from an explicit mapping. Missing key → None."""

    def __init__(self, keys: dict[str, str | None]) -> None:
        self._keys = keys
        self.call_count: dict[str, int] = {}

    def get_key(self, provider: str) -> str | None:
        self.call_count[provider] = self.call_count.get(provider, 0) + 1
        return self._keys.get(provider)


class FakeLocalProvider:
    """LOCAL_MODEL provider. Records whether api_key was non-None."""

    name = "fake_local"
    supported_tier = ModelTier.LOCAL_MODEL

    def __init__(self) -> None:
        self.call_count = 0
        self.received_keys: list[str | None] = []

    def complete(self, prompt: str, *, api_key: str | None) -> ProviderResponse:
        self.call_count += 1
        self.received_keys.append(api_key)
        return ProviderResponse(
            text="local result",
            provider_name="fake_local",
            model_name="local-model-v1",
            usage_tokens=10,
        )


class FakeCloudProvider:
    """BYOK_MODEL provider. Records whether it was called and what key it got."""

    name = "fake_cloud"
    supported_tier = ModelTier.BYOK_MODEL

    def __init__(self) -> None:
        self.call_count = 0
        self.received_keys: list[str | None] = []

    def complete(self, prompt: str, *, api_key: str | None) -> ProviderResponse:
        self.call_count += 1
        self.received_keys.append(api_key)
        return ProviderResponse(
            text="cloud synthesis result",
            provider_name="fake_cloud",
            model_name="cloud-model-v2",
            usage_tokens=42,
        )


class FakeBrokenProvider:
    """BYOK_MODEL provider that embeds a fake key in its exception message."""

    name = "fake_broken"
    supported_tier = ModelTier.BYOK_MODEL
    FAKE_SECRET = "sk-SECRETKEY-that-must-not-leak"

    def __init__(self) -> None:
        self.call_count = 0

    def complete(self, prompt: str, *, api_key: str | None) -> ProviderResponse:
        self.call_count += 1
        raise RuntimeError(f"API error: key={self.FAKE_SECRET} was rejected")


class FakeMalformedObjectProvider:
    """BYOK_MODEL provider that returns a plain object (not ProviderResponse)."""

    name = "fake_malformed_obj"
    supported_tier = ModelTier.BYOK_MODEL

    def __init__(self) -> None:
        self.call_count = 0

    def complete(self, prompt: str, *, api_key: str | None) -> object:
        self.call_count += 1
        return object()  # deliberately wrong type


def _make_byok_response(**overrides) -> ProviderResponse:
    """Build a ProviderResponse with valid defaults, then apply overrides."""
    base = ProviderResponse(
        text="ok",
        provider_name="fake_cloud",
        model_name="cloud-model-v2",
        usage_tokens=1,
    )
    for k, v in overrides.items():
        object.__setattr__(base, k, v)
    return base


class FakeFieldMalformedProvider:
    """BYOK_MODEL provider that returns a ProviderResponse with bad field values."""

    name = "fake_field_malformed"
    supported_tier = ModelTier.BYOK_MODEL

    def __init__(self, response: ProviderResponse) -> None:
        self._response = response
        self.call_count = 0

    def complete(self, prompt: str, *, api_key: str | None) -> ProviderResponse:
        self.call_count += 1
        return self._response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_router_with_both(
    cloud_key: str | None = "valid-key",
    local_provider: FakeLocalProvider | None = None,
    cloud_provider: FakeCloudProvider | None = None,
) -> tuple:
    local = local_provider or FakeLocalProvider()
    cloud = cloud_provider or FakeCloudProvider()
    secrets = FakeSecretProvider({"fake_cloud": cloud_key})
    router = ModelRouter(secrets)
    router.register(local)
    router.register(cloud)
    return router, local, cloud, secrets


# ---------------------------------------------------------------------------
# 1. Deterministic routing — local task
# ---------------------------------------------------------------------------

def test_local_task_routes_to_local_provider():
    router, local, cloud, secrets = _make_router_with_both()

    resp = router.complete("metadata", "some prompt")

    assert resp.provider_name == "fake_local"
    assert local.call_count == 1
    assert cloud.call_count == 0
    # get_key must NOT have been called for a local task
    assert secrets.call_count.get("fake_local", 0) == 0


def test_summary_routes_to_local_provider():
    router, local, cloud, _ = _make_router_with_both()

    resp = router.complete("summary", "summarise this")

    assert resp.provider_name == "fake_local"
    assert local.call_count == 1
    assert cloud.call_count == 0


# ---------------------------------------------------------------------------
# 2. BYOK routing
# ---------------------------------------------------------------------------

def test_byok_task_routes_to_cloud_provider():
    router, local, cloud, _ = _make_router_with_both(cloud_key="valid-key-abc")

    resp = router.complete("document_synthesis", "synthesise")

    assert resp.provider_name == "fake_cloud"
    assert cloud.call_count == 1
    assert local.call_count == 0
    assert cloud.received_keys[0] == "valid-key-abc"


# ---------------------------------------------------------------------------
# 3. Local provider receives no key
# ---------------------------------------------------------------------------

def test_local_provider_receives_no_key():
    router, local, _, _ = _make_router_with_both()
    router.complete("metadata", "prompt")
    assert local.received_keys == [None]


# ---------------------------------------------------------------------------
# 4. Unknown task
# ---------------------------------------------------------------------------

def test_unknown_task_raises():
    router, _, _, _ = _make_router_with_both()

    with pytest.raises(UnknownTaskError) as exc_info:
        router.complete("safety_check", "prompt")

    assert "safety_check" in str(exc_info.value)


def test_unknown_task_never_calls_provider():
    router, local, cloud, _ = _make_router_with_both()

    with pytest.raises(UnknownTaskError):
        router.complete("nonexistent_task", "prompt")

    assert local.call_count == 0
    assert cloud.call_count == 0


# ---------------------------------------------------------------------------
# 5. No provider registered → PolicyViolationError
# ---------------------------------------------------------------------------

def test_no_byok_provider_registered_raises_policy_violation():
    local = FakeLocalProvider()
    secrets = FakeSecretProvider({"fake_cloud": "key"})
    router = ModelRouter(secrets)
    router.register(local)  # only local registered

    with pytest.raises(PolicyViolationError) as exc_info:
        router.complete("document_synthesis", "synthesise")

    err = exc_info.value
    assert err.task_id == "document_synthesis"
    assert "BYOK_MODEL" in err.tier
    assert local.call_count == 0


# ---------------------------------------------------------------------------
# 6. Missing/blank credential
# ---------------------------------------------------------------------------

def test_missing_key_raises_before_provider_call():
    cloud = FakeCloudProvider()
    secrets = FakeSecretProvider({"fake_cloud": None})
    router = ModelRouter(secrets)
    router.register(cloud)

    with pytest.raises(MissingCredentialError) as exc_info:
        router.complete("document_synthesis", "prompt")

    err = exc_info.value
    assert err.provider == "fake_cloud"
    assert err.task_id == "document_synthesis"
    assert cloud.call_count == 0  # provider NOT called


def test_blank_key_raises_before_provider_call():
    cloud = FakeCloudProvider()
    secrets = FakeSecretProvider({"fake_cloud": ""})
    router = ModelRouter(secrets)
    router.register(cloud)

    with pytest.raises(MissingCredentialError):
        router.complete("document_synthesis", "prompt")

    assert cloud.call_count == 0


# ---------------------------------------------------------------------------
# 7. Provider failure — explicit, auditable, no secret leak
# ---------------------------------------------------------------------------

def test_provider_failure_raises_provider_failure_error():
    broken = FakeBrokenProvider()
    secrets = FakeSecretProvider({"fake_broken": "valid-key"})
    router = ModelRouter(secrets)
    router.register(broken)

    with pytest.raises(ProviderFailureError) as exc_info:
        router.complete("document_synthesis", "prompt")

    assert broken.call_count == 1
    err = exc_info.value
    assert err.provider == "fake_broken"
    assert err.task_id == "document_synthesis"
    assert err.reason == "RuntimeError"


def test_provider_failure_secret_not_in_exc_str():
    broken = FakeBrokenProvider()
    secrets = FakeSecretProvider({"fake_broken": "valid-key"})
    router = ModelRouter(secrets)
    router.register(broken)

    with pytest.raises(ProviderFailureError) as exc_info:
        router.complete("document_synthesis", "prompt")

    err = exc_info.value
    secret_fragment = FakeBrokenProvider.FAKE_SECRET

    assert secret_fragment not in str(err)
    assert secret_fragment not in repr(err)

    tb_str = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    assert secret_fragment not in tb_str

    # Identifiers ARE present (auditable)
    assert "fake_broken" in str(err)
    assert "document_synthesis" in str(err)


# ---------------------------------------------------------------------------
# 8. No silent fallback: BYOK failure never falls back to local
# ---------------------------------------------------------------------------

def test_no_silent_fallback_on_provider_failure():
    broken = FakeBrokenProvider()
    local = FakeLocalProvider()
    secrets = FakeSecretProvider({"fake_broken": "valid-key"})
    router = ModelRouter(secrets)
    router.register(local)
    router.register(broken)  # occupies BYOK_MODEL slot

    with pytest.raises(ProviderFailureError):
        router.complete("document_synthesis", "prompt")

    assert broken.call_count == 1
    assert local.call_count == 0  # local never called as fallback


# ---------------------------------------------------------------------------
# 9. Malformed response — wrong type
# ---------------------------------------------------------------------------

def test_malformed_response_wrong_type():
    provider = FakeMalformedObjectProvider()
    secrets = FakeSecretProvider({"fake_malformed_obj": "key"})
    router = ModelRouter(secrets)
    router.register(provider)

    with pytest.raises(MalformedResponseError) as exc_info:
        router.complete("document_synthesis", "prompt")

    err = exc_info.value
    assert err.field == "response"
    assert "ProviderResponse" in err.detail
    assert provider.call_count == 1


# ---------------------------------------------------------------------------
# 10. Malformed response — field failures
# ---------------------------------------------------------------------------

def _field_router(response: ProviderResponse) -> ModelRouter:
    provider = FakeFieldMalformedProvider(response)
    secrets = FakeSecretProvider({"fake_field_malformed": "key"})
    router = ModelRouter(secrets)
    router.register(provider)
    return router


def test_malformed_text_none():
    with pytest.raises(MalformedResponseError) as exc_info:
        _field_router(_make_byok_response(text=None)).complete("document_synthesis", "p")
    assert exc_info.value.field == "text"


def test_malformed_text_blank():
    with pytest.raises(MalformedResponseError) as exc_info:
        _field_router(_make_byok_response(text="   ")).complete("document_synthesis", "p")
    assert exc_info.value.field == "text"


def test_malformed_provider_name_blank():
    with pytest.raises(MalformedResponseError) as exc_info:
        _field_router(_make_byok_response(provider_name="")).complete("document_synthesis", "p")
    assert exc_info.value.field == "provider_name"


def test_malformed_model_name_blank():
    with pytest.raises(MalformedResponseError) as exc_info:
        _field_router(_make_byok_response(model_name="")).complete("document_synthesis", "p")
    assert exc_info.value.field == "model_name"


def test_malformed_usage_tokens_float():
    with pytest.raises(MalformedResponseError) as exc_info:
        _field_router(_make_byok_response(usage_tokens=1.5)).complete("document_synthesis", "p")
    assert exc_info.value.field == "usage_tokens"


def test_malformed_usage_tokens_negative():
    with pytest.raises(MalformedResponseError) as exc_info:
        _field_router(_make_byok_response(usage_tokens=-1)).complete("document_synthesis", "p")
    assert exc_info.value.field == "usage_tokens"


# ---------------------------------------------------------------------------
# 11. Secret not in router repr
# ---------------------------------------------------------------------------

def test_secret_not_in_router_repr():
    secrets = FakeSecretProvider({"fake_cloud": "sk-TOPSECRET"})
    router = ModelRouter(secrets)
    r = repr(router)
    assert "sk-TOPSECRET" not in r
    assert "FakeSecretProvider" not in r
    assert "ModelRouter" in r


# ---------------------------------------------------------------------------
# 12. Audit metadata — response carries provider/model/tokens; no key material
# ---------------------------------------------------------------------------

def test_audit_metadata_fields_complete():
    router, _, _, _ = _make_router_with_both(cloud_key="audit-key-xyz")

    resp = router.complete("document_synthesis", "some prompt")

    assert resp.provider_name == "fake_cloud"
    assert resp.model_name == "cloud-model-v2"
    assert isinstance(resp.usage_tokens, int)
    assert resp.usage_tokens >= 0
    # Key must NOT appear in any response field
    assert "audit-key-xyz" not in resp.text
    assert "audit-key-xyz" not in resp.provider_name
    assert "audit-key-xyz" not in resp.model_name
