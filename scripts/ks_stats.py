#!/usr/bin/env python3
"""
快手数据回收脚本
用法：python3 ks_stats.py --title "视频标题前10字" [--title "另一个"] [--pages 3]

输出：
  STATS {"title_kw":"xxx","views":123,"likes":1,"comments":0}
  PENDING title_kw=xxx
  FAILED title_kw=xxx error=...
exit 0: 全部命中，exit 1: 至少一个未命中
"""
import argparse, asyncio, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp_base import connect_browser, safe_disconnect, load_and_collect_json, find_dict_list

LIST_URL = "https://cp.kuaishou.com/article/manage/video?status=1"


async def list_all(max_videos=80):
    """批量：加载一次列表页，拦截 photo/list JSON，返回全部视频 stats。"""
    pw, browser = await connect_browser()
    videos, seen = [], set()
    try:
        page, bodies = await load_and_collect_json(browser, LIST_URL, "photo/list", scrolls=4)
        for b in bodies:
            for it in find_dict_list(b, ["workId", "playCount"]):
                wid = it.get("workId")
                if not wid or wid in seen:
                    continue
                seen.add(wid)
                videos.append({
                    "id":        wid,
                    "title":     it.get("title") or "",
                    "views":     it.get("playCount", 0),
                    "likes":     it.get("likeCount", 0),
                    "comments":  it.get("commentCount", 0),
                    "shares":    0,
                    "favorites": 0,
                    "pending":   it.get("publishStatus") not in (4, 5),
                    "ts":        int((it.get("uploadTime") or 0) / 1000),
                })
        try: await page.close()
        except Exception: pass
    finally:
        await safe_disconnect(pw, browser)
    return videos

# 待发布视频
PENDING_PATTERN = re.compile(
    r'\d{2}:\d{2}\n(.+?)\n待发布\n定时发布:',
    re.DOTALL
)

PATTERN = re.compile(
    r'\d{2}:\d{2}\n(.+?)\n已发布\n(\d{4}-\d{2}-\d{2}\s+[\d:]+)\n\s*([\d万,.]+)\n\s*([\d万,.]+)\n\s*([\d万,.]+)',
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

async def scrape_page(page):
    text = await page.evaluate("() => document.body.innerText")
    results = []
    for m in PATTERN.finditer(text):
        title = m.group(1).replace('\n', ' ').strip()
        results.append({
            'title':    title,
            'views':    parse_num(m.group(3)),
            'likes':    parse_num(m.group(4)),
            'comments': parse_num(m.group(5)),
        })
    # 待发布
    for m in PENDING_PATTERN.finditer(text):
        title = m.group(1).replace('\n', ' ').strip()
        results.append({'title': title, 'pending': True,
                        'views': 0, 'likes': 0, 'comments': 0})
    return results

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--title', action='append', default=None)
    parser.add_argument('--brief', help='brief.json path; read platform-specific title')
    parser.add_argument('--platform', help='platform key for --brief (douyin/kuaishou/weixin-channels)')
    parser.add_argument('--pages', type=int, default=3)
    parser.add_argument('--list', action='store_true', help='批量列出全部视频+stat（一次加载）')
    parser.add_argument('--max', type=int, default=80)
    args = parser.parse_args()

    if args.list:
        videos = await list_all(args.max)
        print("STATS_BATCH " + json.dumps(
            {"platform": "kuaishou", "count": len(videos), "videos": videos},
            ensure_ascii=False), flush=True)
        sys.exit(0)

    titles = list(args.title or [])
    if args.brief and args.platform:
        try:
            with open(args.brief, encoding='utf-8') as _bf:
                _b = json.load(_bf)
            _pf = _b.get(args.platform) or {}
            _t = (_pf.get('title') or _pf.get('short_title')
                  or ((_pf.get('desc') or '').split('\n')[0].strip() or None))
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
        page = await browser.contexts[0].new_page()
        await page.goto(LIST_URL, wait_until='domcontentloaded')
        await asyncio.sleep(2)
        await page.evaluate("""
        () => {
          const t = [...document.querySelectorAll('.el-tabs__item')]
                    .find(e => e.innerText.trim() === '已发布');
          if (t) t.click();
        }
        """)
        await asyncio.sleep(1.5)

        for page_num in range(1, args.pages + 1):
            rows = await scrape_page(page)
            for row in rows:
                for kw in kws:
                    if kw not in matched and kw not in pending_kws and kw in row['title']:
                        if row.get('pending'):
                            pending_kws.add(kw)
                        else:
                            matched[kw] = row
            if len(matched) + len(pending_kws) == len(kws):
                break
            if page_num < args.pages:
                next_btn = await page.query_selector(
                    '.el-pagination .btn-next:not([disabled])')
                if not next_btn:
                    break
                await next_btn.click()
                await asyncio.sleep(2)

        await page.close()
    finally:
        await safe_disconnect(pw, browser)

    has_error = False
    for kw in kws:
        if kw in matched:
            r = matched[kw]
            out = {'title_kw': kw, 'views': r['views'],
                   'likes': r['likes'], 'comments': r['comments']}
            print(f"STATS {json.dumps(out, ensure_ascii=False)}", flush=True)
        elif kw in pending_kws:
            print(f"PENDING title_kw={kw}", flush=True)
        else:
            print(f"FAILED title_kw={kw} error=前{args.pages}页未找到匹配视频",
                  flush=True)
            has_error = True

    sys.exit(1 if has_error else 0)

if __name__ == '__main__':
    asyncio.run(main())
