#!/usr/bin/env python3
"""
视频号数据回收脚本（通过 frame[1] 读取内容）
用法：python3 weixin_stats.py --title "视频标题前10字" [--title "另一个"] [--scroll 3]

输出：
  STATS {"title_kw":"xxx","views":123,"likes":1,"comments":0,"favorites":0,"shares":0}
  PENDING title_kw=xxx
  FAILED title_kw=xxx error=...
exit 0: 全部命中（含PENDING），exit 1: 未找到
"""
import argparse, asyncio, json, re, sys
sys.path.insert(0, '/Users/zhengweirong/.openclaw/skills/publisher-api-skill/scripts')
from cdp_base import connect_browser, safe_disconnect, load_and_collect_json, find_dict_list

LIST_URL = "https://channels.weixin.qq.com/platform/post/list"


async def list_all(max_videos=80):
    """批量：加载列表页，拦截 post_list JSON；内容在 frame[1]，列表用「下一页」按钮分页
    （顶部多为定时未发，已发布的在后面页，必须翻够）。返回全部视频 stats。"""
    pw, browser = await connect_browser()
    videos, seen, bodies = [], set(), []
    try:
        page = await browser.contexts[0].new_page()
        try: await page.bring_to_front()
        except Exception: pass

        async def _grab(resp):
            try:
                if "post/post_list" in resp.url and "json" in resp.headers.get("content-type", ""):
                    bodies.append(await resp.json())
            except Exception:
                pass

        page.on("response", lambda r: asyncio.create_task(_grab(r)))
        await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=30000)
        try: await page.bring_to_front()
        except Exception: pass
        await asyncio.sleep(6)

        def _count():
            return sum(len((b.get("data") or {}).get("list") or []) for b in bodies)

        for _ in range(8):
            if _count() >= max_videos:
                break
            try:
                frame = page.frames[1] if len(page.frames) > 1 else page.frames[0]
                clicked = await frame.evaluate(
                    "() => {"
                    "  const els = [...document.querySelectorAll('button,a,[role=button],.weui-desktop-btn,.weui-desktop-pagination__nav *')];"
                    "  const nx = els.find(e => (e.innerText||'').trim() === '下一页');"
                    "  if (!nx) return 'no-btn';"
                    "  const cls = nx.className || '';"
                    "  if (nx.disabled || cls.includes('disabled') || nx.getAttribute('aria-disabled')==='true') return 'disabled';"
                    "  nx.click(); return 'clicked';"
                    "}")
            except Exception:
                clicked = "err"
            if clicked != "clicked":
                break
            await asyncio.sleep(2.5)

        for b in bodies:
            lst = ((b.get("data") or {}).get("list")) or find_dict_list(b, ["objectId", "readCount"])
            for it in (lst or []):
                oid = it.get("objectId")
                if not oid or oid in seen:
                    continue
                seen.add(oid)
                desc = it.get("desc") or {}
                title = ((desc.get("description") or desc.get("shortTitle") or "")
                         if isinstance(desc, dict) else "")
                videos.append({
                    "id":        oid,
                    "title":     title,
                    "views":     it.get("readCount", 0),
                    "likes":     it.get("likeCount", 0),
                    "comments":  it.get("commentCount", 0),
                    "shares":    it.get("forwardCount", 0),
                    "favorites": it.get("favCount", 0),
                    "pending":   False,
                    "ts":        it.get("createTime") or 0,
                })
        try: await page.close()
        except Exception: pass
    finally:
        await safe_disconnect(pw, browser)
    return videos

# 已发布：标题\n日期\n已声明原创\n播放\n点赞\n评论\n收藏\n分享\n置顶
PUBLISHED_PATTERN = re.compile(
    r'(.+?)\n\d{4}年\d{2}月\d{2}日 [\d:]+\n(?:已声明原创\n)?'
    r'(\d[\d,.万]*)\n(\d[\d,.万]*)\n(\d[\d,.万]*)\n(\d[\d,.万]*)\n(\d[\d,.万]*)\n置顶',
    re.DOTALL
)

# 定时待发布：标题\n将于日期发表
PENDING_PATTERN = re.compile(
    r'(.+?)\n将于\d{4}年\d{2}月\d{2}日 [\d:]+发表\n',
    re.DOTALL
)

