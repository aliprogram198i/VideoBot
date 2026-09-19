"""Pure source website classification helpers."""

from urllib.parse import urlparse


def detect_website(url):
    host = urlparse(url).netloc.lower().replace("www.", "")

    if "youtube.com" in host or "youtu.be" in host:
        return "YouTube"
    if "instagram.com" in host:
        return "Instagram"
    if "tiktok.com" in host:
        return "TikTok"
    if "facebook.com" in host or "fb.watch" in host:
        return "Facebook"
    if host in {"t.me", "telegram.me"}:
        return "Telegram"
    if "twitter.com" in host or "x.com" in host:
        return "X / Twitter"
    if "reddit.com" in host:
        return "Reddit"
    return host or "Other"
