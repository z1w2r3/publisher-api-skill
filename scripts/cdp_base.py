#!/usr/bin/env python3
"""CDP 基础工具 - 连接 OpenClaw 浏览器"""
import asyncio
import os
import subprocess
import sys
from playwright.async_api import async_playwright, Page, BrowserContext

CDP_URL = "http://127.0.0.1:18800"


async def connect_browser():
    """连接 OpenClaw 浏览器，返回 (playwright, browser)"""
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(CDP_URL)
    return pw, browser


async def safe_disconnect(pw, browser):
    """断开 CDP 连接，不关闭浏览器进程（只停 Playwright driver）"""
    try:
        await pw.stop()
    except Exception:
        pass
async def get_or_create_page(browser, url: str = None) -> Page:
    """获取已有 context 的 page，或新建 page"""
    contexts = browser.contexts
    if not contexts:
        ctx = await browser.new_context()
    else:
        ctx = contexts[0]
    pages = ctx.pages
    if pages:
        page = pages[-1]
    else:
        page = await ctx.new_page()
    if url:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
    return page


async def new_tab(browser, url: str) -> Page:
    """复用已有 tab，自动处理离开确认弹窗。关闭同域名旧 tab 避免堆积。"""
    contexts = browser.contexts
    ctx = contexts[0] if contexts else await browser.new_context()

    # 关闭同域名的旧 tab（视频号等多次发布会堆积）
    from urllib.parse import urlparse
    target_host = urlparse(url).netloc
    for old_page in list(ctx.pages):
        try:
            if urlparse(old_page.url).netloc == target_host:
                await old_page.close()
        except Exception:
            pass

    page = await ctx.new_page()

    # 注册 dialog 自动接受（处理"确认离开"等弹窗）
    async def _handle_dialog(dialog):
        await dialog.accept()
    page.on("dialog", _handle_dialog)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    finally:
        page.remove_listener("dialog", _handle_dialog)

    # 处理视频号"将此次编辑保留?"DOM 弹窗（点"不保留"）
    await asyncio.sleep(1)
    try:
        leave_btn = page.locator('button:has-text("不保留"), button:has-text("离开")')
        if await leave_btn.count():
            await leave_btn.first.click()
            await asyncio.sleep(1)
    except Exception:
        pass

    await asyncio.sleep(3)
    return page


def osascript_select_file(file_path: str):
    """用 osascript 在系统对话框中选择文件"""
    abs_path = os.path.abspath(file_path)
    # 复制路径到剪贴板
    subprocess.run(["pbcopy"], input=abs_path.encode(), check=True)
    script = '''
tell application "Google Chrome" to activate
delay 0.5
tell application "System Events"
    keystroke "g" using {command down, shift down}
    delay 1.0
    keystroke "v" using command down
    delay 0.5
    key code 36
    delay 1.5
    key code 36
end tell
'''
    subprocess.run(["osascript", "-e", script], check=True)
    asyncio.get_event_loop().run_until_complete(asyncio.sleep(2))


def load_brief(brief_path: str, platform: str) -> dict:
    """从 brief.json 加载指定平台的文案数据，返回 dict（空 dict 表示无数据）"""
    import json
    if not brief_path or not os.path.exists(brief_path):
        return {}
    try:
        with open(brief_path, 'r', encoding='utf-8') as f:
            brief = json.load(f)
        return brief.get(platform, {})
    except Exception as e:
        print(f"[brief] 加载失败: {e}", flush=True)
        return {}


def log(msg: str):
    print(msg, flush=True)


def log_argv():
    """在脚本启动时记录完整命令到日志文件，方便排查问题。"""
    import datetime
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = f"/tmp/dispatch_run_{date_str}.log"
    script = sys.argv[0].split("/")[-1]
    args = " ".join(sys.argv[1:])
    with open(log_path, "a") as f:
        f.write(f"[{ts}] {script} {args}\n\n")


def exit_published(scheduled_time: str = ""):
    msg = "PUBLISHED"
    if scheduled_time:
        msg += f" scheduled_time={scheduled_time}"
    print(msg, flush=True)
    sys.exit(0)


def exit_need_login(platform: str):
    print(f"NEED_LOGIN {platform}需要扫码登录", flush=True)
    sys.exit(2)


def exit_failed(reason: str):
    print(f"FAILED error={reason}", flush=True)
    sys.exit(1)
