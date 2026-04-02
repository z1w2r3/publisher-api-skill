#!/usr/bin/env python3
"""
B站视频列表获取脚本 - 从创作者后台获取已发布视频的 bvid
用法：python3 bili_list.py [--pages 3]

输出：
  {"bvid":"BV1xx","title":"视频标题"}
"""
import argparse, asyncio, json, re, sys
sys.path.insert(0, '/Users/zhengweirong/.openclaw/skills/publisher-api-skill/scripts')
from cdp_base import connect_browser, safe_disconnect

LIST_URL = "https://member.bilibili.com/platform/upload-manager/article"

async def scrape_page(page):
    """从页面提取视频列表"""
    text = await page.evaluate("() => document.body.innerText")
    results = []
    
    # 尝试提取视频卡片信息
    # B站创作者后台结构：每个视频卡片包含标题、bvid链接等
    cards = await page.query_selector_all('.video-card, .upload-item, [data-bvid]')
    
    for card in cards:
        try:
            # 获取 bvid
            bvid_attr = await card.get_attribute('data-bvid')
            if not bvid_attr:
                # 尝试从链接提取
                link = await card.query_selector('a[href*="BV"]')
                if link:
                    href = await link.get_attribute('href')
                    match = re.search(r'(BV\w+)', href)
                    if match:
                        bvid_attr = match.group(1)
            
            # 获取标题
            title_el = await card.query_selector('.title, .video-title, h3, .content-title')
            title = await title_el.inner_text() if title_el else ""
            
            if bvid_attr and title:
                results.append({'bvid': bvid_attr, 'title': title.strip()})
        except Exception:
            pass
    
    return results

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pages', type=int, default=3)
    args = parser.parse_args()

    pw, browser = await connect_browser()
    try:
        page = await browser.contexts[0].new_page()
        await page.goto(LIST_URL, wait_until='domcontentloaded')
        await asyncio.sleep(3)
        
        all_videos = []
        
        for page_num in range(1, args.pages + 1):
            # 等待视频列表加载
            await page.wait_for_selector('.video-card, .upload-item, [data-bvid]', timeout=10000)
            
            # 提取当前页视频
            videos = await scrape_page(page)
            all_videos.extend(videos)
            
            # 尝试翻页
            if page_num < args.pages:
                next_btn = await page.query_selector('.next-page:not(.disabled), .pagination-next:not(.disabled)')
                if next_btn:
                    await next_btn.click()
                    await asyncio.sleep(2)
                else:
                    break
        
        # 输出结果
        for v in all_videos:
            print(json.dumps(v, ensure_ascii=False), flush=True)
            
    finally:
        await safe_disconnect(pw, browser)

if __name__ == '__main__':
    asyncio.run(main())
