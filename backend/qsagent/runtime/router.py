"""Model router — provider-neutral LLM dispatch for the QS Agent platform.

Design rules (non-negotiable):
- Policy is a closed enum of task IDs. Unknown task → UnknownTaskError.
- Provider selection is by tier only. No implicit fallback, ever.
- BYOK providers require a non-empty key. Missing/blank → MissingCredentialError.
- LOCAL_MODEL providers receive api_key=None. Secret provider is NOT queried.
- Provider exceptions are caught and re-raised as ProviderFailureError.
  The original message is discarded (it may contain the raw API key).
- ProviderResponse fields are validated before return.
- The SecretProvider reference is name-mangled so it cannot appear in repr().
- No journalling, no QSStore dependency. Callers decide what to persist.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tier enum
# ---------------------------------------------------------------------------

class ModelTier(str, Enum):
    """Routing tier for model completion requests.

    LOCAL_MODEL — runs on the host machine, no network, no API key.
    BYOK_MODEL  — cloud provider, user-supplied key required at call time.

    Note: 'Tier 1/2/3' elsewhere refers to QS tool tiers (deterministic math).
    This enum is unrelated to that concept.
    """
    LOCAL_MODEL = "LOCAL_MODEL"
    BYOK_MODEL  = "BYOK_MODEL"


# ---------------------------------------------------------------------------
# Task → tier policy  (exact key match, no substring/regex)
# ---------------------------------------------------------------------------

_POLICY: dict[str, ModelTier] = {
    "metadata":           ModelTier.LOCAL_MODEL,
    "summary":            ModelTier.LOCAL_MODEL,
    "document_synthesis": ModelTier.BYOK_MODEL,
}


# ---------------------------------------------------------------------------
# Response contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderResponse:
    """Validated response from a model provider.

    All fields are non-empty. usage_tokens is a non-negative int.
    Instances are only produced by ModelRouter.complete() after validation.
    """
    text: str           # non-empty, non-whitespace
    provider_name: str  # non-empty
    model_name: str     # non-empty
    usage_tokens: int   # int, >= 0


# ---------------------------------------------------------------------------
# Protocols (structural — test doubles implement without inheritance)
# ---------------------------------------------------------------------------

@runtime_checkable
class ModelProvider(Protocol):
    """A model backend that can fulfil completion requests."""

    @property
    def name(self) -> str:
        """Stable identifier, e.g. 'openai', 'ollama'. Never the key."""
        ...

    @property
    def supported_tier(self) -> ModelTier:
        """The tier this provider handles."""
        ...

    def complete(self, prompt: str, *, api_key: str | None) -> ProviderResponse:
        """Return a ProviderResponse or raise any exception on failure."""
        ...


@runtime_checkable
class SecretProvider(Protocol):
    """Source of API keys. Never stored visibly; never printed or logged."""

    def get_key(self, provider: str) -> str | None:
        """Return the key for *provider*, or None if not configured."""
        ...


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RouterError(RuntimeError):
    """Base class for all router errors."""


class UnknownTaskError(RouterError):
    """task_id was not found in the router policy table."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"unknown task_id={task_id!r}: not in router policy")


class PolicyViolationError(RouterError):
    """A registered provider cannot serve this task, or none is registered."""

    def __init__(self, task_id: str, tier: str, reason: str) -> None:
        self.task_id = task_id
        self.tier = tier
        self.reason = reason
        super().__init__(
            f"policy violation: task={task_id!r} tier={tier!r} reason={reason!r}"
        )


class MissingCredentialError(RouterError):
    """A BYOK provider was required but no key was available."""

    def __init__(self, provider: str, task_id: str) -> None:
        self.provider = provider
        self.task_id = task_id
        # Message contains only identifiers, never the key itself.
        super().__init__(
            f"provider={provider!r} task={task_id!r}: key absent or blank"
        )


class ProviderFailureError(RouterError):
    """The provider raised an exception during completion.

    The original exception message is intentionally discarded because it may
    contain the raw API key if the provider SDK embeds credentials in errors.
    Only the exception type name is retained.
    """

    def __init__(self, provider: str, task_id: str, reason: str) -> None:
        self.provider = provider
        self.task_id = task_id
        self.reason = reason
        super().__init__(
            f"provider={provider!r} task={task_id!r} failed: {reason}"
        )


