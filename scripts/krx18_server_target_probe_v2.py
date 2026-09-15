#!/usr/bin/env python3
from __future__ import annotations
import html,json,re,urllib.parse,urllib.request
SOURCE="https://krx18.com/movies/84170-femdom-deadly-thigh-squeeze-her-absolute-leg-scissors/"
UA="Mozilla/5.0 (compatible; AliBot-KRX18-Probe/1.0)"; MAX=2*1024*1024; TIMEOUT=10
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
p=urllib.parse.urlparse(SOURCE);m=re.search(r"/movies/(\d+)-([^/]+)/?$",p.path,re.I);mid=m.group(1);slug=m.group(2)
for label,term in (("slug",slug.replace("-"," ")[:120]),("id",mid)):
 q=urllib.parse.quote(term); u=f"https://krx18.com/wp-json/wp/v2/search?search={q}&per_page=10&_fields=id,type,subtype,url,title";print(f"SEARCH_{label}={u}")
 try:st,_,body=get(u,"application/json"); data=json.loads(body);print(f"SEARCH_{label}_STATUS={st};COUNT={len(data) if isinstance(data,list) else 0}")
 except Exception as e:print(f"SEARCH_{label}_ERROR={type(e).__name__}:{e}");continue
 for item in data if isinstance(data,list) else []:
  if not isinstance(item,dict):continue
  iid=str(item.get("id") or ""); path=urllib.parse.urlparse(str(item.get("url") or "")).path.rstrip("/").casefold()
  if not ((iid==mid) or (slug.casefold() in path)):continue
  print(f"MATCH_ID={iid};TYPE={item.get('type')};SUBTYPE={item.get('subtype')};URL={item.get('url')}")
  rb=str(item.get("subtype") or item.get("type") or "posts");rb="posts" if rb=="post" else rb;du=f"https://krx18.com/wp-json/wp/v2/{rb}/{iid}?_fields=id,title,content,link";st,fu,b=get(du,"application/json");d=json.loads(b);title=clean(str(d.get("title",{}).get("rendered","")));ts=targets(str(d.get("content",{}).get("rendered","")));print(f"DETAIL_STATUS={st};TITLE={title};TARGETS={len(ts)}")
  for i,t in enumerate(ts,1):
   print(f"SERVER_TARGET_{i}={t}")
   try:
    ss,sf,sb=get(t,"text/html,application/xhtml+xml,*/*");tm=re.search(r"<title[^>]*>(.*?)</title>",sb,re.I|re.S);med=[x.rstrip(".,;)]}") for x in URL_RE.findall(html.unescape(sb).replace("\\/","/")) if re.search(r"(?:m3u8|mpd|mp4|m4v|webm|mov|mkv|avi|ts)",x,re.I)];print(f"TARGET_{i}_STATUS={ss};FINAL={sf};TITLE={clean(tm.group(1)) if tm else ''};MEDIA={len(med)}")
    for j,x in enumerate(med[:5],1):print(f"TARGET_{i}_MEDIA_{j}={x}")
   except Exception as e:print(f"TARGET_{i}_ERROR={type(e).__name__}:{e}")
