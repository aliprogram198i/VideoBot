#!/usr/bin/env python3
from __future__ import annotations
import html,re,urllib.parse,urllib.request
SOURCE="https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/";UA="Mozilla/5.0 (compatible; AliBot-KRX18-Probe/1.0)";MAX=5*1024*1024
req=urllib.request.Request(SOURCE,headers={"User-Agent":UA,"Accept":"text/html,application/xhtml+xml,*/*","Referer":SOURCE})
with urllib.request.urlopen(req,timeout=12) as r: body=r.read(MAX+1)[:MAX].decode(r.headers.get_content_charset() or "utf-8","replace")
for marker in re.finditer(r"(?:Server|سيرفر)\s*[-_ ]?\d+",html.unescape(body),re.I):
 seg=html.unescape(body)[marker.start():marker.start()+3000]
 print("MARKER:",re.sub(r"\s+"," ",seg[:500]).strip())
 attrs=re.findall(r"(?:href|src|data-server|data-player|data-url|data-href)\s*=\s*(['\"])(.*?)\1",seg,re.I|re.S)
 print("ATTR_COUNT:",len(attrs))
 for _,value in attrs[:20]:
  value=html.unescape(value).strip();
  if value.startswith("//"):value="https:"+value
  elif value.startswith("/"):value=urllib.parse.urljoin(SOURCE,value)
  print("TARGET_ATTR:",value)
