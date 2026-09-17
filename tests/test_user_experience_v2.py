from plugins.user_experience_v2 import _ALLOWED, _set_preferences


def test_preference_choices_are_disjoint_and_complete():
    assert set(_ALLOWED["video"]).isdisjoint(_ALLOWED["audio"])
    assert "video_720" in _ALLOWED["video"]
    assert "audio_320" in _ALLOWED["audio"]


def test_set_preferences_rejects_cross_type_quality():
    class FakeBot:
        def get_db(self):
            raise AssertionError("database must not be touched for invalid input")

    try:
        _set_preferences(FakeBot(), 1, "video", "audio_320")
    except ValueError:
        return
    raise AssertionError("cross-type quality must be rejected")
