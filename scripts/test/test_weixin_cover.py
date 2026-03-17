#!/usr/bin/env python3
"""
测试视频号视频上传 + 封面上传流程
使用已有 list 页 tab，导航到 create 页完整测试
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from cdp_base import connect_browser, safe_disconnect, log

LIST_URL = "https://channels.weixin.qq.com/platform/post/list"
CREATE_URL = "https://channels.weixin.qq.com/platform/post/create"
VIDEO_PATH = os.path.expanduser("~/Media/staging/0307-zeroclaw-autonomous-ai-infra/2026-03-portrait.mp4")
COVER_PATH = os.path.expanduser("~/Media/staging/0307-zeroclaw-autonomous-ai-infra/cover-3x4.png")


async def wujie_eval(page, code):
    """在 wujie shadow root 内执行 JS
    root = html 元素（HTMLHtmlElement），用 root.querySelector('body') 取 body
    body = root.querySelector('body') 快捷变量已注入
    """
    return await page.evaluate(f"""
    () => {{
      try {{
        const sr = document.querySelector('wujie-app') && document.querySelector('wujie-app').shadowRoot;
        const root = sr ? sr.querySelector('html') : document.documentElement;
        if (!root) return 'no-root';
        const body = root.querySelector('body') || document.body;
        {code}
      }} catch(e) {{ return 'err:' + e.message; }}
    }}
    """)


async def wait_for_wujie(page, timeout=15):
    """等待 wujie-app shadow root 初始化完成"""
    for i in range(timeout * 2):
        ready = await page.evaluate("""
        () => {
          const el = document.querySelector('wujie-app');
          if (!el || !el.shadowRoot) return false;
          const html = el.shadowRoot.querySelector('html');
          if (!html) return false;
          const body = html.querySelector('body');
          return !!(body && body.innerText && body.innerText.length > 10);
        }
        """)
        if ready:
            log(f"  wujie 初始化完成 ({(i+1)*0.5:.1f}s)")
            return True
        await asyncio.sleep(0.5)
    return False


async def main():
    assert os.path.exists(VIDEO_PATH), f"视频不存在: {VIDEO_PATH}"
    assert os.path.exists(COVER_PATH), f"封面不存在: {COVER_PATH}"
    log(f"视频: {VIDEO_PATH}")
    log(f"封面: {COVER_PATH}")

    pw, browser = await connect_browser()
    try:
        # ── 找视频号 list 页 ─────────────────────────────────────
        page = None
        for ctx in browser.contexts:
            for p in ctx.pages:
                if 'channels.weixin.qq.com' in p.url:
                    page = p
                    log(f"找到视频号页面: {p.url}")
                    break
            if page:
                break

        if not page:
            log("❌ 未找到视频号页面，请先确认浏览器中有视频号 tab")
            return

        # ── 导航到 create 页 ─────────────────────────────────────
        log("=== Step 1: 导航到发表页 ===")
        # 自动接受弹窗（可能出现"离开页面"确认框）
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        await page.goto(CREATE_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)  # 给 wujie 基础加载时间
        log("等待 wujie 初始化...")
        if not await wait_for_wujie(page, timeout=30):
            # 打印调试信息
            debug = await page.evaluate("""
            () => {
              const el = document.querySelector('wujie-app');
              if (!el) return 'no wujie-app element';
              if (!el.shadowRoot) return 'no shadowRoot';
              const html = el.shadowRoot.querySelector('html');
              if (!html) return 'no html in shadowRoot';
              return 'body len=' + (html.body ? html.body.innerText.length : 'no body');
            }
            """)
            log(f"wujie debug: {debug}")
            log("❌ wujie 初始化超时，继续尝试...")
            # 不 return，继续执行

        # ── 检查登录 ─────────────────────────────────────────────
        log("=== Step 2: 检查登录状态 ===")
        logged_in = await wujie_eval(page, """
        const text = body.innerText;
        const loginKw = ['扫描二维码','扫码登录','微信扫一扫','立即登录','手机号登录'];
        return !loginKw.some(kw => text.includes(kw));
        """)
        if not logged_in or logged_in == 'no-root':
            log(f"❌ 未登录或页面异常: {logged_in}")
            return
        log("✅ 已登录")
        await asyncio.sleep(2)

        # ── 上传视频 ─────────────────────────────────────────────
        log("=== Step 3: 上传视频 ===")
        try:
            inp = page.locator('input[type=file]').first
            await inp.set_input_files(VIDEO_PATH, timeout=10000)
            log("✅ 视频文件已选择")
        except Exception as e:
            log(f"❌ 视频上传失败: {e}")
            return

        # ── 等待上传完成 ─────────────────────────────────────────
        log("=== Step 4: 等待上传完成 ===")
        for i in range(60):
            await asyncio.sleep(5)
            result = await wujie_eval(page, """
            const text = (() => {
              let t = body.innerText;
              for (const h of root.querySelectorAll('*'))
                if (h.shadowRoot) t += h.shadowRoot.textContent || '';
              return t;
            })();
            if (text.includes('网络出错') || text.includes('上传失败')) return 'failed';
            const hasCancelBtn = text.includes('取消上传');
            const hasProgress = !!root.querySelector('.weui-desktop-progress-bar,[class*=uploadProgress]');
            if (!hasCancelBtn && !hasProgress && text.includes('删除')) return 'done';
            return 'uploading';
            """)
            log(f"  上传状态: {result} ({(i+1)*5}s)")
            if result == 'done':
                log("✅ 视频上传完成")
                break
            if result == 'failed':
                log("❌ 视频上传失败")
                return
        else:
            log("❌ 上传超时(300s)")
            return

        # ── 等待 30s ─────────────────────────────────────────────
        log("=== Step 5: 等待 30s 让平台处理视频 ===")
        for i in range(6):
            await asyncio.sleep(5)
            log(f"  {(i+1)*5}/30s...")

        # ── 等编辑按钮就绪 ────────────────────────────────────────
        log("=== Step 6: 等待封面编辑按钮就绪 ===")
        edit_ready = False
        for i in range(24):
            await asyncio.sleep(2.5)
            edit_ready = await wujie_eval(page, """
            const btn = root.querySelector('.edit-btn');
            return !!(btn && btn.offsetHeight > 0 && btn.offsetWidth > 0);
            """)
            log(f"  edit-btn 就绪: {edit_ready} ({(i+1)*2.5:.0f}s)")
            if edit_ready is True:
                log("✅ 编辑按钮已就绪")
                break
        if edit_ready is not True:
            log("❌ 等待编辑按钮超时")
            return

        # ── 点击编辑按钮 ─────────────────────────────────────────
        log("=== Step 7: 点击封面编辑按钮 ===")
        clicked = await wujie_eval(page, """
        const btn = root.querySelector('.edit-btn');
        if (btn && btn.offsetHeight > 0 && btn.offsetWidth > 0) {
          btn.click(); return 'clicked';
        }
        return 'not-ready';
        """)
        log(f"点击结果: {clicked}")
        if clicked != 'clicked':
            log("❌ 点击失败")
            return
        log("✅ 已点击，等弹窗打开...")
        await asyncio.sleep(2)

        # ── 注入封面文件 ─────────────────────────────────────────
        log("=== Step 8: 注入封面文件 ===")
        inp = page.locator('input[type=file]').nth(1)
        cnt = await inp.count()
        log(f"  input[type=file] nth(1) count={cnt}")
        if cnt == 0:
            inp = page.locator('input[type=file]').last
            cnt = await inp.count()
            log(f"  input[type=file] last count={cnt}")
        if cnt == 0:
            log("❌ 未找到封面 input")
            return
        await inp.set_input_files(COVER_PATH)
        log("✅ 封面文件已注入，等待渲染...")
        await asyncio.sleep(3)

        # ── 点击确认 ─────────────────────────────────────────────
        log("=== Step 9: 点击确认按钮 ===")
        confirmed = await wujie_eval(page, """
        const btns = root.querySelectorAll('button');
        for (const btn of btns) {
          if (btn.offsetHeight > 0 && btn.innerText && btn.innerText.trim() === '确认') {
            btn.click(); return 'clicked';
          }
        }
        return 'not-found';
        """)
        log(f"确认结果: {confirmed}")
        if confirmed == 'clicked':
            log("✅ 封面确认完成！")
        else:
            log(f"❌ 确认按钮未找到({confirmed})")
            return

        log("")
        log("🎉 测试完成！页面未发表，可手动继续操作。")
        await asyncio.sleep(5)

    except Exception as e:
        log(f"❌ 异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await safe_disconnect(pw, browser)


if __name__ == "__main__":
    asyncio.run(main())
