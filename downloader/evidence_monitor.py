"""Bounded read-only evidence telemetry for isolated staging runs."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from .resolver_evidence import ResolverEvidenceStore
from .resolver_selection_policy import choose_validated_first
from .resolver_statistical_validation import validate_resolver_set

_ELIGIBLE_ORDER = ("legacy_extractor", "smart_media", "browser_media", "cobalt")


async def run_periodic(db_path: str | Path, *, interval_seconds: int = 60) -> None:
    """Log aggregate paired-evidence counts and read-only statistical gates."""
    path = Path(db_path)
    last_signature = None
    while True:
        try:
            if path.exists():
                with sqlite3.connect(path, timeout=5) as conn:
                    total_rows = int(conn.execute("SELECT COUNT(*) FROM resolver_evidence").fetchone()[0])
                    distinct_samples = int(conn.execute("SELECT COUNT(DISTINCT sample_id) FROM resolver_evidence").fetchone()[0])
                    paired_samples = int(conn.execute("""
                        SELECT COUNT(*) FROM (
                            SELECT sample_id
                            FROM resolver_evidence
                            WHERE resolver IN ('legacy_extractor','smart_media','browser_media','cobalt')
                            GROUP BY sample_id
                            HAVING COUNT(DISTINCT resolver) >= 2
                        )
                    """).fetchone()[0])
                    complete_samples = int(conn.execute("""
                        SELECT COUNT(*) FROM (
                            SELECT sample_id
                            FROM resolver_evidence
                            WHERE resolver IN ('legacy_extractor','smart_media','browser_media','cobalt')
                            GROUP BY sample_id
                            HAVING COUNT(DISTINCT resolver) = 4
                        )
                    """).fetchone()[0])
                    context_counts = conn.execute("""
                        SELECT platform, media_kind, COUNT(DISTINCT sample_id) AS samples
                        FROM resolver_evidence
                        WHERE resolver IN ('legacy_extractor','smart_media','browser_media','cobalt')
                        GROUP BY platform, media_kind
                        ORDER BY platform, media_kind
                    """).fetchall()

                print(
                    f"📈 Paired Evidence Monitor: rows={total_rows} samples={distinct_samples} "
                    f"paired>=2={paired_samples} complete4={complete_samples}",
                    flush=True,
                )

                signature = (total_rows, distinct_samples, paired_samples, complete_samples, tuple(context_counts))
                if signature != last_signature:
                    store = ResolverEvidenceStore(path)
                    validation_summary = []
                    for platform, media_kind, sample_count in context_counts:
                        validations = validate_resolver_set(
                            store,
                            _ELIGIBLE_ORDER,
                            platform=platform,
                            media_kind=media_kind,
                        )
                        proposed = choose_validated_first(_ELIGIBLE_ORDER, validations)
                        validated = proposed != list(_ELIGIBLE_ORDER)
                        max_discordant = max(
                            (int(item["a_only"]) + int(item["b_only"]) for item in validations),
                            default=0,
                        )
                        qualifying_pairs = sum(
                            1
                            for item in validations
                            if int(item["paired_samples"]) >= 30
                            and (int(item["a_only"]) + int(item["b_only"])) >= 10
                        )
                        pair_details = ";".join(
                            f"{item['resolver_a']}>{item['resolver_b']}:n={item['paired_samples']},"
                            f"d={float(item['success_rate_delta']):.3f},"
                            f"disc={int(item['a_only']) + int(item['b_only'])},"
                            f"p={float(item['mcnemar_p_value']):.4f},"
                            f"lo={float(item['delta_lower_95']):.3f},"
                            f"adv={'1' if item['validated_advantage'] else '0'}"
                            for item in validations
                        )
                        validation_summary.append(
                            f"{platform}/{media_kind}:samples={sample_count} "
                            f"pairs={len(validations)} discordant_max={max_discordant} "
                            f"gate_ready_pairs={qualifying_pairs} "
                            f"validated={'yes' if validated else 'no'} "
                            f"details=[{pair_details}]"
                        )
                    if validation_summary:
                        print(
                            "📊 Paired Evidence Validation: " + " | ".join(validation_summary),
                            flush=True,
                        )
                    last_signature = signature
        except Exception as exc:
            # Monitoring must never affect the downloader runtime; surface only
            # the exception type so diagnostics cannot leak URLs or credentials.
            print(
                f"⚠️ Paired Evidence Monitor: validation diagnostics skipped ({type(exc).__name__})",
                flush=True,
            )
        await asyncio.sleep(max(30, int(interval_seconds)))


__all__ = ["run_periodic"]
