from __future__ import annotations

import os

from entrypoint import normalize_runtime_environment


def test_production_configures_default_pot_provider(monkeypatch):
    monkeypatch.delenv("YTDLP_POT_BASE_URL", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    normalize_runtime_environment()
    assert os.environ["YTDLP_POT_BASE_URL"] == "http://youtube-pot-provider.railway.internal:4416"


def test_explicit_pot_provider_is_preserved(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    monkeypatch.setenv("YTDLP_POT_BASE_URL", "http://custom-provider:4416")
    normalize_runtime_environment()
    assert os.environ["YTDLP_POT_BASE_URL"] == "http://custom-provider:4416"


def test_staging_does_not_assume_production_provider(monkeypatch):
    monkeypatch.delenv("YTDLP_POT_BASE_URL", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "staging")
    normalize_runtime_environment()
    assert "YTDLP_POT_BASE_URL" not in os.environ
