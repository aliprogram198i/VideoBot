#!/usr/bin/env python3
from __future__ import annotations
import html,re,urllib.request
SOURCE="https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/";UA="Mozilla/5.0 (compatible; AliBot-KRX18-Probe/1.0)";MAX=5*1024*1024
req=urllib.request.Request(SOURCE,headers={"User-Agent":UA,"Accept":"text/html,application/xhtml+xml,*/*","Referer":SOURCE})
with urllib.request.urlopen(req,timeout=12) as r:body=r.read(MAX+1)[:MAX].decode(r.headers.get_content_charset() or "utf-8","replace")
text=html.unescape(body)
for m in re.finditer(r"<li[^>]+id=['\"]player-option-[^>]+>.*?</li>",text,re.I|re.S):
 print("PLAYER_OPTION:",re.sub(r"\s+"," ",m.group(0))[:1800])
for needle in ("playkrx18.site","mov18plus.cloud","player-option","dooplay_player_option","admin-ajax.php","data-nume","data-post"):
 print(f"\n=== {needle} ===")
 count=0
 for m in re.finditer(re.escape(needle),text,re.I):
  print(re.sub(r"\s+"," ",text[max(0,m.start()-700):m.start()+1400])[:2100]); count+=1
  if count>=5:break
