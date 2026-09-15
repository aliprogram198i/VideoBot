#!/usr/bin/env python3
from __future__ import annotations
import html,json,re,urllib.parse,urllib.request
SOURCE="https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/"; UA="Mozilla/5.0 (compatible; AliBot-KRX18-Probe/1.0)"; MAX=2*1024*1024; TIMEOUT=10
URL_RE=re.compile(r"https?://[^\s\"'<>\\]+",re.I)
def get(u,a="*/*"):
 r=urllib.request.Request(u,headers={"User-Agent":UA,"Accept":a,"Referer":SOURCE})
 with urllib.request.urlopen(r,timeout=TIMEOUT) as x:return x.status,x.geturl(),x.read(MAX+1)[:MAX].decode(x.headers.get_content_charset() or "utf-8","replace")
def clean(s):return re.sub(r"\s+"," ",html.unescape(re.sub(r"<[^>]+>"," ",s or ""))).strip()
def targets(content):
 src=html.unescape(content); mark=re.compile(r"(?:server|سيرفر)\s*[-_ ]?\d+",re.I)
 for m in mark.finditer(src):
  out=[]
  for raw in URL_RE.findall(src[m.start():m.start()+1800]):
   u=raw.rstrip(".,;)]}"); p=urllib.parse.urlparse(u)
   if p.scheme in {"http","https"} and p.hostname and not re.search(r"\.(?:m3u8|mpd|mp4|m4v|webm|mov|mkv|avi|ts)(?:$|[?#])",u,re.I) and u not in out:out.append(u)
  if out:return out[:3]
 return []
def main():
 mid="84170"; print(f"SOURCE={SOURCE}")
 types_url="https://krx18.com/wp-json/wp/v2/types?_fields=slug,rest_base"
 try:st,_,b=get(types_url,"application/json"); data=json.loads(b); print(f"TYPES_STATUS={st}")
 except Exception as e:print(f"TYPES_ERROR={type(e).__name__}:{e}");return 2
 bases=[]
 for key,val in (data.items() if isinstance(data,dict) else []):
  if not isinstance(val,dict):continue
  rb=str(val.get("rest_base") or "").strip().strip("/"); sl=str(val.get("slug") or "").casefold()
  if rb and any(t in sl or t in rb.casefold() for t in ("movie","film","video")):bases.append(rb)
 print(f"MOVIE_REST_BASES={bases}")
 bases=list(dict.fromkeys(bases))+["posts"]
 for rb in bases[:6]:
  u=f"https://krx18.com/wp-json/wp/v2/{rb}/{mid}?_fields=id,title,content,link"
  try:st,fu,b=get(u,"application/json"); print(f"TYPE={rb};STATUS={st};FINAL={fu}");
  except Exception as e:print(f"TYPE={rb};ERROR={type(e).__name__}:{e}");continue
  try:d=json.loads(b)
  except Exception as e:print(f"TYPE={rb};JSON_ERROR={type(e).__name__}");continue
  if not isinstance(d,dict) or "content" not in d:continue
  title=clean(str(d.get("title",{}).get("rendered",""))); ts=targets(str(d.get("content",{}).get("rendered",""))); print(f"TYPE={rb};TITLE={title};TARGETS={len(ts)}")
  for i,t in enumerate(ts,1):
   print(f"SERVER_TARGET_{i}={t}")
   try:
    ss,sf,sb=get(t,"text/html,application/xhtml+xml,*/*");tm=re.search(r"<title[^>]*>(.*?)</title>",sb,re.I|re.S);print(f"TARGET_{i}_STATUS={ss};FINAL={sf};TITLE={clean(tm.group(1)) if tm else ''}")
    media=[]
    for v in URL_RE.findall(html.unescape(sb).replace("\\/","/")):
     v=v.rstrip(".,;)]}")
     if re.search(r"(?:m3u8|mpd|mp4|m4v|webm|mov|mkv|avi|ts)",v,re.I) and v not in media:media.append(v)
    print(f"TARGET_{i}_MEDIA={len(media)}")
    for j,v in enumerate(media[:5],1):print(f"TARGET_{i}_MEDIA_{j}={v}")
   except Exception as e:print(f"TARGET_{i}_ERROR={type(e).__name__}:{e}")
 return 0
if __name__=="__main__":raise SystemExit(main())
