import tempfile
import unittest
from pathlib import Path

from downloader.resolver_admission import admit_local_media, admit_media_artifact


class ResolverAdmissionContractTests(unittest.TestCase):
    def test_instagram_requires_identity_attestation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "video.mp4"
            media.write_bytes(b"media")
            path, result = admit_local_media(
                "https://www.instagram.com/reel/ABC123/",
                str(media),
                temp_dir=temp_dir,
                resolver="instagram_graphql",
                diagnostics={},
            )
            self.assertIsNone(path)
            self.assertEqual(result["reason"], "instagram_identity_proof_missing_or_mismatch")

    def test_instagram_accepts_matching_attestation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "video.mp4"
            media.write_bytes(b"media")
            path, result = admit_local_media(
                "https://www.instagram.com/reel/ABC123/",
                str(media),
                temp_dir=temp_dir,
                resolver="instagram_graphql",
                diagnostics={
                    "source_identity_verified": True,
                    "identity_proof": {
                        "type": "instagram_shortcode",
                        "key": "ABC123",
                    },
                },
            )
            self.assertEqual(path, str(media.resolve()))
            self.assertTrue(result["admitted"])

    def test_instagram_rejects_untrusted_resolver(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "video.mp4"
            media.write_bytes(b"media")
            path, result = admit_local_media(
                "https://www.instagram.com/reel/ABC123/",
                str(media),
                temp_dir=temp_dir,
                resolver="generic_browser",
                diagnostics={"source_identity_verified": True},
            )
            self.assertIsNone(path)
            self.assertEqual(result["reason"], "instagram_untrusted_resolver")

    def test_file_outside_temp_dir_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as other_dir:
            media = Path(other_dir) / "video.mp4"
            media.write_bytes(b"media")
            path, result = admit_local_media(
                "https://example.com/video",
                str(media),
                temp_dir=temp_dir,
                resolver="direct",
                diagnostics={},
            )
            self.assertIsNone(path)
            self.assertEqual(result["reason"], "media_outside_temp_dir")

    def test_instagram_rejects_mismatched_proof(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "video.mp4"
            media.write_bytes(b"media")
            path, result = admit_local_media(
                "https://www.instagram.com/reel/ABC123/",
                str(media),
                temp_dir=temp_dir,
                resolver="instagram_graphql",
                diagnostics={
                    "source_identity_verified": True,
                    "identity_proof": {
                        "type": "instagram_shortcode",
                        "key": "WRONG123",
                    },
                },
            )
            self.assertIsNone(path)
            self.assertEqual(result["reason"], "instagram_identity_proof_missing_or_mismatch")

    def test_instagram_image_accepts_matching_identity_and_signature(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "image.jpg"
            media.write_bytes(b"\\xff\\xd8\\xff" + b"image")
            path, result = admit_media_artifact(
                "https://www.instagram.com/p/Dcs0funuV-t/",
                str(media),
                temp_dir=temp_dir,
                resolver="instagram_image",
                diagnostics={
                    "source_identity_verified": True,
                    "identity_proof": {
                        "type": "instagram_shortcode",
                        "key": "Dcs0funuV-t",
                    },
                },
                media_type="image",
            )
            self.assertEqual(path, str(media.resolve()))
            self.assertTrue(result["admitted"])

    def test_telegram_requires_identity_attestation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "video.mp4"
            media.write_bytes(b"media")
            path, result = admit_local_media(
                "https://t.me/example/123",
                str(media),
                temp_dir=temp_dir,
                resolver="download_pipeline",
                diagnostics={},
            )
            self.assertIsNone(path)
            self.assertEqual(result["reason"], "telegram_identity_proof_missing_or_mismatch")

    def test_universal_artifact_gate_fails_closed_on_invalid_media(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "video.mp4"
            media.write_bytes(b"not-a-real-video")
            path, result = admit_media_artifact(
                "https://example.com/video",
                str(media),
                temp_dir=temp_dir,
                resolver="download_pipeline",
                diagnostics={},
                media_type="video",
            )
            self.assertIsNone(path)
            self.assertFalse(result["admitted"])
            self.assertIn(result["reason"], {
                "ffprobe_rejected_media",
                "ffprobe_unavailable_or_failed",
                "expected_media_stream_missing",
            })

if __name__ == "__main__":
    unittest.main()
