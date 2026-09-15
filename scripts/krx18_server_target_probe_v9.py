#!/usr/bin/env python3
from __future__ import annotations
import html,re,urllib.request
U="https://play.playkrx18.site/play/6a420848fcbc5cec4fcc6fb4";UA="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 Chrome/139.0.0.0 Mobile Safari/537.36"
r=urllib.request.Request(U,headers={"User-Agent":UA,"Accept":"text/html,application/xhtml+xml,*/*","Referer":"https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/"})
with urllib.request.urlopen(r,timeout=12) as x:body=x.read(5*1024*1024).decode(x.headers.get_content_charset() or "utf-8","replace")
text=html.unescape(body).replace('\\/','/')
for needle in (".mp4","mp4","m3u8","sources","source","videojs","jwplayer","file:","src:","manifest","playlist"):
 print(f"=== {needle} ===")
 c=0
 for m in re.finditer(re.escape(needle),text,re.I):
  s=re.sub(r"\s+"," ",text[max(0,m.start()-500):m.start()+1200])
  print(s[:1800]);c+=1
  if c>=6:break
