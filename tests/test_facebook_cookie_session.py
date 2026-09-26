import base64

from bot import _prepare_facebook_cookie_file


COOKIE_TEXT = """# Netscape HTTP Cookie File
.facebook.com	TRUE	/	TRUE	0	c_user	123
"""


def test_facebook_cookie_file_is_materialized(tmp_path, monkeypatch):
    encoded = base64.b64encode(COOKIE_TEXT.encode()).decode()
    monkeypatch.setenv("FACEBOOK_COOKIES_B64", encoded)

    path = _prepare_facebook_cookie_file(
        "https://www.facebook.com/reel/2115871489331970",
        str(tmp_path),
    )

    assert path is not None
    assert open(path, encoding="utf-8").read() == COOKIE_TEXT


def test_facebook_cookie_file_is_ignored_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("FACEBOOK_COOKIES_B64", raising=False)

    assert _prepare_facebook_cookie_file(
        "https://www.facebook.com/reel/2115871489331970",
        str(tmp_path),
    ) is None


def test_facebook_cookie_file_is_scoped_to_facebook(tmp_path, monkeypatch):
    encoded = base64.b64encode(COOKIE_TEXT.encode()).decode()
    monkeypatch.setenv("FACEBOOK_COOKIES_B64", encoded)

    assert _prepare_facebook_cookie_file(
        "https://www.youtube.com/watch?v=x",
        str(tmp_path),
    ) is None
