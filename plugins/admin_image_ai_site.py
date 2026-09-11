"""Website analysis and learning engine for the private Image AI Studio.

This module is intentionally isolated from the downloader. It crawls public pages on
an administrator-supplied site, extracts image/form/UI patterns, optionally inspects
representative images with Gemini vision, and stores the resulting learning notes in
the existing Image AI instruction memory.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import json
import logging
import re
import socket
import tempfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urldefrag, urlparse
from html.parser import HTMLParser

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from .admin_common import authorize

try:
    from google import genai
    from google.genai import types
except ImportError:  # pragma: no cover
    genai = None
    types = None

logger = logging.getLogger(__name__)

_MODE_KEY = "alibot_image_ai_site_mode"
_PERMISSION = "center.view"
_MAX_PAGES = 30
_MAX_DEPTH = 2
_MAX_PAGE_BYTES = 5 * 1024 * 1024
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_IMAGES = 24
_MAX_FORMS = 100
_MAX_TEXT = 12000
_ANALYSIS_MODEL = "gemini-2.5-flash-lite"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    configured = Path(__import__("os").getenv("IMAGE_AI_DATA_DIR", "/app/data/image_ai"))
    root = configured if configured.name == "image_ai" else configured / "image_ai"
    if not root.parent.exists() and str(root).startswith("/app/data"):
        root = Path(".image_ai")
    root.mkdir(parents=True, exist_ok=True)
    return root / "image_ai.db"


def _connect():
    import sqlite3
    conn = sqlite3.connect(_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema() -> None:
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS image_ai_sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                host TEXT NOT NULL,
                status TEXT NOT NULL,
                pages INTEGER NOT NULL DEFAULT 0,
                images INTEGER NOT NULL DEFAULT 0,
                forms INTEGER NOT NULL DEFAULT 0,
                learned_instructions INTEGER NOT NULL DEFAULT 0,
                summary TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_image_ai_sites_created ON image_ai_sites(id DESC);
            CREATE TABLE IF NOT EXISTS image_ai_site_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT,
                alt_text TEXT,
                metadata TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(site_id) REFERENCES image_ai_sites(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_image_ai_site_assets_site ON image_ai_site_assets(site_id, id DESC);
            """
        )
        conn.commit()
    finally:
        conn.close()


def _valid_url(value: str) -> str:
    value = value.strip()
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("INVALID_SITE_URL")
    return urldefrag(value)[0]


