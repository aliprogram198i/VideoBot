import asyncio
import os
import tempfile
import unittest

from downloader.process_utils import (
    communicate_with_cleanup,
    final_output_from_yt_dlp,
)


class ProcessUtilsTests(unittest.TestCase):
    def test_final_output_prefers_explicit_yt_dlp_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "download_video.mp4")
            with open(path, "wb") as handle:
                handle.write(b"video")

            self.assertEqual(
                final_output_from_yt_dlp(f"ignored\n{path}\n", tmp, (".mp4",)),
                path,
            )

    def test_final_output_uses_direct_trusted_directory_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "download_audio.mp3")
            with open(path, "wb") as handle:
                handle.write(b"audio")

            self.assertEqual(
                final_output_from_yt_dlp("", tmp, (".mp3",)),
                path,
            )

    def test_final_output_rejects_untrusted_path(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.NamedTemporaryFile(
            suffix=".mp4",
        ) as outside:
            self.assertIsNone(
                final_output_from_yt_dlp(
                    outside.name,
                    tmp,
                    (".mp4",),
                )
            )

    def test_communicate_with_cleanup_returns_process_output(self):
        async def run():
            process = await asyncio.create_subprocess_exec(
                "python",
                "-c",
                "print('ok')",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await communicate_with_cleanup(
                process,
                timeout=10,
                shutdown_timeout=2,
            )
            return stdout, stderr, process.returncode

        stdout, stderr, returncode = asyncio.run(run())
        self.assertEqual(stdout.decode().strip(), "ok")
        self.assertEqual(stderr, b"")
        self.assertEqual(returncode, 0)


if __name__ == "__main__":
    unittest.main()
