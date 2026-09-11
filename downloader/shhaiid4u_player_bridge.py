"""Bounded player/AJAX response discovery for shhaiid4u.net."""
from __future__ import annotations
import asyncio
import inspect
import re
from urllib.parse import urljoin
from downloader import shhaiid4u_resolver as base
MAX_RESPONSES=180
MAX_BODY_BYTES=768*1024
MAX_BODY_SCANS=36
MAX_NAV_TARGETS=8
SETTLE_MS=2500
RESPONSE_HINTS=("admin-ajax.php","/ajax","ajax=","player","embed","source","server","stream","media","episode","watch")
MEDIA_CONTENT_HINTS=("video/","audio/","mpegurl","dash+xml")
def _is_interesting_response(url:str,content_type:str)->bool:
    if not base._is_http(url) or base._is_ad_host(url): return False
    value=f"{url} {content_type}".casefold()
    return any(x in value for x in RESPONSE_HINTS) or any(x in content_type.casefold() for x in MEDIA_CONTENT_HINTS)
def _extract_urls(text:str,*,base_url:str)->list[str]:
    if not isinstance(text,str) or not text:return []
    values=re.findall(r"https?://[^\\\"'<>\s]+",text)+[x.replace("\\/","/") for x in re.findall(r"https?:\\/\\/[^\"'<>\s]+",text)]
    values += [urljoin(base_url,x.replace("\\/","/")) for x in re.findall(r"(?:^|[\"'`=:(,\s])((?:/|\./|\.\./)(?:[^\"'`<>\s]+))",text)]
    out=[];seen=set()
    for value in values:
        value=value.replace("\\/","/").rstrip(".,);]}")
        if value in seen or not base._is_http(value) or base._is_ad_host(value):continue
        if base._looks_like_candidate(value) or any(x in value.casefold() for x in ("player","embed","iframe","stream","source","server","admin-ajax.php")):
            seen.add(value);out.append(value)
    return out
def _score(url:str)->int:
    v=url.casefold();s=100 if base._looks_like_candidate(url) else 0
    s+=35 if ".m3u8" in v or ".mpd" in v else 0
    s+=25 if any(x in v for x in ("player","embed","iframe")) else 0
    s+=20 if any(x in v for x in ("stream","source","server")) else 0
    return s
def _rank(urls:set[str])->list[str]:
    return [u for u,_ in sorted(((u,_score(u)) for u in urls),key=lambda x:(-x[1],x[0]))[:base.MAX_CANDIDATES]]
