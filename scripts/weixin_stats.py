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
import argparse, asyncio, json, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from cdp_base import connect_browser, safe_disconnect

LIST_URL = "https://channels.weixin.qq.com/platform/post/list"

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
    parser.add_argument('--title', action='append', required=True)
    parser.add_argument('--scroll', type=int, default=3, help='滚动加载次数')
    args = parser.parse_args()

    kws = [t[:15] for t in args.title]
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
