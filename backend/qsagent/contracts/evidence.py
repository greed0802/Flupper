"""Evidence-first data contracts.

Core rule of the platform: *no quantity claim may exist without attached
evidence* - a file hash, a drawing sheet, and a source locator (raw text or
vector bounding box). These Pydantic models enforce that rule at the type
level so it cannot be bypassed by prose from a language model.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Discipline(str, Enum):
    ARCHITECTURAL = "ARCHITECTURAL"
    CIVIL = "CIVIL"
    STRUCTURAL = "STRUCTURAL"
    HYDRAULIC = "HYDRAULIC"
    ELECTRICAL = "ELECTRICAL"
    MECHANICAL = "MECHANICAL"
    LANDSCAPE = "LANDSCAPE"
    SPECIFICATION = "SPECIFICATION"
    UNKNOWN = "UNKNOWN"


class Unit(str, Enum):
    """Units permitted on a quantity claim, with dimensional exponents."""

    M = "m"
    M2 = "m2"
    M3 = "m3"
    MM = "mm"
    KG = "kg"
    TONNE = "t"
    EACH = "ea"
    ITEM = "item"
    HOUR = "hr"
    LITRE = "L"

    @property
    def length_exponent(self) -> Optional[int]:
        return {"m": 1, "mm": 1, "m2": 2, "m3": 3}.get(self.value)

    @property
    def is_metric_length(self) -> bool:
        return self.value in {"m", "mm", "m2", "m3"}


class AssumptionStatus(str, Enum):
    ASSUMED = "ASSUMED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class ApprovalLevel(str, Enum):
    """Human approval boundary for an action."""

    SAFE = "SAFE"        # auto-execute
    REVIEW = "REVIEW"    # show a diff, require acknowledgement
    CONFIRM = "CONFIRM"  # destructive: explicit typed confirmation


class EvidenceRef(BaseModel):
    """Pointer to the exact spot in a source document a number came from."""

    model_config = ConfigDict(frozen=True)

    file_hash: str = Field(..., description="SHA-256 of the source file bytes.")
    file_name: str
    drawing_no: Optional[str] = Field(
        None, description="e.g. C-204. Required for drawing-derived evidence."
    )
    revision: Optional[str] = Field(None, description="e.g. 'Rev 3' / 'B'.")
    sheet: Optional[str] = Field(None, description="Sheet or page identifier.")
    page: Optional[int] = Field(None, ge=1)
    zone: Optional[str] = Field(None, description="Grid zone e.g. 'C4'.")
    bbox: Optional[tuple[float, float, float, float]] = Field(
        None, description="(x0, y0, x1, y1) in PDF points."
    )
    raw_text: Optional[str] = Field(
        None, description="Verbatim text/dimension read from the source."
    )

    @field_validator("file_hash")
    @classmethod
    def _validate_hash(cls, v: str) -> str:
        v = v.strip().lower()
        if not SHA256_RE.match(v):
            raise ValueError("file_hash must be a lowercase 64-char SHA-256 hex digest")
        return v

    @model_validator(mode="after")
    def _require_locator(self) -> "EvidenceRef":
        if self.raw_text is None and self.bbox is None:
            raise ValueError(
                "evidence requires a source locator: raw_text or bbox must be provided"
            )
        if self.sheet is None and self.page is None:
            raise ValueError("evidence requires a sheet or page reference")
        return self

    def citation(self) -> str:
        parts = [self.drawing_no or self.file_name]
        if self.revision:
            parts.append(f"Rev {self.revision.lstrip('Rr').strip('ev ').strip() or self.revision}")
        if self.sheet:
            parts.append(f"Sheet {self.sheet}")
        elif self.page:
            parts.append(f"p.{self.page}")
        if self.zone:
            parts.append(f"Zone {self.zone}")
        return " / ".join(parts)


class EvidenceNode(BaseModel):
    """A persisted node in the project knowledge graph."""

    id: Optional[int] = None
    project_id: int
    node_type: str = Field(..., description="document | drawing | element | quantity | rate | boq_line")
    label: str
    discipline: Discipline = Discipline.UNKNOWN
    ref: Optional[EvidenceRef] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class Quantity(BaseModel):
    """A dimensioned number."""

    model_config = ConfigDict(frozen=True)

    value: float
    unit: Unit

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.value:,.3f} {self.unit.value}"


class Assumption(BaseModel):
    """An explicitly tracked QS assumption with lifecycle and cost impact."""

    id: str = Field(..., pattern=r"^A-\d{2,}$", description="e.g. A-01")
    project_id: int
    statement: str = Field(..., min_length=3)
    status: AssumptionStatus = AssumptionStatus.ASSUMED
    rationale: Optional[str] = None
    impact_delta_aud: Optional[float] = Field(
        None, description="Cost delta if the assumption proves wrong."
    )
    impact_quantity: Optional[Quantity] = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    raised_at: datetime = Field(default_factory=_utcnow)
    resolved_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _resolution_consistency(self) -> "Assumption":
        terminal = {AssumptionStatus.CONFIRMED, AssumptionStatus.REJECTED,
                    AssumptionStatus.SUPERSEDED}
        if self.status in terminal and self.resolved_at is None:
            object.__setattr__(self, "resolved_at", _utcnow())
        if self.status is AssumptionStatus.ASSUMED and self.resolved_at is not None:
            raise ValueError("an ASSUMED assumption cannot have resolved_at set")
        return self


class QuantityClaim(BaseModel):
    """The only legal way to assert a quantity in this platform."""

    claim_id: Optional[str] = None
    project_id: int
    description: str = Field(..., min_length=3)
    quantity: Quantity
    method: str = Field(..., description="Tool id or calculation method used.")
    evidence: list[EvidenceRef] = Field(..., min_length=1)
    assumption_ids: list[str] = Field(default_factory=list)
    workings: list[str] = Field(
        default_factory=list, description="Human-readable calculation steps."
    )
    created_at: datetime = Field(default_factory=_utcnow)

    @field_validator("evidence")
    @classmethod
    def _non_empty(cls, v: list[EvidenceRef]) -> list[EvidenceRef]:
        if not v:
            raise ValueError("a quantity claim requires at least one evidence reference")
        return v

    def citations(self) -> list[str]:
        return [e.citation() for e in self.evidence]


class ToolRun(BaseModel):
    """Full execution payload of a Tier 1/2/3 tool, stored for deterministic replay."""

    id: Optional[int] = None
    project_id: int
    tool_id: str
    tier: int = Field(..., ge=1, le=3)
    inputs: dict[str, Any]
    outputs: dict[str, Any] = Field(default_factory=dict)
    workings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=_utcnow)
    duration_ms: Optional[float] = None
    ok: bool = True
    error: Optional[str] = None
