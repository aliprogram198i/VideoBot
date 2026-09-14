"""Staging-only real-request harness for paired resolver evidence."""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import time

APP_ROOT = "/app"
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

TEST_URLS = ("https://www.youtube.com/watch?v=dQw4w9WgXcQ",)

async def main() -> None:
    if os.getenv("ALIBOT_RUNTIME_ENV", "").strip().lower() != "staging":
        raise RuntimeError("Paired evidence harness is staging-only")
    if os.getenv("ALIBOT_PAIRED_EVIDENCE_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError("Paired evidence harness requires explicit evidence opt-in")

    bot_module = importlib.import_module("bot")
    runtime_config = importlib.import_module("plugins.runtime_config")
    install_yoinku_compat = importlib.import_module("plugins.yoinku_compat").install
    install_download_guards = importlib.import_module("security.download_guard").install_download_guards
    install_shhaiid4u_resolver = importlib.import_module("downloader.shhaiid4u_resolver").install
    install_shhaiid4u_network_discovery = importlib.import_module("downloader.shhaiid4u_network_discovery").install
    install_shhaiid4u_player_bridge = importlib.import_module("downloader.shhaiid4u_player_bridge").install
    install_smart_media_bridge = importlib.import_module("downloader.smart_media_bridge").install
    install_shadow_runtime_observer = importlib.import_module("downloader.shadow_runtime_observer").install
    install_adaptive_orchestrator = importlib.import_module("downloader.adaptive_download_orchestrator").install
    install_movie_source_guard = importlib.import_module("downloader.movie_source_guard").install
    run_evidence_monitor = importlib.import_module("downloader.evidence_monitor").run_periodic
    smart_telemetry_store = importlib.import_module("downloader.smart_learning").SmartTelemetryStore

    runtime_config.apply_to_bot_module(bot_module)
    install_yoinku_compat(bot_module)
    install_download_guards(bot_module)
    install_shhaiid4u_resolver(bot_module)
    install_shhaiid4u_network_discovery(bot_module)
    install_shhaiid4u_player_bridge(bot_module)
    install_movie_source_guard(bot_module)
    legacy_probe = getattr(bot_module, "extract_direct_media_urls", None)
    install_smart_media_bridge(bot_module)
    if callable(legacy_probe):
        bot_module._alibot_legacy_extractor_probe = legacy_probe
    install_shadow_runtime_observer(bot_module)
    install_adaptive_orchestrator(bot_module)

    db_path = smart_telemetry_store().db_path
    monitor_task = asyncio.create_task(run_evidence_monitor(db_path))
    print("🧪 Paired Evidence Harness: initialized real extraction stack.", flush=True)
    print("📈 Paired Evidence Monitor: started by isolated harness.", flush=True)

    extractor = getattr(bot_module, "extract_direct_media_urls", None)
    if not callable(extractor):
        raise RuntimeError("extract_direct_media_urls is unavailable")

    interval = max(5.0, float(os.getenv("ALIBOT_EVIDENCE_HARNESS_INTERVAL", "15")))
    rounds = max(1, int(os.getenv("ALIBOT_EVIDENCE_HARNESS_ROUNDS", "0")))
    completed = 0
    try:
        while rounds == 0 or completed < rounds:
            for source_url in TEST_URLS:
                started = time.monotonic()
                try:
                    result = extractor(source_url)
                    if hasattr(result, "__await__"):
                        result = await result
                    count = len(result) if isinstance(result, (list, tuple)) else int(bool(result))
                    print(f"🧪 Paired Evidence Harness: extraction completed candidates={count} elapsed_ms={(time.monotonic()-started)*1000:.0f}", flush=True)
                except Exception as exc:
                    print(f"⚠️ Paired Evidence Harness: extraction failed {type(exc).__name__}", flush=True)
            completed += 1
            await asyncio.sleep(interval)
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

if __name__ == "__main__":
    asyncio.run(main())
