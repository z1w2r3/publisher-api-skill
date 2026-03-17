#!/usr/bin/env python3
"""
B站数据回收脚本（通过标题搜索）
用法：python3 bili_search_stats.py --cookie /path/to/account.json --title "视频标题前15字" [--title "另一个"]

输出：
  STATS {"title_kw":"xxx","bvid":"BV1xx","views":123,...}
  PENDING title_kw=xxx
  FAILED title_kw=xxx error=...
exit 0: 全部成功，exit 1: 至少一个失败
"""
import argparse, asyncio, json, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from cdp_base import connect_browser, safe_disconnect

COOKIE_PATH = os.path.expanduser("~/.openclaw/cookies/bilibili_uploader/account.json")
LIST_URL = "https://member.bilibili.com/platform/upload-manager/article"

# 匹配已发布视频：标题...BVxxxxx...播放量
PUBLISHED_PATTERN = re.compile(
    r'([^\n]+?)\n[^\n]*?(BV\w+)[^\n]*?\n\s*(\d[\d,.万]*)\s*\n',
    re.DOTALL
)

# 定时发布中
PENDING_PATTERN = re.compile(
    r'([^\n]+?)\n[^\n]*定时发布中',
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
    
    # 找已发布
    for m in PUBLISHED_PATTERN.finditer(text):
        title = m.group(1).replace('\n', ' ').strip()[:50]
        bvid = m.group(2)
        views = parse_num(m.group(3))
        results.append({
            'title': title,
            'bvid': bvid,
            'views': views,
            'pending': False
        })
    
    # 找待发布
    for m in PENDING_PATTERN.finditer(text):
        title = m.group(1).replace('\n', ' ').strip()[:50]
        results.append({'title': title, 'pending': True})
    
    return results

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cookie', default=COOKIE_PATH)
    parser.add_argument('--title', action='append', required=True)
    args = parser.parse_args()

    kws = [t[:15] for t in args.title]
    matched = {}
    pending_kws = set()

    pw, browser = await connect_browser()
    try:
        page = await browser.contexts[0].new_page()
        await page.goto(LIST_URL, wait_until='domcontentloaded')
        await asyncio.sleep(3)
        
        # 滚动加载更多
        for _ in range(5):
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
            await page.evaluate("() => window.scrollBy(0, 800)")
            await asyncio.sleep(2)
        
        await page.close()
    finally:
        await safe_disconnect(pw, browser)

    # 获取详细数据
    import urllib.request
    
    def load_cookie(path):
        d = json.load(open(path))
        return "; ".join(f'{c["name"]}={c["value"]}' for c in d["cookie_info"]["cookies"])
    
    def fetch_stat(bvid, cookie_str):
        url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        req = urllib.request.Request(url, headers={
            "Cookie": cookie_str,
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/",
        })
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.load(resp)
        code = data.get("code", 0)
        if code != 0:
            raise ValueError(f"API error code={code}")
        s = data["data"]["stat"]
        return {
            "bvid": bvid,
            "views": s.get("view", 0),
            "likes": s.get("like", 0),
            "comments": s.get("reply", 0),
            "shares": s.get("share", 0),
            "coins": s.get("coin", 0),
            "danmaku": s.get("danmaku", 0),
            "favorites": s.get("favorite", 0),
        }

    has_error = False
    cookie_str = load_cookie(args.cookie)
    
    for kw in kws:
        if kw in matched:
            r = matched[kw]
            try:
                stat = fetch_stat(r['bvid'], cookie_str)
                stat['title_kw'] = kw
                print(f"STATS {json.dumps(stat, ensure_ascii=False)}", flush=True)
            except Exception as e:
                print(f"FAILED title_kw={kw} error={e}", flush=True)
                has_error = True
        elif kw in pending_kws:
            print(f"PENDING title_kw={kw}", flush=True)
        else:
            print(f"FAILED title_kw={kw} error=未找到匹配视频", flush=True)
            has_error = True

    sys.exit(1 if has_error else 0)

if __name__ == '__main__':
    asyncio.run(main())
