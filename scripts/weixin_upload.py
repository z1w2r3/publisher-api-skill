#!/usr/bin/env python3
"""
视频号视频上传脚本（CDP 连接 OpenClaw 浏览器）
注意：视频号使用 Shadow DOM + iframe，文件上传走 pierce locator

用法：python3 weixin_upload.py --video /path/video.mp4 --short-title "短标题(6-16字)"
      --desc "描述+#话题" --cover34 /path/cover-3x4.png --dtime "2026-03-02 12:00:00"
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from cdp_base import connect_browser, new_tab, log, exit_published, exit_need_login, exit_failed

LIST_URL = "https://channels.weixin.qq.com/platform/post/list"
CREATE_URL = "https://channels.weixin.qq.com/platform/post/create"


async def check_login_and_duplicate(page, short_title: str) -> dict:
    """视频号 shadow DOM，用 innerText 读页面文字检查"""
    result = await page.evaluate("""
    async (title) => {
      await new Promise(r => setTimeout(r, 2000));
      const text = document.body.innerText;
      // 检查是否登录
      if (text.includes('登录') && !text.includes('视频')) return { loggedIn: false };
      const core = s => s.replace(/[^\\u4e00-\\u9fff\\w]/g, '');
      // shadow DOM 中找内容
      let allText = text;
      for (const host of document.querySelectorAll('*')) {
        if (host.shadowRoot) allText += host.shadowRoot.textContent || '';
      }
      const duplicate = core(allText).includes(core(title));
      return { loggedIn: true, duplicate };
    }
    """, short_title)
    return result


async def upload_video(page, video_path: str):
    log(f"[视频号] 上传视频: {video_path}")
    # 在 Shadow DOM 中找 file input
    # Playwright pierce 选择器可以穿透 shadow DOM
    try:
        inp = page.locator('input[type=file]').first
        await inp.set_input_files(video_path, timeout=10000)
        log("[视频号] 视频文件已选择（pierce）")
        return
    except Exception as e:
        log(f"[视频号] pierce 上传失败: {e}，尝试 evaluate shadow DOM")

    # 备选：evaluate 找 shadow input 并用 dispatchEvent
    js_result = await page.evaluate(f"""
    async () => {{
      for (const host of document.querySelectorAll('*')) {{
        if (host.shadowRoot) {{
          const input = host.shadowRoot.querySelector('input[type="file"]');
          if (input) {{
            // 不能直接调 input.click()，改用 DataTransfer
            const dt = new DataTransfer();
            // 无法在 JS 中直接创建本地文件，返回坐标
            const p = input.parentElement || input;
            const r = p.getBoundingClientRect();
            return {{
              screenX: window.screenX + Math.round(r.x + r.width/2),
              screenY: window.screenY + (window.outerHeight - window.innerHeight) + Math.round(r.y + r.height/2)
            }};
          }}
        }}
      }}
      return null;
    }}
    """)
    if js_result:
        log(f"[视频号] 获取到上传坐标: {js_result}，请确认 OpenClaw 浏览器窗口在前台")
        exit_failed("视频号：需要 peekaboo 物理点击上传，请使用 auto-browser-skill 处理视频号")
    else:
        exit_failed("视频号：找不到视频上传 input")


async def wait_upload_done(page, timeout=300):
    log("[视频号] 等待上传完成...")
    for i in range(timeout // 5):
        await asyncio.sleep(5)
        try:
            result = await page.evaluate("""
            () => {
              // 检查是否有视频上传成功的标志
              for (const host of document.querySelectorAll('*')) {
                if (host.shadowRoot) {
                  const text = host.shadowRoot.textContent || '';
                  if (text.includes('上传成功') || text.includes('封面')) return true;
                }
              }
              return document.body.innerText.includes('描述') || document.body.innerText.includes('发表');
            }
            """)
            if result:
                log("[视频号] 上传完成")
                return True
        except:
            pass
    return False


async def set_cover(page, cover34_path: str):
    if not cover34_path or not os.path.exists(cover34_path):
        log("[视频号] 无封面，跳过")
        return
    log(f"[视频号] 设置封面: {cover34_path}")

    # 点击封面设置
    await page.evaluate("""
    () => {
      // 普通 DOM 先找
      const el = [...document.querySelectorAll('*')]
        .find(e => (e.textContent.trim().includes('封面') || e.textContent.trim() === '选择封面')
          && e.offsetHeight > 0 && e.offsetHeight < 100 && e.children.length < 3);
      if (el) { el.click(); return 'dom'; }
      // Shadow DOM
      for (const host of document.querySelectorAll('*')) {
        if (host.shadowRoot) {
          const btn = [...host.shadowRoot.querySelectorAll('*')]
            .find(e => e.textContent.trim().includes('封面') && e.offsetHeight > 0 && e.offsetHeight < 100);
          if (btn) { btn.click(); return 'shadow'; }
        }
      }
      return null;
    }
    """)
    await asyncio.sleep(2)

    # 上传封面 file input
    try:
        inputs = page.locator('input[type=file]')
        count = await inputs.count()
        for i in range(count - 1, -1, -1):
            inp = inputs.nth(i)
            accept = await inp.get_attribute('accept') or ''
            if 'image' in accept or 'png' in accept or not accept:
                await inp.set_input_files(cover34_path)
                log("[视频号] 封面已上传")
                break
    except Exception as e:
        log(f"[视频号] 封面上传失败: {e}")
    await asyncio.sleep(3)

    # 确认
    await page.evaluate("""
    () => {
      const btn = [...document.querySelectorAll('button')]
        .find(e => e.textContent.includes('完成') || e.textContent.includes('确认'));
      if (btn) { btn.click(); return true; }
      for (const host of document.querySelectorAll('*')) {
        if (host.shadowRoot) {
          const b = [...host.shadowRoot.querySelectorAll('button')]
            .find(e => e.textContent.includes('完成') || e.textContent.includes('确认'));
          if (b) { b.click(); return true; }
        }
      }
      return false;
    }
    """)
    await asyncio.sleep(2)


async def fill_desc(page, desc: str):
    log("[视频号] 填写描述")
    # 描述在 iframe 内
    frames = page.frames
    for frame in frames:
        if 'contenteditable' in await frame.content():
            await frame.evaluate(f"""
            () => {{
              const ed = document.querySelector('[contenteditable=true]');
              if (!ed) return false;
              ed.focus();
              document.execCommand('selectAll', false, null);
              document.execCommand('insertText', false, {repr(desc)});
              return true;
            }}
            """)
            log("[视频号] 描述已填（iframe）")
            await asyncio.sleep(1)
            return

    # 备选：普通 DOM contenteditable
    await page.evaluate(f"""
    () => {{
      const ed = [...document.querySelectorAll('[contenteditable=true]')]
        .find(e => e.offsetHeight > 40);
      if (!ed) return false;
      ed.focus();
      document.execCommand('selectAll', false, null);
      document.execCommand('insertText', false, {repr(desc)});
      return true;
    }}
    """)
    await asyncio.sleep(1)


async def fill_short_title(page, short_title: str):
    log(f"[视频号] 填写短标题: {short_title}")
    # 清理特殊符号，确保 6-16 字
    import re
    clean = re.sub(r'[^\u4e00-\u9fff\w\s]', '', short_title)[:16]
    if len(clean) < 6:
        clean = clean + '　' * (6 - len(clean))

    await page.evaluate(f"""
    () => {{
      const inp = [...document.querySelectorAll('input[type=text], textarea')]
        .find(e => (e.placeholder?.includes('标题') || e.placeholder?.includes('title'))
          && e.offsetHeight > 0);
      if (!inp) return false;
      const nativeSet = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      nativeSet.call(inp, {repr(clean)});
      inp.dispatchEvent(new Event('input', {{bubbles: true}}));
      return true;
    }}
    """)
    await asyncio.sleep(1)


async def set_original(page):
    """勾选原创声明"""
    log("[视频号] 勾选原创声明")
    await page.evaluate("""
    () => {
      // Shadow DOM checkbox
      for (const host of document.querySelectorAll('*')) {
        if (host.shadowRoot) {
          const cb = host.shadowRoot.querySelector('input[type=checkbox]');
          if (cb && !cb.checked) { cb.click(); return 'shadow'; }
        }
      }
      // 普通
      const cb = document.querySelector('input[type=checkbox]');
      if (cb && !cb.checked) { cb.click(); return 'dom'; }
      return null;
    }
    """)
    await asyncio.sleep(1)


async def set_schedule(page, dtime: str):
    log(f"[视频号] 设置定时: {dtime}")
    from datetime import datetime
    dt = datetime.strptime(dtime, "%Y-%m-%d %H:%M:%S")

    # 点定时发布 radio
    await page.evaluate("""
    () => {
      // shadow DOM radio
      for (const host of document.querySelectorAll('*')) {
        if (host.shadowRoot) {
          const radios = host.shadowRoot.querySelectorAll('input[type=radio]');
          for (const r of radios) {
            const label = r.nextElementSibling?.textContent || r.parentElement?.textContent || '';
            if (label.includes('定时')) { r.click(); return 'shadow'; }
          }
        }
      }
      // 普通 DOM
      const el = [...document.querySelectorAll('*')]
        .find(e => e.textContent.trim() === '定时发送' && e.offsetHeight > 0 && e.offsetHeight < 60);
      if (el) { el.click(); return 'dom'; }
      return null;
    }
    """)
    await asyncio.sleep(2)

    # 填时间
    date_str = dt.strftime("%Y-%m-%d %H:%M")
    await page.evaluate(f"""
    () => {{
      // Shadow DOM input
      for (const host of document.querySelectorAll('*')) {{
        if (host.shadowRoot) {{
          const inp = host.shadowRoot.querySelector('input[placeholder*="时间"], input[type=datetime-local], input[type=text]');
          if (inp) {{
            inp.focus();
            inp.value = {repr(date_str)};
            inp.dispatchEvent(new Event('input', {{bubbles:true}}));
            inp.dispatchEvent(new Event('change', {{bubbles:true}}));
            return 'shadow input';
          }}
        }}
      }}
      return null;
    }}
    """)
    await asyncio.sleep(1)


async def publish(page) -> bool:
    log("[视频号] 点击发表")
    await page.evaluate("""
    () => {
      const btn = [...document.querySelectorAll('button,div')]
        .find(e => e.textContent.trim() === '发表' && e.offsetHeight > 0 && e.offsetHeight < 80);
      if (btn) { btn.click(); return true; }
      for (const host of document.querySelectorAll('*')) {
        if (host.shadowRoot) {
          const b = [...host.shadowRoot.querySelectorAll('button')]
            .find(e => e.textContent.trim() === '发表');
          if (b) { b.click(); return 'shadow'; }
        }
      }
      return false;
    }
    """)
    await asyncio.sleep(5)
    text = await page.evaluate("""
    () => {
      let t = document.body.innerText;
      for (const host of document.querySelectorAll('*')) {
        if (host.shadowRoot) t += host.shadowRoot.textContent || '';
      }
      return t;
    }
    """)
    return '发表成功' in text or '发布成功' in text or '视频管理' in text


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--short-title", required=True)
    parser.add_argument("--desc", default="")
    parser.add_argument("--cover34", default="")
    parser.add_argument("--dtime", default="")
    args = parser.parse_args()

    pw, browser = await connect_browser()
    try:
        page = await new_tab(browser, LIST_URL)
        result = await check_login_and_duplicate(page, args.short_title)
        if not result.get('loggedIn'):
            exit_need_login("视频号")
        if result.get('duplicate'):
            log("[视频号] 视频已存在，标记为 published")
            exit_published(args.dtime)

        await page.goto(CREATE_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        await upload_video(page, args.video)
        ok = await wait_upload_done(page)
        if not ok:
            exit_failed("视频号：视频上传超时")

        # 顺序：封面 → 描述 → 短标题 → 原创 → 定时 → 发表
        await set_cover(page, args.cover34)
        await fill_desc(page, args.desc)
        await fill_short_title(page, args.short_title)
        await set_original(page)

        if args.dtime:
            await set_schedule(page, args.dtime)

        ok = await publish(page)
        if ok:
            exit_published(args.dtime)
        else:
            exit_failed("视频号：发表后未检测到成功状态")

    except Exception as e:
        exit_failed(str(e))
    finally:
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
