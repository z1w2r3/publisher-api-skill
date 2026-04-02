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
sys.path.insert(0, '/Users/zhengweirong/.openclaw/skills/publisher-api-skill/scripts')
from cdp_base import connect_browser, safe_disconnect

LIST_URL = "https://cp.kuaishou.com/article/manage/video?status=1"

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
