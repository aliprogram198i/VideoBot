"""Isolated 200-URL paired-resolver evidence harness.

This harness is staging-only and writes to a dedicated SQLite database. It does
not call Telegram download handlers, does not serve users, and never changes
resolver ordering. Sampling is intentionally forced to include each supplied
URL because this is a bounded offline/staging stress harness, not production
statistical validation. Its database must never be used as production evidence.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

# When Python executes ``scripts/foo.py``, sys.path[0] is /app/scripts.
# The production modules (including bot.py) live one directory above it.
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

SOURCE = "https://raw.githubusercontent.com/aliprogram198i/VideoBot/test/resolver-200-url-validation/tests/manual_resolver_200_urls.py"
DB_PATH = Path(os.getenv("ALIBOT_EVIDENCE_HARNESS_DB", "/app/data/paired_evidence_harness.db"))


def _load_urls() -> list[str]:
    with urllib.request.urlopen(SOURCE, timeout=20) as response:
        text = response.read().decode("utf-8")
    urls = re.findall(r"^https?://[^\s'\"]+$", text, re.MULTILINE)
    if len(urls) != 200:
        raise RuntimeError(f"Expected exactly 200 URLs, got {len(urls)}")
    return urls


def _platform(url: str) -> str:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    if "youtube" in host or host == "youtu.be":
        return "youtube"
    if "tiktok" in host:
        return "tiktok"
    if "instagram" in host:
        return "instagram"
    if "facebook" in host or host == "fb.watch":
        return "facebook"
    if host in {"x.com", "twitter.com"}:
        return "twitter"
    return "other"


def _print_statistical_summary(store, resolver_names: tuple[str, ...]) -> None:
    """Print the repository's conservative paired-validation results."""
    import sqlite3

    validation = importlib.import_module("downloader.resolver_statistical_validation")
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT sample_id) FROM resolver_evidence"
        ).fetchone()
        rows, samples = int(row[0] or 0), int(row[1] or 0)
        complete4 = conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT sample_id
                FROM resolver_evidence
                GROUP BY sample_id
                HAVING COUNT(DISTINCT resolver) = 4
            )
            """
        ).fetchone()[0]
        platforms = [r[0] for r in conn.execute(
            "SELECT DISTINCT platform FROM resolver_evidence ORDER BY platform"
        )]

    print("=== STATISTICAL SUMMARY (ISOLATED HARNESS DB) ===", flush=True)
    print(f"rows={rows} distinct_samples={samples} complete4={int(complete4)}", flush=True)
    print("gates: min_samples=30 min_discordant=10 alpha=0.05 min_effect=0.10", flush=True)

    def emit(scope: str, platform: str = "unknown") -> None:
        results = validation.validate_resolver_set(
            store,
            resolver_names,
            platform=platform,
            media_kind="unknown",
        )
        print(f"-- {scope} --", flush=True)
        if not results:
            print("no pairwise results", flush=True)
            return
        for item in results:
            print(
                f"{item['resolver_a']} vs {item['resolver_b']} "
                f"n={item['paired_samples']} "
                f"success={item['a_successes']}/{item['b_successes']} "
                f"a_only={item['a_only']} b_only={item['b_only']} "
                f"delta={item['success_rate_delta']:.4f} "
                f"CI95=[{item['delta_lower_95']:.4f},{item['delta_upper_95']:.4f}] "
                f"p={item['mcnemar_p_value']:.6g} "
                f"significant={item['statistically_significant']} "
                f"meaningful={item['practically_meaningful']} "
                f"validated_advantage={item['validated_advantage']}",
                flush=True,
            )

    emit("GLOBAL")
    for platform in platforms:
        emit(f"PLATFORM {platform}", platform)


async def main() -> None:
    if os.getenv("ALIBOT_RUNTIME_ENV", "").strip().lower() != "staging":
        raise RuntimeError("200-URL evidence harness is staging-only")
    if os.getenv("ALIBOT_PAIRED_EVIDENCE_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError("200-URL evidence harness requires explicit evidence opt-in")

    bot_module = importlib.import_module("bot")
    runtime_config = importlib.import_module("plugins.runtime_config")
    runtime_config.apply_to_bot_module(bot_module)
    importlib.import_module("plugins.yoinku_compat").install(bot_module)
    importlib.import_module("security.download_guard").install_download_guards(bot_module)
    importlib.import_module("downloader.shhaiid4u_resolver").install(bot_module)
    importlib.import_module("downloader.shhaiid4u_network_discovery").install(bot_module)
    importlib.import_module("downloader.shhaiid4u_player_bridge").install(bot_module)
    importlib.import_module("downloader.movie_source_guard").install(bot_module)

    legacy_probe = getattr(bot_module, "extract_direct_media_urls", None)
    importlib.import_module("downloader.smart_media_bridge").install(bot_module)
    if callable(legacy_probe):
        bot_module._alibot_legacy_extractor_probe = legacy_probe

    observer = importlib.import_module("downloader.shadow_runtime_observer")
    probes = observer._build_paired_probes(bot_module)
    if len(probes) < 2:
        raise RuntimeError(f"Expected at least 2 resolver probes, got {len(probes)}")

    collector = importlib.import_module("downloader.paired_evidence_collector")
    store_cls = importlib.import_module("downloader.resolver_evidence").ResolverEvidenceStore
    store = store_cls(DB_PATH)
    resolver_names = tuple(name for name in observer._PAIRED_PROBE_ORDER if name in probes)
    urls = _load_urls()
    from downloader.smart_media_bridge import _resolver_context

    print(f"🧪 200-URL Evidence Harness: URLs={len(urls)} probes={resolver_names}", flush=True)
    print(f"🗄️ Dedicated evidence DB: {DB_PATH}", flush=True)

    class _ForceSample(random.Random):
        def random(self) -> float:
            return 0.0

    rng = _ForceSample()
    totals = {}
    for index, source_url in enumerate(urls, 1):
        platform = _platform(source_url)
        try:
            platform, media_kind = _resolver_context(source_url, "unknown")
        except Exception:
            media_kind = "unknown"
        started = time.monotonic()
        try:
            stored = await collector.collect(
                store,
                sample_id=f"harness-200-{index:03d}",
                platform=platform,
                media_kind=media_kind,
                source_url=source_url,
                resolvers={name: probes[name] for name in resolver_names},
                rng=rng,
            )
            key = f"{platform}/{media_kind}"
            bucket = totals.setdefault(key, {"attempted": 0, "stored": 0})
            bucket["attempted"] += 1
            bucket["stored"] += int(bool(stored))
            print(f"[{index}/200] {platform}/{media_kind} stored={stored} elapsed_ms={(time.monotonic()-started)*1000:.0f}", flush=True)
        except Exception as exc:
            print(f"[{index}/200] {platform}/{media_kind} ERROR {type(exc).__name__}: {str(exc)[:180]}", flush=True)

    print("=== HARNESS COMPLETE ===", flush=True)
    print(totals, flush=True)
    print(f"DB={DB_PATH}", flush=True)
    _print_statistical_summary(store, resolver_names)


if __name__ == "__main__":
    asyncio.run(main())
