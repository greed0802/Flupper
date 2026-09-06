"""Tier 1 tool registry.

Tier 1 tools are *fixed Python math*: deterministic, unit-checked, and fully
described by their inputs so any run can be replayed byte-for-byte. No LLM
ever performs arithmetic in this platform - it only chooses which tool to call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from ..contracts.evidence import ToolRun


@dataclass
class ToolResult:
    outputs: dict[str, Any]
    workings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


ToolFn = Callable[..., ToolResult]


@dataclass
class Tool:
    id: str
    tier: int
    summary: str
    fn: ToolFn
    unit_map: dict[str, str] = field(default_factory=dict)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool_id: str, *, tier: int, summary: str,
                 unit_map: dict[str, str] | None = None) -> Callable[[ToolFn], ToolFn]:
        def deco(fn: ToolFn) -> ToolFn:
            self._tools[tool_id] = Tool(tool_id, tier, summary, fn, unit_map or {})
            return fn

        return deco

    def get(self, tool_id: str) -> Tool:
        if tool_id not in self._tools:
            raise KeyError(f"unknown tool: {tool_id}")
        return self._tools[tool_id]

    def list(self) -> list[dict[str, Any]]:
        return [
            {"id": t.id, "tier": t.tier, "summary": t.summary, "units": t.unit_map}
            for t in sorted(self._tools.values(), key=lambda t: t.id)
        ]

    def run(self, tool_id: str, project_id: int, **inputs: Any) -> ToolRun:
        tool = self.get(tool_id)
        started = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        try:
            result = tool.fn(**inputs)
        except Exception as exc:  # deterministic tools fail loudly, never silently
            return ToolRun(
                project_id=project_id, tool_id=tool_id, tier=tool.tier, inputs=inputs,
                started_at=started, duration_ms=(time.perf_counter() - t0) * 1000,
                ok=False, error=f"{type(exc).__name__}: {exc}",
            )
        return ToolRun(
            project_id=project_id, tool_id=tool_id, tier=tool.tier, inputs=inputs,
            outputs=result.outputs, workings=result.workings, warnings=result.warnings,
            started_at=started, duration_ms=(time.perf_counter() - t0) * 1000, ok=True,
        )


REGISTRY = ToolRegistry()
