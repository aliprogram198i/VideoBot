import asyncio
import os


def final_output_from_yt_dlp(stdout_text, temp_dir, extensions):
    """Resolve the final yt-dlp artifact safely from explicit output first."""
    base = os.path.realpath(temp_dir) + os.sep

    # Primary source: yt-dlp --print after_move:filepath
    for line in reversed(stdout_text.splitlines()):
        candidate = line.strip()
        if (
            candidate
            and candidate.lower().endswith(extensions)
            and os.path.realpath(candidate).startswith(base)
            and os.path.isfile(candidate)
        ):
            return os.path.realpath(candidate)

    # Defensive fallback: only inspect direct files created in our
    # trusted temporary directory. Never recurse outside temp_dir.
    try:
        candidates = []
        for name in os.listdir(temp_dir):
            candidate = os.path.join(temp_dir, name)
            real_candidate = os.path.realpath(candidate)

            if not real_candidate.startswith(base):
                continue
            if not os.path.isfile(real_candidate):
                continue
            if not name.startswith("download_"):
                continue
            if not name.lower().endswith(extensions):
                continue

            candidates.append(real_candidate)

        if candidates:
            candidates.sort(
                key=lambda p: os.path.getmtime(p),
                reverse=True,
            )
            return candidates[0]
    except OSError:
        pass

    return None


async def communicate_with_cleanup(process, timeout, shutdown_timeout):
    """Wait for a child process and reliably reap it on timeout/cancellation."""
    try:
        return await asyncio.wait_for(process.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=shutdown_timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        raise