def parse_num(s):
    s = s.strip().replace(',', '')
    if '万' in s:
        return int(float(s.replace('万', '')) * 10000)
    try:
        return int(s)
    except Exception:
        return 0

def extract_title(block):
    lines = [l.strip() for l in block.split('\n') if l.strip()]
    for line in reversed(lines):
        if re.search(r'[\u4e00-\u9fff]', line):
            return line
    return lines[-1] if lines else ''

async def scrape_frame(frame):
    text = await frame.evaluate("() => document.body.innerText")
    results = []

    for m in PUBLISHED_PATTERN.finditer(text):
        title = extract_title(m.group(1))
        results.append({
            'title':     title,
            'pending':   False,
            'views':     parse_num(m.group(2)),
            'likes':     parse_num(m.group(3)),
            'comments':  parse_num(m.group(4)),
            'favorites': parse_num(m.group(5)),
            'shares':    parse_num(m.group(6)),
        })

    for m in PENDING_PATTERN.finditer(text):
        title = extract_title(m.group(1))
        results.append({'title': title, 'pending': True,
                        'views': 0, 'likes': 0, 'comments': 0,
                        'favorites': 0, 'shares': 0})
    return results

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--title', action='append', default=None)
    parser.add_argument('--brief', help='brief.json path; read platform-specific title')
    parser.add_argument('--platform', help='platform key for --brief (weixin-channels)')
    parser.add_argument('--scroll', type=int, default=3, help='滚动加载次数')
    parser.add_argument('--list', action='store_true', help='批量列出全部视频+stat（一次加载）')
    parser.add_argument('--max', type=int, default=80)
    args = parser.parse_args()

    if args.list:
        videos = await list_all(args.max)
        print("STATS_BATCH " + json.dumps(
            {"platform": "weixin-channels", "count": len(videos), "videos": videos},
            ensure_ascii=False), flush=True)
        sys.exit(0)

    titles = list(args.title or [])
    if args.brief and args.platform:
        try:
            with open(args.brief, encoding='utf-8') as _bf:
                _b = json.load(_bf)
            _pf = _b.get(args.platform) or {}
            _t = (((_pf.get('desc') or '').split('\n')[0].strip() or None)
                  or _pf.get('short_title') or _pf.get('title'))
            if _t:
                titles.append(_t)
        except Exception as _e:
            print('FAILED error=brief read failed: %s' % _e, flush=True)
    if not titles:
        print('FAILED error=need --title or --brief+--platform', flush=True)
        sys.exit(1)

    kws = [t[:15] for t in titles]
    matched = {}
    pending_kws = set()

    pw, browser = await connect_browser()
    try:
        # 找或打开列表页
        page = next((p for p in browser.contexts[0].pages
                     if 'channels.weixin' in p.url), None)
        if not page:
            page = await browser.contexts[0].new_page()
            await page.goto(LIST_URL, wait_until='domcontentloaded')
        await asyncio.sleep(3)

        # frame[1] 是真正内容
        frame = page.frames[1] if len(page.frames) > 1 else page.frames[0]

        for scroll_n in range(args.scroll + 1):
            rows = await scrape_frame(frame)
            for row in rows:
                for kw in kws:
                    if kw not in matched and kw not in pending_kws and kw in row['title']:
                        if row['pending']:
                            pending_kws.add(kw)
                        else:
                            matched[kw] = row
            if len(matched) + len(pending_kws) == len(kws):
                break
            if scroll_n < args.scroll:
                await frame.evaluate("() => window.scrollBy(0, 1200)")
                await asyncio.sleep(2)

    finally:
        await safe_disconnect(pw, browser)

    has_error = False
    for kw in kws:
        if kw in matched:
            r = matched[kw]
            out = {'title_kw': kw, 'views': r['views'], 'likes': r['likes'],
                   'comments': r['comments'], 'favorites': r['favorites'],
                   'shares': r['shares']}
            print(f"STATS {json.dumps(out, ensure_ascii=False)}", flush=True)
        elif kw in pending_kws:
            print(f"PENDING title_kw={kw}", flush=True)
        else:
            print(f"FAILED title_kw={kw} error=滚动{args.scroll}次未找到", flush=True)
            has_error = True

    sys.exit(1 if has_error else 0)

if __name__ == '__main__':
    asyncio.run(main())