async def _discover(url:str,*,validator)->list[str]:
    try: from playwright.async_api import async_playwright
    except Exception as exc:
        print(f"⚠️ Shhaiid4u Player Bridge: Playwright unavailable ({type(exc).__name__})",flush=True);return []
    candidates=set();queue=[url];visited=set()
    async with async_playwright() as p:
        try: browser=await p.chromium.launch(headless=True,args=["--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage","--no-first-run","--no-default-browser-check"])
        except Exception as exc:
            print(f"⚠️ Shhaiid4u Player Bridge: Chromium launch failed ({type(exc).__name__})",flush=True);return []
        try:
            context=await browser.new_context(user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",java_script_enabled=True)
            while queue and len(visited)<base.MAX_PAGES and len(candidates)<base.MAX_CANDIDATES:
                page_url=queue.pop(0)
                if page_url in visited:continue
                visited.add(page_url);page=await context.new_page();response_count=0;body_scans=0
                async def on_response(response):
                    nonlocal response_count,body_scans
                    if response_count>=MAX_RESPONSES:return
                    response_count+=1;response_url=response.url
                    try: ct=(response.headers.get("content-type") or "").casefold();cl=int(response.headers.get("content-length") or "0")
                    except Exception:ct="";cl=0
                    if not _is_interesting_response(response_url,ct):return
                    if base._looks_like_candidate(response_url):
                        try:validator(response_url)
                        except Exception:return
                        candidates.add(response_url);return
                    if body_scans>=MAX_BODY_SCANS or (cl and cl>MAX_BODY_BYTES):return
                    body_scans+=1
                    try:
                        body=await response.body()
                        if len(body)>MAX_BODY_BYTES:return
                        text=body.decode("utf-8","ignore")
                    except Exception:return
                    for extracted in _extract_urls(text,base_url=response_url):
                        try:validator(extracted)
                        except Exception:continue
                        if base._looks_like_candidate(extracted):candidates.add(extracted)
                        elif any(h in extracted.casefold() for h in ("player","embed","iframe","server","source")) and len(queue)<MAX_NAV_TARGETS:
                            if extracted not in visited and extracted not in queue:queue.append(extracted)
                page.on("response",on_response)
                try:
                    await page.goto(page_url,wait_until="domcontentloaded",timeout=base.TIMEOUT_MS);await page.wait_for_timeout(SETTLE_MS)
                    selector="a[href], button, [role='button'], [data-server], [data-player], [data-post], [data-nume], [data-type], [data-url], [onclick]"
                    try:controls=await page.locator(selector).count()
                    except Exception:controls=0
                    for index in range(min(controls,10)):
                        try:
                            control=page.locator(selector).nth(index);text=((await control.inner_text()) or "").strip().casefold()
                            attrs=await control.evaluate("el => [el.outerHTML,el.getAttribute('onclick')||'',el.getAttribute('data-server')||'',el.getAttribute('data-player')||'',el.getAttribute('data-post')||'',el.getAttribute('data-nume')||'',el.getAttribute('data-type')||'',el.getAttribute('data-url')||''].join(' ')")
                            signal=f"{text} {attrs}".casefold()
                            if not any(k in signal for k in ("server","player","embed","مشغل","سيرفر","مشاهدة","source","stream")):continue
                            try:await control.click(timeout=2500,no_wait_after=True)
                            except Exception:continue
                            await page.wait_for_timeout(900)
                        except Exception:continue
                    await page.wait_for_timeout(SETTLE_MS)
                    try:html=await page.content()
                    except Exception:html=""
                    for extracted in _extract_urls(html,base_url=page.url):
                        try:validator(extracted)
                        except Exception:continue
                        if base._looks_like_candidate(extracted):candidates.add(extracted)
                except Exception as exc:print(f"⚠️ Shhaiid4u Player Bridge: page failed ({type(exc).__name__})",flush=True)
                finally:await page.close()
            await context.close()
        finally:await browser.close()
    ranked=_rank(candidates);print(f"🎯 Shhaiid4u Player Bridge: {'found '+str(len(ranked))+' candidate(s)' if ranked else 'no player media candidate found'}",flush=True);return ranked
def resolve(url:str,*,validator)->list[str]:
    if not base.is_platform_url(url):return []
    canonical_url=base._canonical_page_url(url)
    try:validator(canonical_url)
    except Exception:return []
    try:return asyncio.run(_discover(canonical_url,validator=validator))
    except RuntimeError:
        loop=asyncio.new_event_loop()
        try:return loop.run_until_complete(_discover(canonical_url,validator=validator))
        finally:loop.close()
    except Exception as exc:print(f"⚠️ Shhaiid4u Player Bridge: failed ({type(exc).__name__})",flush=True);return []
def install(bot_module):
    original=getattr(bot_module,"extract_direct_media_urls",None)
    if not callable(original) or getattr(original,"_shhaiid4u_player_bridge",False):return
    async def wrapped(url,*args,**kwargs):
        if base.is_platform_url(url):
            try:candidates=await asyncio.to_thread(resolve,url,validator=bot_module.validate_public_http_url)
            except asyncio.CancelledError:raise
            except Exception as exc:print(f"⚠️ Shhaiid4u Player Bridge: hook failed ({type(exc).__name__})",flush=True);candidates=[]
            if candidates:return candidates
            print("🎯 Shhaiid4u Player Bridge: falling through to existing Shhaiid4u/network chain",flush=True)
        result=original(url,*args,**kwargs)
        if inspect.isawaitable(result):result=await result
        return result
    wrapped._shhaiid4u_player_bridge=True;bot_module.extract_direct_media_urls=wrapped;print("🎯 Shhaiid4u Player Bridge: ENABLED",flush=True)
