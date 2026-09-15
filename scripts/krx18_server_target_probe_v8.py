#!/usr/bin/env python3
from __future__ import annotations
import html,re,urllib.request
UA="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 Chrome/139.0.0.0 Mobile Safari/537.36";MAX=5*1024*1024
TARGETS=["https://play.playkrx18.site/play/6a420848fcbc5cec4fcc6fb4","https://mov18plus.cloud/?v=TKmOBXVvu"]
for i,u in enumerate(TARGETS,1):
 req=urllib.request.Request(u,headers={"User-Agent":UA,"Accept":"text/html,application/xhtml+xml,*/*","Referer":"https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/"})
 print(f"PLAYER_{i}_URL={u}")
 try:
  with urllib.request.urlopen(req,timeout=12) as r:
   body=r.read(MAX+1)[:MAX].decode(r.headers.get_content_charset() or "utf-8","replace"); print(f"PLAYER_{i}_STATUS={r.status};FINAL={r.geturl()};BYTES={len(body)};CTYPE={r.headers.get('Content-Type','')}")
   tm=re.search(r"<title[^>]*>(.*?)</title>",body,re.I|re.S);print(f"PLAYER_{i}_TITLE={re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',tm.group(1) if tm else ''))).strip()}")
   media=[]
   for v in re.findall(r"https?://[^\s\"'<>\\]+",html.unescape(body).replace('\\/','/'),re.I):
    v=v.rstrip(".,;)]}")
    if re.search(r"(?:m3u8|mpd|mp4|m4v|webm|mov|mkv|avi|ts)",v,re.I) and v not in media:media.append(v)
   print(f"PLAYER_{i}_MEDIA_COUNT={len(media)}")
   for j,v in enumerate(media[:10],1):print(f"PLAYER_{i}_MEDIA_{j}={v}")
   for needle in ("iframe","video","source","m3u8","mp4","filemoon","stream"):
    print(f"PLAYER_{i}_{needle.upper()}_HITS={len(re.findall(needle,body,re.I))}")
 except Exception as e: print(f"PLAYER_{i}_ERROR={type(e).__name__}:{e}")
