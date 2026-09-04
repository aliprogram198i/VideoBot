import unittest

from downloader.candidate_ranker import CandidateRanker
from downloader.candidate_validator import CandidateValidator
from downloader.embed_resolver import EmbedResolver
from downloader.page_fetcher import PageFetcher
from downloader.production_network import SmartNetworkAdapter
from downloader.production_page_fetcher import ProductionPageFetcher
from downloader.production_stack import (
    ProductionSmartExtractionStack,
    build_production_smart_extraction_stack,
)
from downloader.smart_engine import SmartExtractionEngine


class ProductionStackTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def url_validator(url):
            self.calls.append(("validate", url))

        def request_factory(url, headers=None):
            self.calls.append(("request", url, dict(headers or {})))
            return {"url": url, "headers": headers}

        def open_function(request, **kwargs):
            self.calls.append(("open", request, dict(kwargs)))

            class Response:
                status = 200
                code = 200

                class Headers:
                    @staticmethod
                    def get(name):
                        if name.lower() == "content-type":
                            return "text/html; charset=utf-8"
                        return None

                headers = Headers()

                def close(self):
                    pass

            return Response()

        def read_function(response, max_bytes):
            return b"<html><body></body></html>"

        self.stack = build_production_smart_extraction_stack(
            url_validator=url_validator,
            request_factory=request_factory,
            open_function=open_function,
            read_function=read_function,
        )

    def test_builds_complete_stack(self):
        self.assertIsInstance(self.stack, ProductionSmartExtractionStack)
        self.assertIsInstance(self.stack.engine, SmartExtractionEngine)
        self.assertIsInstance(self.stack.page_fetcher, PageFetcher)
        self.assertIsInstance(self.stack.resolver, EmbedResolver)
        self.assertIsInstance(self.stack.network, SmartNetworkAdapter)

    def test_components_are_connected(self):
        self.assertIs(self.stack.resolver.page_fetcher, self.stack.page_fetcher)
        self.assertIs(self.stack.engine.resolver, self.stack.resolver)
        self.assertIsInstance(self.stack.engine.validator, CandidateValidator)
        self.assertIsInstance(self.stack.engine.ranker, CandidateRanker)

    def test_page_fetcher_uses_production_adapter(self):
        self.stack.page_fetcher.fetch(
            "https://example.com/",
            timeout=5,
            max_bytes=1024,
        )

        kinds = [item[0] for item in self.calls]
        self.assertEqual(kinds, ["request", "open"])

    def test_invalid_limits_are_rejected(self):
        common = dict(
            url_validator=lambda url: None,
            request_factory=lambda url, headers=None: url,
            open_function=lambda request, **kwargs: None,
            read_function=lambda response, max_bytes: b"",
        )

        with self.assertRaises(ValueError):
            build_production_smart_extraction_stack(
                **common,
                max_html_bytes=0,
            )

        with self.assertRaises(ValueError):
            build_production_smart_extraction_stack(
                **common,
                max_declared_bytes=0,
            )


if __name__ == "__main__":
    unittest.main()
