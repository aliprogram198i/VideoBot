#!/usr/bin/env python3
from __future__ import annotations
import html,re,urllib.parse,urllib.request
SOURCE="https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/"; UA="Mozilla/5.0 (compatible; AliBot-KRX18-Probe/1.0)"; MAX=5*1024*1024; TIMEOUT=12
URL_RE=re.compile(r"https?://[^\s\"'<>\\]+",re.I)
def get(u,a="*/*"):
 r=urllib.request.Request(u,headers={"User-Agent":UA,"Accept":a,"Referer":SOURCE})
 with urllib.request.urlopen(r,timeout=TIMEOUT) as x:return x.status,x.geturl(),x.read(MAX+1)[:MAX].decode(x.headers.get_content_charset() or "utf-8","replace")
def clean(s):return re.sub(r"\s+"," ",html.unescape(re.sub(r"<[^>]+>"," ",s or ""))).strip()
def extract(src):
 mark=re.compile(r"(?:server|سيرفر)\s*[-_ ]?\d+",re.I)
 for m in mark.finditer(src):
  print(f"SERVER_MARKER={clean(src[m.start():m.start()+120])}")
  out=[]
  for raw in URL_RE.findall(src[m.start():m.start()+2200]):
   u=html.unescape(raw).rstrip(".,;)]}");p=urllib.parse.urlparse(u)
   if p.scheme in {"http","https"} and p.hostname and not re.search(r"\.(?:m3u8|mpd|mp4|m4v|webm|mov|mkv|avi|ts)(?:$|[?#])",u,re.I) and u not in out:out.append(u)
  if out:return out[:3]
 return []
st,final,body=get(SOURCE,"text/html,application/xhtml+xml,*/*");print(f"SOURCE_STATUS={st};FINAL={final};BYTES={len(body)}");print(f"SOURCE_TITLE={clean(re.search(r'<title[^>]*>(.*?)</title>',body,re.I|re.S).group(1)) if re.search(r'<title[^>]*>(.*?)</title>',body,re.I|re.S) else ''}")
targets=extract(body);print(f"SERVER_TARGET_COUNT={len(targets)}")
for i,t in enumerate(targets,1):
 print(f"SERVER_TARGET_{i}={t}")
 try:
  ss,sf,sb=get(t,"text/html,application/xhtml+xml,*/*");tm=re.search(r"<title[^>]*>(.*?)</title>",sb,re.I|re.S);print(f"TARGET_{i}_STATUS={ss};FINAL={sf};TITLE={clean(tm.group(1)) if tm else ''};BYTES={len(sb)}")
  media=[]
  for v in URL_RE.findall(html.unescape(sb).replace("\\/","/")):
   v=v.rstrip(".,;)]}")
   if re.search(r"(?:m3u8|mpd|mp4|m4v|webm|mov|mkv|avi|ts)",v,re.I) and v not in media:media.append(v)
  print(f"TARGET_{i}_MEDIA_COUNT={len(media)}")
  for j,v in enumerate(media[:5],1):print(f"TARGET_{i}_MEDIA_{j}={v}")
 except Exception as e:print(f"TARGET_{i}_ERROR={type(e).__name__}:{e}")
