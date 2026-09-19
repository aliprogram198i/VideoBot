from types import SimpleNamespace

from downloader.resolver_contract import enforce_source_identity


def item(url, *, valid=True, kind="video", page="https://source.test/post"):
    return SimpleNamespace(
        valid=valid,
        candidate=SimpleNamespace(url=url, kind=kind, source_page=page),
    )


def test_unprotected_source_preserves_best_candidate():
    best = item("https://cdn.test/video.mp4")
    selected, gate = enforce_source_identity(
        "https://example.com/post",
        [best],
        best.candidate,
        telegram_parser=lambda _: None,
        telegram_matcher=lambda *_: False,
        instagram_parser=lambda _: None,
        instagram_matcher=lambda *_: False,
    )
    assert selected is best.candidate
    assert gate.required is False


def test_telegram_gate_rejects_unrelated_candidate():
    source = SimpleNamespace(key="channel/42")
    candidate = item("https://cdn.test/video.mp4")
    selected, gate = enforce_source_identity(
        "https://t.me/channel/42",
        [candidate],
        candidate.candidate,
        telegram_parser=lambda _: source,
        telegram_matcher=lambda *_: False,
        instagram_parser=lambda _: None,
        instagram_matcher=lambda *_: False,
    )
    assert selected is None
    assert gate.required is True
    assert gate.matched_candidates == 0
