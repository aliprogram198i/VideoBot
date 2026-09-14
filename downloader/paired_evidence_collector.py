"""Bounded, opt-in paired resolver evidence collection.

This collector is intentionally separate from the production resolver chain. It
runs only when explicitly enabled by environment policy, probes already-known
resolver callables with a strict per-probe timeout, and persists one paired
sample only when at least two resolver outcomes are available. The caller owns
sampling and supplies the resolver callables; this module never changes the
production result or resolver order.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import random
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .resolver_evidence import ResolverEvidence, ResolverEvidenceStore

_DEFAULT_SAMPLE_RATE = 0.01
_MAX_SAMPLE_RATE = 0.05
_MAX_PROBES = 4
_DEFAULT_TIMEOUT_SECONDS = 20.0
_MAX_TIMEOUT_SECONDS = 45.0


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def enabled() -> bool:
    """Return True only for an explicit staging-only opt-in."""
    return (
        _truthy(os.getenv("ALIBOT_PAIRED_EVIDENCE_ENABLED"))
        and os.getenv("ALIBOT_RUNTIME_ENV", "").strip().lower() == "staging"
    )


def sample_rate() -> float:
    try:
        value = float(os.getenv("ALIBOT_PAIRED_EVIDENCE_RATE", str(_DEFAULT_SAMPLE_RATE)))
    except (TypeError, ValueError):
        return _DEFAULT_SAMPLE_RATE
    return min(max(0.0, value), _MAX_SAMPLE_RATE)


def timeout_seconds() -> float:
    try:
        value = float(os.getenv("ALIBOT_PAIRED_EVIDENCE_TIMEOUT", str(_DEFAULT_TIMEOUT_SECONDS)))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_SECONDS
    return min(max(1.0, value), _MAX_TIMEOUT_SECONDS)


async def _invoke(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    result = operation(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def collect(
    store: ResolverEvidenceStore,
    *,
    sample_id: str | None,
    platform: str,
    media_kind: str,
    source_url: str,
    resolvers: Mapping[str, Callable[..., Any]],
    validator_kwargs: Mapping[str, Any] | None = None,
    timeout: float | None = None,
    rng: random.Random | None = None,
) -> bool:
    """Run a sampled paired probe and persist its bounded outcomes.

    The probe is observational only. The caller must not use its return value to
    serve the user request. Failures, cancellations, malformed resolver output,
    and persistence errors are all fail-open.
    """
    if not enabled():
        return False
    names = tuple(str(name) for name in resolvers)[:_MAX_PROBES]
    if len(names) < 2 or not source_url:
        return False
    chooser = rng or random.SystemRandom()
    if chooser.random() >= sample_rate():
        return False
    limit = min(max(1.0, float(timeout or timeout_seconds())), _MAX_TIMEOUT_SECONDS)
    sample = (sample_id or uuid.uuid4().hex)[:80]
    kwargs = dict(validator_kwargs or {})

    async def run_one(name: str) -> ResolverEvidence:
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(_invoke(resolvers[name], source_url, **kwargs), timeout=limit)
            count = len(result) if isinstance(result, (list, tuple)) else int(bool(result))
            return ResolverEvidence(name, bool(result), (time.monotonic() - started) * 1000, count)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return ResolverEvidence(
                name,
                False,
                (time.monotonic() - started) * 1000,
                0,
                type(exc).__name__,
            )

    try:
        outcomes = await asyncio.gather(*(run_one(name) for name in names))
        return store.record_sample(
            sample,
            platform=platform,
            media_kind=media_kind,
            outcomes=outcomes,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        return False


__all__ = ["collect", "enabled", "sample_rate", "timeout_seconds"]
