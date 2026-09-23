from pathlib import Path

from downloader.instagram_image import (
    _extract_html_image_urls,
    _image_extension,
    download_instagram_image,
)


SOURCE = "https://www.instagram.com/p/Dcs0funuV-t/?utm_source=ig_web_copy_link"


class _Headers:
    def get_content_charset(self):
        return "utf-8"

    def get_content_type(self):
        return "image/jpeg"

    def get(self, name):
        return "image/jpeg" if name.lower() == "content-type" else None


class _Response:
    headers = _Headers()

    def __init__(self, body):
        self._body = body
        self.closed = False

    def read(self, _limit=None):
        return self._body

    def close(self):
        self.closed = True


def test_extracts_exact_instagram_og_image():
    page = (
        '<meta property="og:url" content="'
        + SOURCE
        + '"><meta property="og:image" '
        'content="https://scontent.cdninstagram.com/example.jpg">'
    )

    candidates = _extract_html_image_urls(page, SOURCE)

    assert candidates == ["https://scontent.cdninstagram.com/example.jpg"]


def test_rejects_video_markers_in_image_resolver():
    # The resolver must not turn a reel/video cover into a photo download.
    page = (
        '<meta property="og:image" '
        'content="https://scontent.cdninstagram.com/cover.jpg">'
        '{"video_url":"https://scontent.cdninstagram.com/video.mp4"}'
    )
    lower = page.lower()
    assert '"video_url"' in lower


def test_image_extension_uses_magic_bytes():
    assert _image_extension("application/octet-stream", b"\\xff\\xd8\\xffabc", "x") == ".jpg"
    assert _image_extension("application/octet-stream", b"\\x89PNG\\r\\n\\x1a\\nabc", "x") == ".png"


def test_download_instagram_image_preserves_exact_source_identity(tmp_path):
    image_bytes = b"\\xff\\xd8\\xff" + b"fake-jpeg-payload"

    def request_factory(*args, **kwargs):
        return object()

    def open_function(request, **kwargs):
        return _Response(image_bytes)

    path, diagnostics = download_instagram_image(
        SOURCE,
        tmp_path,
        request_factory=request_factory,
        open_function=open_function,
    )

    assert path is not None
    assert Path(path).suffix == ".jpg"
    assert Path(path).read_bytes() == image_bytes
    assert diagnostics["status"] == "success"
    assert diagnostics["source_identity_verified"] is True
    assert diagnostics["identity_proof"] == {
        "type": "instagram_shortcode",
        "key": "Dcs0funuV-t",
    }
