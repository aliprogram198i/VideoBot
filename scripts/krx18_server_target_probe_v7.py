#!/usr/bin/env python3
from __future__ import annotations
import json,urllib.request
BASE="https://krx18.com/wp-json/dooplayer/v2/84170/movie/";UA="Mozilla/5.0 (compatible; AliBot-KRX18-Probe/1.0)"
for num in ("1","2"):
 u=BASE+num; req=urllib.request.Request(u,headers={"User-Agent":UA,"Accept":"application/json","Referer":"https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/"})
 print("TARGET_API="+u)
 try:
  with urllib.request.urlopen(req,timeout=10) as r:
   body=r.read(2*1024*1024).decode(r.headers.get_content_charset() or "utf-8","replace");print(f"STATUS={r.status};FINAL={r.geturl()}")
   try:
    data=json.loads(body); print("JSON="+json.dumps(data,ensure_ascii=False,separators=(",",":"))[:8000])
   except Exception: print("BODY="+body[:8000])
 except Exception as e: print(f"ERROR={type(e).__name__}:{e}")
