#!/usr/bin/env python3
"""
抖音数据回收脚本（video-card DOM 提取版）
用法：python3 douyin_stats.py --title "视频标题前10字" [--title "另一个"] [--pages 3]

输出：
  STATS {"title_kw":"xxx","views":123,"likes":1,"comments":0,"shares":0}
  PENDING title_kw=xxx   (定时待发布)
  FAILED title_kw=xxx error=...
exit 0: 全部命中（含PENDING），exit 1: 未找到
"""
import argparse, asyncio, json, re, sys, time
sys.path.insert(0, '/Users/zhengweirong/.openclaw/skills/publisher-api-skill/scripts')
from cdp_base import connect_browser, safe_disconnect, load_and_collect_json

LIST_URL = "https://creator.douyin.com/creator-micro/content/manage"


async def list_all(max_videos=80):
    """批量：加载 manage 页（拦截初始 work_list）+ page.evaluate fetch 翻页（work_list 无签名，cookie 即可）。"""
    pw, browser = await connect_browser()
    videos, seen = [], set()

    def add(al):
        now = time.time()
        for it in (al or []):
            aid = it.get("aweme_id")
            if not aid or aid in seen:
                continue
            seen.add(aid)
            st = it.get("statistics") or {}
            ct = it.get("create_time") or 0
            videos.append({
                "id":        aid,
                "title":     it.get("desc") or "",
                "views":     st.get("play_count", 0),
                "likes":     st.get("digg_count", 0),
                "comments":  st.get("comment_count", 0),
                "shares":    st.get("share_count", 0),
                "favorites": st.get("collect_count", 0),
                "pending":   bool(ct) and ct > now,
                "ts":        ct or 0,
            })

    try:
        page, bodies = await load_and_collect_json(
            browser, LIST_URL, "work_list", scrolls=1, settle=6.0)
        for b in bodies:
            add(b.get("aweme_list"))
        cursor = 0
        for _ in range(6):
            if len(videos) >= max_videos:
                break
            try:
                res = await page.evaluate("""async (cursor) => {
                    const url = `/janus/douyin/creator/pc/work_list?status=0&count=20&max_cursor=${cursor}&scene=star_atlas&device_platform=android&aid=1128`;
                    const r = await fetch(url, { credentials: 'include' });
                    return await r.json();
                }""", cursor)
            except Exception:
                break
            al = (res or {}).get("aweme_list") or []
            if not al:
                break
            add(al)
            if not res.get("has_more"):
                break
            nc = res.get("max_cursor")
            if not nc or nc == cursor:
                break
            cursor = nc
        try: await page.close()
        except Exception: pass
    finally:
        await safe_disconnect(pw, browser)
    return videos

def parse_num(s):
    s = s.strip().replace(',', '')
    if '万' in s:
        return int(float(s.replace('万', '')) * 10000)
    try:
        return int(s)
    except Exception:
        return 0

def extract_title(text_before_buttons):
    """从视频卡片文本中提取标题"""
    # 去掉按钮文字
    text = text_before_buttons
    text = text.replace('编辑作品', '').replace('设置权限', '').replace('作品置顶', '')
    text = text.replace('继续编辑', '').replace('修改定时', '')
    text = text.replace('已智能生成章节要点，确认并添加，可以使视频结构更清晰', '')
    # 取#号之前的文字
    parts = text.split('#')
    title_part = parts[0].strip() if parts else text
    # 去掉时长前缀如 01:21
    title_part = re.sub(r'^\d{2}:\d{2}', '', title_part).strip()
    # 标题通常是第一句，到第一个句号/问号/叹号为止（允许空格在标题中）
    m = re.match(r'(.{3,40}?)[。！？，]', title_part)
    if m:
        return m.group(1).strip()
    # 备用：取前35字
    return title_part[:35].strip()

async def scrape_page(page):
    """从 video-card DOM 元素提取视频列表"""
    cards = await page.evaluate("""function() {
        var cards = document.querySelectorAll('[class*="video-card"]');
        return Array.from(cards).map(function(card) {
            return card.textContent || '';
        }).filter(function(t) { return t.length > 80 && t.indexOf('删除作品') >= 0; });
    }""")
    
    results = []
    seen = set()
    
    for text in cards:
        # 已发布：...删除作品YYYY年MM月DD日 HH:MM已发布播放N点赞N评论N分享N（无空格）
        m_pub = re.search(
            r'删除作品(\d{4}年\d{2}月\d{2}日 [\d:]+)已发布'
            r'播放(\d[\d,.万]*)点赞(\d[\d,.万]*)评论(\d[\d,.万]*)分享(\d[\d,.万]*)',
            text
        )
        if m_pub:
            before = text[:m_pub.start()]
            title = extract_title(before)
            
            if title and title not in seen:
                seen.add(title)
                results.append({
                    'title': title,
                    'pending': False,
                    'views': parse_num(m_pub.group(2)),
                    'likes': parse_num(m_pub.group(3)),
                    'comments': parse_num(m_pub.group(4)),
                    'shares': parse_num(m_pub.group(5)),
                })
            continue
        
        # 定时待发布：...删除作品定时发布中定时: ...
        m_pen = re.search(r'删除作品定时发布中定时: (\d{4}年\d{2}月\d{2}日 [\d:]+)', text)
        if m_pen:
            before = text[:m_pen.start()]
            title = extract_title(before)
            
            if title and title not in seen:
                seen.add(title)
                results.append({
                    'title': title,
                    'pending': True,
                    'views': 0, 'likes': 0, 'comments': 0, 'shares': 0,
                })
    
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
            {"platform": "douyin", "count": len(videos), "videos": videos},
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
