from media.audio_service import AudioService
from media.video_service import VideoService


def test_video_quality_policy_is_separate_and_deterministic():
    service = VideoService()
    assert "height<=1080" in service.format_selector("video_1080")
    assert "height<=480" in service.format_selector("video_480")
    assert service.quality_name("video_360") == "360p"


def test_audio_quality_policy_is_separate_and_deterministic():
    service = AudioService()
    assert service.format_selector("audio_320") == "bestaudio/best[acodec!=none]"
    assert service.audio_quality("audio_320") == "320K"
    assert service.audio_quality("audio_128") == "128K"


def test_invalid_media_quality_is_rejected():
    video = VideoService()
    audio = AudioService()
    for choice in ("audio_320", "invalid"):
        if choice == "audio_320":
            try:
                video.format_selector(choice)
            except ValueError:
                pass
            else:
                raise AssertionError("video service accepted audio quality")
        else:
            try:
                audio.format_selector(choice)
            except ValueError:
                pass
            else:
                raise AssertionError("audio service accepted invalid quality")