class MalformedResponseError(RouterError):
    """The provider returned a structurally invalid response."""

    def __init__(self, provider: str, field: str, detail: str) -> None:
        self.provider = provider
        self.field = field
        self.detail = detail
        super().__init__(
            f"provider={provider!r} malformed response: field={field!r} {detail}"
        )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class ModelRouter:
    """Deterministic model router.

    Usage::

        router = ModelRouter(secret_provider)
        router.register(LocalOllamaProvider())
        router.register(OpenAIProvider())
        response = router.complete("metadata", "Summarise this drawing index.")

    The SecretProvider is held in a name-mangled attribute so it cannot appear
    in repr() or be accidentally serialised.
    """

    def __init__(self, secret_provider: SecretProvider) -> None:
        # Name-mangled: not accessible as router.secret_provider.
        self.__secret: SecretProvider = secret_provider  # noqa: SLF001
        self._providers: dict[ModelTier, ModelProvider] = {}

    def __repr__(self) -> str:
        tiers = [t.value for t in self._providers]
        return f"ModelRouter(tiers={tiers})"

    def register(self, provider: ModelProvider) -> None:
        """Register *provider* for its declared tier.

        A second registration for the same tier replaces the first.
        """
        self._providers[provider.supported_tier] = provider
        log.debug(
            "ModelRouter: registered provider=%r tier=%s",
            provider.name, provider.supported_tier.value,
        )

    def get_tier(self, task_id: str) -> ModelTier:
        """Return the policy tier for *task_id* from the canonical policy table.

        Raises:
            UnknownTaskError — task_id not in the policy table

        This is the single source of truth for the question "may this task run
        locally, or does it require a BYOK credential?". Callers must not
        re-derive routing from task-name substrings, and must not keep their own
        copy of the policy.
        """
        if task_id not in _POLICY:
            raise UnknownTaskError(task_id)
        return _POLICY[task_id]


    def complete(self, task_id: str, prompt: str) -> ProviderResponse:
        """Route *prompt* to the correct provider for *task_id*.

        Raises:
            UnknownTaskError       — task_id not in policy table
            PolicyViolationError   — no provider registered for the required tier
            MissingCredentialError — BYOK tier but key absent or blank
            ProviderFailureError   — provider raised any exception (message redacted)
            MalformedResponseError — response type wrong or fields invalid
        """
        # Step 1: policy lookup (exact match only)
        if task_id not in _POLICY:
            raise UnknownTaskError(task_id)
        tier = _POLICY[task_id]

        # Step 2: provider lookup
        provider = self._providers.get(tier)
        if provider is None:
            raise PolicyViolationError(
                task_id, tier.value, "no provider registered for tier"
            )

        # Step 3/4: key resolution
        if tier is ModelTier.LOCAL_MODEL:
            api_key: str | None = None
        else:
            # BYOK — requires a non-empty key; do NOT fall back
            raw_key = self.__secret.get_key(provider.name)
            if not raw_key:           # None or ""
                raise MissingCredentialError(provider.name, task_id)
            api_key = raw_key

        # Step 5: call provider; catch everything, redact message
        try:
            raw_response = provider.complete(prompt, api_key=api_key)
        except Exception as exc:
            # Discard exc args — they may contain the raw API key.
            reason = type(exc).__name__
            log.warning(
                "ModelRouter: provider=%r task=%r raised %s (message redacted)",
                provider.name, task_id, reason,
            )
            raise ProviderFailureError(provider.name, task_id, reason) from None

        # Step 6a: type guard before any field access
        if not isinstance(raw_response, ProviderResponse):
            raise MalformedResponseError(
                provider.name, "response", "not a ProviderResponse"
            )

        # Step 6b: field validation
        _validate_response(raw_response, provider.name)

        # Step 7: return validated response
        return raw_response


# ---------------------------------------------------------------------------
# Internal validation helper
# ---------------------------------------------------------------------------

def _validate_response(r: ProviderResponse, provider_name: str) -> None:
    """Validate all ProviderResponse fields; raise MalformedResponseError on first failure."""

    # text — non-None, non-blank
    if r.text is None or not r.text.strip():
        raise MalformedResponseError(provider_name, "text", "blank or None")

    # provider_name — non-empty string
    if not r.provider_name:
        raise MalformedResponseError(provider_name, "provider_name", "blank or None")

    # model_name — non-empty string
    if not r.model_name:
        raise MalformedResponseError(provider_name, "model_name", "blank or None")

    # usage_tokens — must be int (not bool, not float), must be >= 0
    if not isinstance(r.usage_tokens, int) or isinstance(r.usage_tokens, bool):
        raise MalformedResponseError(
            provider_name, "usage_tokens", "not a non-negative integer"
        )
    if r.usage_tokens < 0:
        raise MalformedResponseError(
            provider_name, "usage_tokens", "not a non-negative integer"
        )

