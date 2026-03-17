#!/usr/bin/env python3
"""
B站数据回收脚本（通过创作者后台标题搜索）
用法：python3 bili_stats_by_title.py --title "视频标题前15字" [--title "另一个"] [--pages 3]

输出：
  STATS {"title_kw":"xxx","views":123,"likes":1,"comments":0,"shares":0,"coins":0,"danmaku":0,"favorites":0}
  PENDING title_kw=xxx
  FAILED title_kw=xxx error=...
exit 0: 全部命中（含PENDING），exit 1: 未找到
"""
import argparse, asyncio, json, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from cdp_base import connect_browser, safe_disconnect

LIST_URL = "https://member.bilibili.com/platform/upload-manager/article"

# 匹配已发布视频数据
# 格式：标题
# 时间
# 播放 · 点赞 · 评论 · 收藏
# 数据
PUBLISHED_PATTERN = re.compile(
    r'(.+?)\n'
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\n'
    r'(?:原创\n)?'
    r'([\d万,.]+)\s*·\s*([\d万,.]+)\s*·\s*([\d万,.]+)\s*·\s*([\d万,.]+)',
    re.DOTALL
)

# 定时待发布
PENDING_PATTERN = re.compile(
    r'(.+?)\n'
    r'定时发布:\s*\d{4}-\d{2}-\d{2}',
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
    for line in lines:
        if re.search(r'[\u4e00-\u9fff]', line):
            return line
    return lines[0] if lines else ''

async def scrape_page(page):
    text = await page.evaluate("() => document.body.innerText")
    results = []
    
    for m in PUBLISHED_PATTERN.finditer(text):
        title = extract_title(m.group(1))
        results.append({
            'title':     title,
            'pending':   False,
            'views':     parse_num(m.group(3)),
            'likes':     parse_num(m.group(4)),
            'comments':  parse_num(m.group(5)),
            'favorites': parse_num(m.group(6)),
            'shares':    0,
            'coins':     0,
            'danmaku':   0,
        })
    
    for m in PENDING_PATTERN.finditer(text):
        title = extract_title(m.group(1))
        results.append({'title': title, 'pending': True,
                        'views': 0, 'likes': 0, 'comments': 0,
                        'favorites': 0, 'shares': 0, 'coins': 0, 'danmaku': 0})
    return results

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--title', action='append', required=True)
    parser.add_argument('--pages', type=int, default=3)
    args = parser.parse_args()

    kws = [t[:15] for t in args.title]
    matched = {}
    pending_kws = set()

    pw, browser = await connect_browser()
    try:
        page = await browser.contexts[0].new_page()
        await page.goto(LIST_URL, wait_until='domcontentloaded')
        await asyncio.sleep(3)
        
        for page_num in range(1, args.pages + 1):
            rows = await scrape_page(page)
            for row in rows:
                for kw in kws:
                    if kw not in matched and kw not in pending_kws and kw in row['title']:
                        if row['pending']:
                            pending_kws.add(kw)
                        else:
                            matched[kw] = row
            if len(matched) + len(pending_kws) == len(kws):
                break
            if page_num < args.pages:
                # 找下一页按钮
                next_btn = await page.query_selector(
                    '.next-page:not(.disabled), .pagination-next:not(.disabled), button:has-text("下一页")'
                )
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
            out = {'title_kw': kw, 'views': r['views'], 'likes': r['likes'],
                   'comments': r['comments'], 'favorites': r['favorites'],
                   'shares': r['shares'], 'coins': r['coins'], 'danmaku': r['danmaku']}
            print(f"STATS {json.dumps(out, ensure_ascii=False)}", flush=True)
        elif kw in pending_kws:
            print(f"PENDING title_kw={kw}", flush=True)
        else:
            print(f"FAILED title_kw={kw} error=前{args.pages}页未找到", flush=True)
            has_error = True

    sys.exit(1 if has_error else 0)

if __name__ == '__main__':
    asyncio.run(main())
