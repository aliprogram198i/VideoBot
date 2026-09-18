"""Canonical resolver result contract for AliBot.

Resolvers may keep their legacy return values for compatibility, but every
resolver outcome can be normalized through this contract for orchestration,
telemetry, and diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


VALID_STATUSES = frozenset({"success", "empty", "failed", "skipped"})


@dataclass(frozen=True)
class ResolverResult:
    resolver: str
    status: str
    candidates: tuple[Any, ...] = ()
    elapsed_ms: float = 0.0
    failure_reason: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.resolver).strip():
            raise ValueError("resolver must be non-empty")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"unsupported resolver status: {self.status}")
        if self.elapsed_ms < 0:
            raise ValueError("elapsed_ms must be non-negative")

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def ok(self) -> bool:
        return self.status == "success" and bool(self.candidates)

    @classmethod
    def from_output(
        cls,
        resolver: str,
        output: Any,
        *,
        elapsed_ms: float = 0.0,
        failure_reason: str | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> "ResolverResult":
        if output is None or output is False:
            status = "failed" if failure_reason else "empty"
            candidates: tuple[Any, ...] = ()
        elif isinstance(output, (list, tuple)):
            candidates = tuple(output)
            status = "success" if candidates else ("failed" if failure_reason else "empty")
        else:
            candidates = (output,)
            status = "success"

        return cls(
            resolver=str(resolver),
            status=status,
            candidates=candidates,
            elapsed_ms=max(0.0, float(elapsed_ms)),
            failure_reason=failure_reason,
            diagnostics=dict(diagnostics or {}),
        )

    @classmethod
    def skipped(
        cls,
        resolver: str,
        reason: str,
        *,
        diagnostics: dict[str, Any] | None = None,
    ) -> "ResolverResult":
        return cls(
            resolver=resolver,
            status="skipped",
            failure_reason=reason,
            diagnostics=dict(diagnostics or {}),
        )


def normalize_candidates(value: Any) -> tuple[Any, ...]:
    """Normalize common legacy resolver outputs without changing semantics."""
    if value is None or value is False:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def result_from_exception(
    resolver: str,
    exc: BaseException,
    *,
    elapsed_ms: float = 0.0,
    diagnostics: dict[str, Any] | None = None,
) -> ResolverResult:
    return ResolverResult(
        resolver=resolver,
        status="failed",
        failure_reason=type(exc).__name__,
        elapsed_ms=max(0.0, float(elapsed_ms)),
        diagnostics=dict(diagnostics or {}),
    )
