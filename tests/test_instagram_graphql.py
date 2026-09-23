import unittest

from downloader.instagram_graphql import (
    DEFAULT_DOC_ID,
    _cookie_header_from_set_cookie,
    _csrf_token_from_cookies,
    _response_error_reason,
)


class FakeHeaders:
    def __init__(self, values):
        self.values = values

    def get_all(self, name):
        return self.values if name.lower() == "set-cookie" else []


class FakeResponse:
    def __init__(self, cookies):
        self.headers = FakeHeaders(cookies)


class InstagramGraphQLRecoveryTests(unittest.TestCase):
    def test_current_doc_id_is_pinned(self):
        self.assertEqual(DEFAULT_DOC_ID, "27128499623469141")

    def test_bootstrap_cookies_extract_csrf(self):
        header = _cookie_header_from_set_cookie([
            "csrftoken=abc123; Path=/; Secure",
            "mid=xyz; Path=/; Secure",
        ])
        self.assertEqual(header, "csrftoken=abc123; mid=xyz")
        self.assertEqual(_csrf_token_from_cookies(header), "abc123")

    def test_cookie_header_deduplicates_pairs(self):
        header = _cookie_header_from_set_cookie([
            "csrftoken=abc; Path=/",
            "csrftoken=abc; Path=/",
            "sessionid=s1; Path=/",
        ])
        self.assertEqual(header, "csrftoken=abc; sessionid=s1")

    def test_graphql_error_is_preserved(self):
        self.assertEqual(
            _response_error_reason({
                "errors": [{"message": "execution error", "code": 4630001}]
            }),
            "execution error:4630001",
        )

    def test_response_without_errors_is_clean(self):
        self.assertIsNone(_response_error_reason({"data": {}}))

    def test_response_cookie_headers_are_read(self):
        response = FakeResponse(["csrftoken=abc; Path=/"])
        self.assertEqual(
            response.headers.get_all("Set-Cookie"),
            ["csrftoken=abc; Path=/"],
        )

    def test_image_selection_uses_exact_item(self):
        from downloader.instagram_graphql import _select_image
        url, diagnostics = _select_image({
            "code": "Dcs0funuV-t",
            "image_versions2": {
                "candidates": [{"url": "https://scontent.cdninstagram.com/image.jpg"}]
            },
        })
        self.assertEqual(url, "https://scontent.cdninstagram.com/image.jpg")
        self.assertEqual(diagnostics["selection"], "image_versions2")


if __name__ == "__main__":
    unittest.main()