def _host_allowed(url: str, root_host: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    root = root_host.lower().rstrip(".")
    return host == root or host.endswith("." + root)


def _safe_remote_host(url: str) -> None:
    """Reject local/private destinations while leaving public-site content unrestricted."""
    host = urlparse(url).hostname
    if not host:
        raise ValueError("INVALID_SITE_HOST")
    try:
        addresses = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise ValueError("SITE_DNS_FAILED") from exc
    for item in addresses:
        ip = ipaddress.ip_address(item[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("SITE_HOST_NOT_PUBLIC")


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.images: list[dict[str, str]] = []
        self.forms: list[dict[str, Any]] = []
        self.text_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self._form: dict[str, Any] | None = None
        self._skip = 0
        self._title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs_d = {str(k).lower(): str(v or "") for k, v in attrs}
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
        if self._skip:
            return
        if tag == "a" and attrs_d.get("href"):
            self.links.append(attrs_d["href"])
        elif tag == "img":
            self.images.append({
                "src": attrs_d.get("src", ""),
                "srcset": attrs_d.get("srcset", ""),
                "alt": attrs_d.get("alt", ""),
                "title": attrs_d.get("title", ""),
                "width": attrs_d.get("width", ""),
                "height": attrs_d.get("height", ""),
            })
        elif tag == "meta":
            key = attrs_d.get("name") or attrs_d.get("property") or attrs_d.get("itemprop")
            if key and attrs_d.get("content"):
                self.meta[key.lower()] = attrs_d["content"]
        elif tag == "form":
            self._form = {"action": attrs_d.get("action", ""), "method": attrs_d.get("method", "get"), "fields": []}
            self.forms.append(self._form)
        elif tag in {"input", "textarea", "select", "button"} and self._form is not None:
            self._form["fields"].append({
                "tag": tag,
                "name": attrs_d.get("name", ""),
                "type": attrs_d.get("type", ""),
                "placeholder": attrs_d.get("placeholder", ""),
                "value": attrs_d.get("value", ""),
            })

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag == "form":
            self._form = None

    def handle_data(self, data):
        if self._skip:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self._title += text + " "
        self.text_parts.append(text)


def _extract_srcset(srcset: str) -> list[str]:
    return [item.strip().split()[0] for item in srcset.split(",") if item.strip()]


def _page_record(url: str, parser: _PageParser) -> dict[str, Any]:
    return {
        "url": url,
        "title": parser._title.strip()[:500],
        "text": " ".join(parser.text_parts)[:_MAX_TEXT],
        "meta": parser.meta,
        "links": parser.links,
        "images": parser.images,
        "forms": parser.forms[:_MAX_FORMS],
    }


def _normalize_link(base: str, raw: str) -> str | None:
    if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    url = urldefrag(urljoin(base, html.unescape(raw)))[0]
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return url


def _image_urls(page: dict[str, Any]) -> list[str]:
    out = []
    for item in page["images"]:
        for raw in [item.get("src", ""), *_extract_srcset(item.get("srcset", ""))]:
            url = _normalize_link(page["url"], raw)
            if url and url not in out:
                out.append(url)
    return out


async def _crawl(root: str, progress=None) -> tuple[list[dict[str, Any]], int]:
    root_host = (urlparse(root).hostname or "").lower()
    _safe_remote_host(root)
    queue = deque([(root, 0)])
    seen: set[str] = set()
    pages: list[dict[str, Any]] = []
    images: set[str] = set()
    timeout = httpx.Timeout(20.0, connect=10.0)
    headers = {"User-Agent": "AliBot-ImageAI-SiteLearner/1.0"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        while queue and len(pages) < _MAX_PAGES:
            url, depth = queue.popleft()
            if url in seen or not _host_allowed(url, root_host):
                continue
            seen.add(url)
            try:
                response = await client.get(url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "text/html" not in content_type.lower():
                    continue
                data = response.content
                if len(data) > _MAX_PAGE_BYTES:
                    data = data[:_MAX_PAGE_BYTES]
                parser = _PageParser()
                parser.feed(data.decode(response.encoding or "utf-8", errors="replace"))
                final_url = urldefrag(str(response.url))[0]
                record = _page_record(final_url, parser)
                pages.append(record)
                images.update(_image_urls(record))
                if progress:
                    await progress(len(pages), len(images), final_url)
                if depth < _MAX_DEPTH:
                    for raw in parser.links:
                        child = _normalize_link(final_url, raw)
                        if child and _host_allowed(child, root_host) and child not in seen:
                            queue.append((child, depth + 1))
            except Exception as exc:
                logger.info("Site learner skipped page %s: %s", url, type(exc).__name__)
    return pages, min(len(images), _MAX_IMAGES)


async def _download_images(image_urls: list[str]) -> list[tuple[str, bytes, str]]:
    result = []
    timeout = httpx.Timeout(15.0, connect=8.0)
    headers = {"User-Agent": "AliBot-ImageAI-SiteLearner/1.0"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for url in image_urls[:_MAX_IMAGES]:
            try:
                _safe_remote_host(url)
                response = await client.get(url)
                response.raise_for_status()
                data = response.content
                if len(data) > _MAX_IMAGE_BYTES:
                    continue
                mime = response.headers.get("content-type", "image/jpeg").split(";", 1)[0].strip()
                if not mime.startswith("image/"):
                    continue
                result.append((url, data, mime))
            except Exception as exc:
                logger.info("Site learner skipped image %s: %s", url, type(exc).__name__)
    return result


def _client():
    import os
    key = os.getenv("IMAGE_AI_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key or genai is None:
        return None
    return genai.Client(api_key=key)


async def _analyze_image(client, image_bytes: bytes, mime: str, url: str) -> str:
    prompt = (
        "Analyze this public website image for an administrator's private Image AI learning dataset. "
        "Describe visual style, composition, subject treatment, background, lighting, colors, typography if visible, "
        "editing characteristics, and any apparent before/after or template role. Be factual and concise. "
        "Do not invent details. Return plain text.\nSource URL: " + url
    )
    def run():
        part = types.Part.from_bytes(data=image_bytes, mime_type=mime)
        response = client.models.generate_content(
            model=_ANALYSIS_MODEL,
            contents=[part, prompt],
        )
        return (getattr(response, "text", "") or "").strip()
    return await asyncio.to_thread(run)


def _site_summary(pages: list[dict[str, Any]]) -> str:
    image_count = sum(len(p["images"]) for p in pages)
    form_count = sum(len(p["forms"]) for p in pages)
    titles = [p["title"] for p in pages if p["title"]]
    meta = {}
    for page in pages:
        meta.update(page["meta"])
    form_shapes = []
    for page in pages:
        for form in page["forms"]:
            fields = [f.get("name") or f.get("type") or f.get("tag") for f in form["fields"]]
            form_shapes.append(f"{form.get('method','get').upper()} {form.get('action','')} [{', '.join(fields[:12])}]")
    payload = {
        "pages_analyzed": len(pages),
        "images_discovered": image_count,
        "forms_discovered": form_count,
        "page_titles": titles[:30],
        "meta": dict(list(meta.items())[:40]),
        "forms": form_shapes[:100],
        "visible_text": "\n".join(p["text"] for p in pages)[:_MAX_TEXT],
    }
    return json.dumps(payload, ensure_ascii=False)


def _learn_instruction(site_url: str, summary: str, visual_notes: list[str]) -> str:
    compact = "\n".join(f"- {x}" for x in visual_notes[:24])
    return (
        f"Website learning profile for {site_url}.\n"
        "Use this as reference when the administrator asks for a similar image operation. "
        "Learned site structure, form patterns, image roles and visual characteristics. "
        "Do not claim details that are not present in the profile.\n"
        f"Site analysis: {summary[:7000]}\n"
        f"Visual image observations:\n{compact[:12000]}"
    )[:2000]


def _save_site(site_url: str, pages: list[dict[str, Any]], image_assets: list[dict[str, Any]], learned: str, summary: str) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO image_ai_sites(url,host,status,pages,images,forms,learned_instructions,summary,created_at,completed_at) "
            "VALUES (?,?, 'complete',?,?,?,?,?,?,?)",
            (site_url, urlparse(site_url).hostname or "", len(pages), len(image_assets),
             sum(len(p["forms"]) for p in pages), 1 if learned else 0, summary[:20000], _now(), _now()),
        )
        site_id = int(cur.lastrowid)
        for asset in image_assets:
            conn.execute(
                "INSERT INTO image_ai_site_assets(site_id,kind,url,title,alt_text,metadata,created_at) VALUES (?,?,?,?,?,?,?)",
                (site_id, "image", asset["url"], asset.get("title"), asset.get("alt"), json.dumps(asset.get("metadata", {}), ensure_ascii=False)[:8000], _now()),
            )
        if learned:
            conn.execute(
                "INSERT INTO image_ai_instructions(instruction,active,created_at,updated_at) VALUES (?,1,?,?)",
                (learned, _now(), _now()),
            )
        conn.commit()
        return site_id
    finally:
        conn.close()


def _keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 تحليل موقع جديد", callback_data="admin_image_ai_site")],
        [InlineKeyboardButton("📚 AI Studio", callback_data="admin_image_ai")],
    ])


def _authorized(update: Update, get_db: Any, owner_id: int) -> bool:
    return authorize(update, get_db, owner_id, _PERMISSION)


async def site_start_callback(update, context, get_db, owner_id):
    query = update.callback_query
    await query.answer()
    if not _authorized(update, get_db, owner_id):
        return
    context.user_data[_MODE_KEY] = "site"
    await query.edit_message_text(
        "🌐 <b>Website Learning Engine</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        "أرسل رابط الموقع. سيقوم النظام بتحليل الصفحات العامة المتاحة، الصور، النماذج، النصوص، وبنية الواجهة، ثم يفحص الصور المكتشفة ويضيف المعرفة الناتجة إلى ذاكرة Image AI.\n\n"
        "أرسل الرابط الآن:", parse_mode="HTML", reply_markup=_keyboard()
    )
    raise ApplicationHandlerStop


async def site_url_handler(update, context, get_db, owner_id):
    if not _authorized(update, get_db, owner_id):
        return
    if context.user_data.get(_MODE_KEY) != "site":
        return
    raw = (update.effective_message.text or "").strip()
    if not raw:
        return
    try:
        site_url = _valid_url(raw)
        _safe_remote_host(site_url)
    except ValueError as exc:
        await update.effective_message.reply_text(f"❌ تعذر قبول الرابط: {exc}")
        return

    context.user_data.pop(_MODE_KEY, None)
    _ensure_schema()
    status = await update.effective_message.reply_text("🌐 بدء تحليل الموقع... ⏳")

    async def progress(pages, images, url):
        if pages == 1 or pages % 5 == 0:
            try:
                await status.edit_text(f"🌐 تحليل الموقع...\nالصفحات: {pages}\nالصور المكتشفة: {images}\nآخر صفحة: {url[:180]}")
            except Exception:
                pass

    try:
        pages, discovered_count = await _crawl(site_url, progress)
        all_image_urls = []
        for page in pages:
            all_image_urls.extend(_image_urls(page))
        unique_images = list(dict.fromkeys(all_image_urls))[:discovered_count]
        downloaded = await _download_images(unique_images)

        client = _client()
        visual_notes: list[str] = []
        if client and types:
            for url, data, mime in downloaded:
                try:
                    note = await _analyze_image(client, data, mime, url)
                    if note:
                        visual_notes.append(note[:1200])
                except Exception as exc:
                    logger.info("Visual site analysis failed for %s: %s", url, type(exc).__name__)

        summary = _site_summary(pages)
        learned = _learn_instruction(site_url, summary, visual_notes)
        image_assets = []
        for page in pages:
            for item in page["images"]:
                for raw_img in [item.get("src", ""), *_extract_srcset(item.get("srcset", ""))]:
                    normalized = _normalize_link(page["url"], raw_img)
                    if normalized and normalized in unique_images:
                        image_assets.append({"url": normalized, "title": item.get("title", ""), "alt": item.get("alt", ""), "metadata": {"width": item.get("width", ""), "height": item.get("height", "")}})
        dedup = {x["url"]: x for x in image_assets}
        site_id = _save_site(site_url, pages, list(dedup.values()), learned, summary)

        await status.edit_text(
            "✅ <b>اكتمل تحليل الموقع والتعلّم</b>\n━━━━━━━━━━━━━━━━━━\n\n"
            f"🌐 الموقع: <code>{html.escape(site_url)}</code>\n"
            f"📄 الصفحات المحللة: <b>{len(pages)}</b>\n"
            f"🖼️ الصور المكتشفة: <b>{len(dedup)}</b>\n"
            f"🔬 الصور التي أمكن تحليلها بصرياً: <b>{len(visual_notes)}</b>\n"
            f"🧩 النماذج المكتشفة: <b>{sum(len(p['forms']) for p in pages)}</b>\n"
            f"🧠 ملف التعلم: <b>#{site_id}</b>\n\n"
            "تمت إضافة المعرفة الناتجة إلى سياق Image AI.",
            parse_mode="HTML", reply_markup=_keyboard()
        )
    except Exception as exc:
        logger.exception("Website Image AI learning failed: %s", type(exc).__name__)
        await status.edit_text(
            "❌ فشل تحليل الموقع.\n\n"
            f"السبب التقني: <code>{type(exc).__name__}</code>",
            parse_mode="HTML", reply_markup=_keyboard()
        )
    raise ApplicationHandlerStop


def register_admin_image_ai_site(app: Any, get_db: Any, owner_id: int) -> None:
    _ensure_schema()
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: site_start_callback(u, c, get_db, owner_id),
            pattern=r"^admin_image_ai_site$",
        ),
        group=-100,
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            lambda u, c: site_url_handler(u, c, get_db, owner_id),
        ),
        group=-149,
    )
