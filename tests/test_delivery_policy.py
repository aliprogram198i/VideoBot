import tempfile
import unittest
from pathlib import Path

from delivery.policy import DeliveryPolicy


class DeliveryPolicyTests(unittest.TestCase):
    def test_artifact_policy_allows_audio_that_will_be_split(self):
        policy = DeliveryPolicy()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "large.mp3"
            with path.open("wb") as handle:
                handle.truncate(48 * 1024 * 1024)
            self.assertEqual(policy.validate_file(path, media_type="audio"), path)

    def test_audio_upload_gate_rejects_oversized_part(self):
        policy = DeliveryPolicy()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "part.mp3"
            with path.open("wb") as handle:
                handle.truncate(48 * 1024 * 1024)
            with self.assertRaises(ValueError):
                policy.validate_telegram_upload(path, media_type="audio")

    def test_video_upload_gate_accepts_telegram_safe_part(self):
        policy = DeliveryPolicy()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "part.mp4"
            path.write_bytes(b"video")
            self.assertEqual(policy.validate_telegram_upload(path, media_type="video"), path)


if __name__ == "__main__":
    unittest.main()
