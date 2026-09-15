from pathlib import Path
p=Path('downloader/browser_media_resolver.py')
s=p.read_text()
anchor='async def _discover_navigation_targets(page, base_url: str, max_targets: int) -> list[str]:\n'
helper=r'''async def _discover_krx18_source_targets(page, base_url: str, max_targets: int) -> list[str]:
    """Extract public KRX18 server/player targets exposed by markup or inline JS."""
    if not _is_krx18_host(base_url):
        return []
    targets = {}
    try:
        rows = await page.locator("a[href], iframe[src], embed[src], [data-server], [data-player], [data-download], [data-url], [data-href]").evaluate_all("""els => els.map(el => ({href: el.href || el.src || el.getAttribute('data-url') || el.getAttribute('data-href') || '', text: (el.innerText || el.textContent || '').trim(), attr: Array.from(el.attributes || []).map(a => a.name + '=' + a.value).join(' '), onclick: el.getAttribute('onclick') || ''}))""")
    except Exception:
        rows=[]
    for row in rows or []:
        if not isinstance(row,dict): continue
        text=' '.join(str(row.get(k) or '') for k in ('text','attr','onclick'))
        values=[str(row.get('href') or '').strip()]
        values += re.findall(r"https?://[^\s\"'<>]+", text, flags=re.I)
        for candidate in values:
            candidate=html.unescape(candidate).replace('\\/','/').rstrip('\\.,;)]}')
            if not _is_http_url(candidate) or candidate == base_url: continue
            score=_navigation_score(text,candidate)
            host=(urlparse(candidate).hostname or '').lower()
            if host and host != 'krx18.com': score += 12
            if any(t in text.casefold() for t in ('server','player','watch','stream','سيرفر','مشاهدة','مشغل')): score += 24
            if score > 0: targets[candidate]=max(targets.get(candidate,0),score)
    try:
        scripts=await page.locator('script').all_text_contents()
    except Exception:
        scripts=[]
    for script in scripts or []:
        for candidate in re.findall(r"https?://[^\s\"'<>]+", html.unescape(script).replace('\\/','/'), flags=re.I):
            candidate=candidate.rstrip('\\.,;)]}')
            if not _is_http_url(candidate) or candidate == base_url: continue
            host=(urlparse(candidate).hostname or '').lower()
            if not host or host == 'krx18.com': continue
            context=sum(5 for word in ('server','player','watch','stream','source','download','سيرفر','مشاهدة') if word.casefold() in script.casefold())
            if context: targets[candidate]=max(targets.get(candidate,0),16+context)
    ordered=sorted(targets.items(), key=lambda item:(-item[1],item[0]))
    result=[u for u,_ in ordered[:max_targets]]
    if result:
        LOG.info('KRX18 site adapter: discovered %d public server/player target(s)',len(result))
        print(f'KRX18 site adapter: discovered {len(result)} public server/player target(s)',flush=True)
    return result


'''
if anchor not in s: raise SystemExit('anchor not found')
s=s.replace(anchor,helper+anchor,1)
old="""                targets = await _discover_navigation_targets(page, page_url, max_nav_targets)\n                for target in targets:\n                    if target not in visited_pages and target not in queue:\n                        queue.append(target)\n"""
new="""                if _is_krx18_host(url):\n                    krx_targets = await _discover_krx18_source_targets(page, page_url, max_nav_targets)\n                    for target in krx_targets:\n                        if target not in visited_pages and target not in queue:\n                            queue.append(target)\n                targets = await _discover_navigation_targets(page, page_url, max_nav_targets)\n                for target in targets:\n                    if target not in visited_pages and target not in queue:\n                        queue.append(target)\n"""
if old not in s: raise SystemExit('call anchor not found')
s=s.replace(old,new,1)
p.write_text(s)
