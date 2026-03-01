#!/usr/bin/env python3
"""
快手视频上传脚本（CDP 连接 OpenClaw 浏览器）
实机调试验证版本 - 2026-03-01

用法：python3 ks_upload.py --video /path/video.mp4 --desc "内容+#话题"
      --cover34 /path/cover-3x4.png --dtime "2026-03-02 17:00:00"
      --dedup-kw "用于查重的关键词（可选）"
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from cdp_base import connect_browser, safe_disconnect, new_tab, log, exit_published, exit_need_login, exit_failed

MANAGE_URL = "https://cp.kuaishou.com/article/manage/video"
PUBLISH_URL = "https://cp.kuaishou.com/article/publish/video"


async def check_login_and_duplicate(page, dedup_kw: str) -> dict:
    result = await page.evaluate("""
    async (kw) => {
      await new Promise(r => setTimeout(r, 2000));
      const text = document.body.innerText;
      if (location.href.includes('login') || text.includes('立即登录'))
        return { loggedIn: false };
      const core = s => s.replace(/[^\\u4e00-\\u9fff\\w]/g, '');
      return { loggedIn: true, duplicate: kw ? core(text).includes(core(kw)) : false };
    }
    """, dedup_kw)
    return result


async def upload_video(page, video_path: str):
    log(f"[快手] 上传视频: {video_path}")
    inputs = await page.query_selector_all('input[type=file]')
    target = None
    for inp in inputs:
        if 'video' in (await inp.get_attribute('accept') or ''):
            target = inp
            break
    if not target and inputs:
        target = inputs[0]
    if not target:
        exit_failed("快手：找不到视频上传 input")
    await target.set_input_files(video_path)
    log("[快手] 视频文件已选择")


async def wait_upload_done(page, timeout=300):
    log("[快手] 等待上传完成...")
    for i in range(timeout // 5):
        await asyncio.sleep(5)
        try:
            text = await page.evaluate("() => document.body.innerText")
            if '发布' in text and ('描述' in text or '封面' in text):
                log(f"[快手] 上传完成（{(i+1)*5}s）")
                return True
        except:
            pass
    return False


async def pause_video(page):
    await page.evaluate("() => { const v = document.querySelector('video'); if (v) v.pause(); }")


async def fill_desc(page, desc: str):
    log("[快手] 填写描述")
    result = await page.evaluate("""
    (desc) => {
      const ed = [...document.querySelectorAll('[contenteditable=true]')]
        .filter(e => e.offsetHeight > 40 && e.offsetWidth > 200)[0];
      if (!ed) return 'not found';
      ed.click(); ed.focus();
      ed.innerHTML = '';
      document.execCommand('insertText', false, desc);
      return 'ok: ' + ed.textContent.slice(0, 20);
    }
    """, desc)
    log(f"[快手] 描述: {result}")
    await asyncio.sleep(1)


async def set_cover(page, cover_path: str):
    """
    快手封面正确流程（实机调试验证）：
    1. 点封面黑块（cover-full-editor）打开弹窗
    2. 点"上传封面" tab
    3. expect_file_chooser + 点"上传图片"按钮
    4. 点确认
    """
    if not cover_path or not os.path.exists(cover_path):
        log("[快手] 无封面，跳过")
        return
    log(f"[快手] 上传封面: {cover_path}")

    # 滚到封面区
    await page.evaluate("""
    () => {
      const el = document.querySelector('[class*=high-cover-editor]');
      if (el) el.scrollIntoView({block: 'center'});
    }
    """)
    await asyncio.sleep(0.5)

    # Step1: 点封面黑块打开弹窗
    await page.click('[class*=cover-full-editor]')
    await asyncio.sleep(1.5)

    # Step2: 点"上传封面" tab
    await page.evaluate("""
    () => {
      const tab = [...document.querySelectorAll('*')]
        .find(e => e.textContent.trim() === '上传封面' && e.offsetHeight > 0 && e.offsetHeight < 60);
      if (tab) tab.click();
    }
    """)
    await asyncio.sleep(1)

    # Step3: expect_file_chooser + 点"上传图片"按钮
    try:
        async with page.expect_file_chooser(timeout=5000) as fc_info:
            await page.evaluate("""
            () => {
              const btn = [...document.querySelectorAll('button')]
                .find(e => e.textContent.trim() === '上传图片' && e.offsetHeight > 0);
              if (btn) btn.click();
            }
            """)
        fc = await fc_info.value
        await fc.set_files(cover_path)
        log("[快手] 封面已上传")
        await asyncio.sleep(3)
    except Exception as e:
        log(f"[快手] 封面上传失败: {e}")
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)
        return

    # Step4: 确认
    await page.evaluate("""
    () => {
      const btn = [...document.querySelectorAll('button')]
        .find(e => e.textContent.trim() === '确认' && e.offsetHeight > 0);
      if (btn) btn.click();
    }
    """)
    await asyncio.sleep(2)
    log("[快手] 封面设置完成")


async def set_schedule(page, dtime: str):
    """
    快手定时：Ant Design DateTimePicker
    - 点"定时发布" radio
    - 打开 picker → 点日历日期 → 点时/分/秒 li → 点确定
    """
    dt = datetime.strptime(dtime, "%Y-%m-%d %H:%M:%S")
    day = str(dt.day)
    hh  = f"{dt.hour:02d}"
    mm  = f"{dt.minute:02d}"

    log(f"[快手] 设置定时: {dtime}")

    # 点"定时发布" radio
    await page.evaluate("""
    () => {
      const labels = [...document.querySelectorAll('.ant-radio-wrapper')]
        .filter(e => e.textContent.includes('定时') && e.offsetHeight > 0);
      if (labels.length) { labels[0].click(); return; }
      const radio = [...document.querySelectorAll('input[type=radio]')]
        .find(e => e.value === '2');
      if (radio) radio.click();
    }
    """)
    await asyncio.sleep(1)

    # 打开 picker
    await page.evaluate("""
    () => {
      const inp = document.querySelector('input[placeholder="选择日期时间"]');
      if (inp) inp.click();
    }
    """)
    await asyncio.sleep(1)

    # 点目标日期（排除 disabled）
    r = await page.evaluate(f"""
    () => {{
      const panel = document.querySelector('.ant-picker-dropdown');
      if (!panel) return 'no panel';
      const td = [...panel.querySelectorAll('td')]
        .find(e => e.textContent.trim() === '{day}'
          && e.offsetHeight > 0 && !e.className.includes('disabled'));
      if (td) {{ td.click(); return 'ok'; }}
      return 'not found';
    }}
    """)
    log(f"[快手] 日期: {r}")
    await asyncio.sleep(0.3)

    # 点时/分/秒 li
    for ul_idx, val in [(0, hh), (1, mm), (2, "00")]:
        await page.evaluate(f"""
        () => {{
          const ul = document.querySelectorAll(
            '.ant-picker-dropdown ul.ant-picker-time-panel-column')[{ul_idx}];
          const li = ul && [...ul.querySelectorAll('li')]
            .find(e => e.textContent.trim() === '{val}');
          if (li) {{ li.scrollIntoView({{block: 'center'}}); li.click(); }}
        }}
        """)
        await asyncio.sleep(0.2)

    # 确定
    await page.evaluate("""
    () => {
      const btn = document.querySelector('.ant-picker-ok button') ||
        [...document.querySelectorAll('button')]
          .find(e => e.textContent.trim() === '确定');
      if (btn) btn.click();
    }
    """)
    await asyncio.sleep(1)

    val = await page.evaluate("""
    () => {
      const inp = document.querySelector('input[placeholder="选择日期时间"]');
      return inp ? inp.value : 'not found';
    }
    """)
    log(f"[快手] 定时验证: {val}")


async def publish(page) -> bool:
    log("[快手] 点击发布")
    # 快手发布按钮是 DIV（class 含 button-primary），不是 <button>
    await page.evaluate("""
    () => {
      const btn = [...document.querySelectorAll('div')]
        .find(e => e.textContent.trim() === '发布'
          && e.className.includes('button-primary') && e.offsetHeight > 0);
      if (btn) { btn.click(); return; }
      // 备用：任意含发布文字的可见元素
      const fallback = [...document.querySelectorAll('*')]
        .find(e => e.textContent.trim() === '发布' && e.offsetHeight > 0 && e.offsetHeight < 80);
      if (fallback) fallback.click();
    }
    """)
    await asyncio.sleep(8)
    url = await page.evaluate("() => location.href")
    text = await page.evaluate("() => document.body.innerText.slice(0, 200)")
    return 'manage' in url or '发布成功' in text or 'publish/video' not in url


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--desc", default="")
    parser.add_argument("--cover34", default="")
    parser.add_argument("--dtime", default="")
    parser.add_argument("--dedup-kw", default="")
    args = parser.parse_args()

    dedup_kw = args.dedup_kw or (args.desc.split('\n')[0] if args.desc else "")

    pw, browser = await connect_browser()
    try:
        page = await new_tab(browser, MANAGE_URL)
        result = await check_login_and_duplicate(page, dedup_kw)
        if not result.get('loggedIn'):
            exit_need_login("快手")
        if result.get('duplicate'):
            log("[快手] 视频已存在，标记为 published")
            exit_published(args.dtime)

        await page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        await upload_video(page, args.video)
        ok = await wait_upload_done(page)
        if not ok:
            exit_failed("快手：视频上传超时")

        await pause_video(page)
        await asyncio.sleep(1)
        await fill_desc(page, args.desc)
        await set_cover(page, args.cover34)

        if args.dtime:
            await set_schedule(page, args.dtime)

        ok = await publish(page)
        if ok:
            exit_published(args.dtime)
        else:
            exit_failed("快手：发布后未检测到成功状态")

    except Exception as e:
        import traceback
        traceback.print_exc()
        exit_failed(str(e))
    finally:
        await safe_disconnect(pw, browser)


if __name__ == "__main__":
    asyncio.run(main())
