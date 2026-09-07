"""Safe media-processing primitives.

The existing downloader/compression implementation remains the source of
truth. This service only owns process execution policy so future migrations do
not duplicate shell handling or invent unsafe command construction.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class MediaService:
    def __init__(self, ffmpeg_binary: str = "ffmpeg") -> None:
        self.ffmpeg_binary = ffmpeg_binary

    def available(self) -> bool:
        return shutil.which(self.ffmpeg_binary) is not None

    def transcode_audio(
        self,
        source: str | Path,
        destination: str | Path,
        *,
        bitrate: str = "128k",
    ) -> Path:
        src = Path(source)
        dst = Path(destination)
        if not src.is_file():
            raise FileNotFoundError(src)
        if not bitrate or any(ch in bitrate for ch in ";&|`$\n\r"):
            raise ValueError("bitrate contains unsafe characters")
        if not self.available():
            raise RuntimeError("ffmpeg is not available")
        dst.parent.mkdir(parents=True, exist_ok=True)
        command = [self.ffmpeg_binary, "-y", "-i", str(src), "-vn", "-b:a", bitrate, str(dst)]
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return dst
