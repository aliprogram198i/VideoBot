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


def test_rejects_video_markers_tied_to_exact_source_in_image_resolver(tmp_path):
    page = (
        '<meta property="og:image" '
        'content="https://scontent.cdninstagram.com/cover.jpg">'
        '{"code":"Dcs0funuV-t","video_url":"https://scontent.cdninstagram.com/video.mp4"}'
    )

    def request_factory(*args, **kwargs):
        return object()

    def open_function(request, **kwargs):
        return _Response(page.encode())

    path, diagnostics = download_instagram_image(
        SOURCE,
        tmp_path,
        request_factory=request_factory,
        open_function=open_function,
    )

    assert path is None
    assert diagnostics["status"] == "skipped"
    assert diagnostics["reason"] == "video_or_mixed_instagram_post"


def test_image_extension_uses_magic_bytes():
    assert _image_extension("application/octet-stream", bytes.fromhex("ffd8ff") + b"abc", "x") == ".jpg"
    assert _image_extension("application/octet-stream", bytes.fromhex("89504e470d0a1a0a") + b"abc", "x") == ".png"


def test_download_instagram_image_preserves_exact_source_identity(tmp_path):
    image_bytes = bytes.fromhex("ffd8ff") + b"fake-jpeg-payload"

    def request_factory(*args, **kwargs):
        return object()

    page = (
        '<meta property="og:image" '
        'content="https://scontent.cdninstagram.com/example.jpg">'
    )
    responses = iter([
        _Response(page.encode()),
        _Response(image_bytes),
    ])

    def open_function(request, **kwargs):
        return next(responses)

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


def test_accepts_realistic_instagram_cdn_subdomains():
    page = (
        '<meta property="og:image" '
        'content="https://scontent-ams4-1.cdninstagram.com/example.jpg">'
    )
    candidates = _extract_html_image_urls(page, SOURCE)
    assert candidates == ["https://scontent-ams4-1.cdninstagram.com/example.jpg"]


def test_photo_post_ignores_unrelated_video_schema():
    page = (
        '<meta content="https://scontent-ams4-1.cdninstagram.com/example.jpg" '
        'property="og:image">'
        '{"other_post":{"video_versions":["https://scontent.cdninstagram.com/other.mp4"]}}'
    )

    def request_factory(*args, **kwargs):
        return object()

    responses = iter([
        _Response(page.encode()),
        _Response(bytes.fromhex("ffd8ff") + b"photo"),
    ])

    def open_function(request, **kwargs):
        return next(responses)

    path, diagnostics = download_instagram_image(
        SOURCE,
        Path("/tmp"),
        request_factory=request_factory,
        open_function=open_function,
    )

    assert path is not None
    assert diagnostics["status"] == "success"
    assert diagnostics["source_identity_verified"] is True
