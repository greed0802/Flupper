"""Local HTTP gateway for the agent runtime (Phase 4, task A).

Transport surface only - see :mod:`qsagent.api.main` for the design notes.
"""

from .approvals import (
    ApprovalError,
    ApprovalRegistry,
    Binding,
    PendingApproval,
    canonical_request_hash,
    check_binding,
    prompt_digest,
)
from .errors import register_error_handlers
from .limits import DEFAULT_MAX_BODY_BYTES, BodySizeLimitMiddleware
from .main import (
    API_PREFIX,
    DEFAULT_ALLOWED_ORIGINS,
    DEFAULT_SANDBOX_ROOT,
    SessionRegistry,
    build_tier3_request,
    create_app,
    ensure_sandbox_root,
    model_action_id,
    tier3_action_id,
    tier3_request_hash,
)

__all__ = [
    "API_PREFIX",
    "DEFAULT_ALLOWED_ORIGINS",
    "DEFAULT_MAX_BODY_BYTES",
    "DEFAULT_SANDBOX_ROOT",
    "ApprovalError",
    "ApprovalRegistry",
    "Binding",
    "BodySizeLimitMiddleware",
    "PendingApproval",
    "SessionRegistry",
    "build_tier3_request",
    "canonical_request_hash",
    "check_binding",
    "create_app",
    "ensure_sandbox_root",
    "model_action_id",
    "prompt_digest",
    "register_error_handlers",
    "tier3_action_id",
    "tier3_request_hash",
]
