#!/usr/bin/env python3
"""
抖音数据回收脚本
用法：python3 douyin_stats.py --title "视频标题前10字" [--title "另一个"] [--pages 3]

输出：
  STATS {"title_kw":"xxx","views":123,"likes":1,"comments":0,"shares":0}
  PENDING title_kw=xxx   (定时待发布)
  FAILED title_kw=xxx error=...
exit 0: 全部命中（含PENDING），exit 1: 未找到
"""
import argparse, asyncio, json, re, sys
sys.path.insert(0, '/Users/niuone/.openclaw/skills/publisher-api-skill/scripts')
from cdp_base import connect_browser, safe_disconnect

LIST_URL = "https://creator.douyin.com/creator-micro/content/manage"

# 已发布：...编辑作品\n设置权限\n作品置顶\n删除作品\n日期\n已发布\n播放\n数\n点赞\n数\n评论\n数\n分享\n数
PUBLISHED_PATTERN = re.compile(
    r'(.+?)\n编辑作品\n设置权限\n作品置顶\n删除作品\n'
    r'\d{4}年\d{2}月\d{2}日 [\d:]+\n已发布\n'
    r'播放\n(\d[\d,.万]*)\n点赞\n(\d[\d,.万]*)\n评论\n(\d[\d,.万]*)\n分享\n(\d[\d,.万]*)',
    re.DOTALL
)

# 定时待发布：...继续编辑\n作品置顶\n删除作品\n定时发布中\n...
PENDING_PATTERN = re.compile(
    r'(.+?)\n继续编辑\n作品置顶\n删除作品\n定时发布中\n',
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
    """从标题段提取最后一段有意义的文字"""
    lines = [l.strip() for l in block.split('\n') if l.strip()]
    # 找最后一行包含中文的（过滤时长格式 00:00）
    for line in reversed(lines):
        if re.search(r'[\u4e00-\u9fff]', line) and not re.match(r'^\d{2}:\d{2}$', line):
            return line
    return lines[-1] if lines else ''

async def scrape_page(page):
    text = await page.evaluate("() => document.body.innerText")
    results = []

    for m in PUBLISHED_PATTERN.finditer(text):
        title = extract_title(m.group(1))
        results.append({
            'title':    title,
            'pending':  False,
            'views':    parse_num(m.group(2)),
            'likes':    parse_num(m.group(3)),
            'comments': parse_num(m.group(4)),
            'shares':   parse_num(m.group(5)),
        })

    for m in PENDING_PATTERN.finditer(text):
        title = extract_title(m.group(1))
        results.append({'title': title, 'pending': True,
                        'views': 0, 'likes': 0, 'comments': 0, 'shares': 0})

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
                next_btn = await page.query_selector(
                    'li.ant-pagination-next:not(.ant-pagination-disabled) button,'
                    '[aria-label="Next Page"]:not([disabled])'
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
                   'comments': r['comments'], 'shares': r['shares']}
            print(f"STATS {json.dumps(out, ensure_ascii=False)}", flush=True)
        elif kw in pending_kws:
            print(f"PENDING title_kw={kw}", flush=True)
        else:
            print(f"FAILED title_kw={kw} error=前{args.pages}页未找到", flush=True)
            has_error = True

    sys.exit(1 if has_error else 0)

if __name__ == '__main__':
    asyncio.run(main())
