#!/usr/bin/env python3
"""
抖音视频上传脚本（CDP 连接 OpenClaw 浏览器）
2026-03-01 重写干净版

用法：
  python3 douyin_upload.py \
    --video /path/portrait.mp4 \
    --title "标题" \
    --desc "描述正文（不含话题）" \
    --tags "苹果,Python,AI大模型" \
    --cover34 /path/cover-3x4.png \
    --cover43 /path/cover-4x3.png \
    --dtime "2026-03-02 17:00:00"
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from cdp_base import log_argv,  connect_browser, safe_disconnect, new_tab, log, exit_published, exit_need_login, exit_failed

MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"
UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"


async def check_login_and_duplicate(page, title: str) -> dict:
    await asyncio.sleep(2)
    text = await page.evaluate("() => document.body.innerText")
    url  = await page.evaluate("() => location.href")
    if "扫码登录" in text or "login" in url:
        return {"loggedIn": False}
    def core(s):
        return "".join(c for c in s if "\u4e00" <= c <= "\u9fff" or c.isalnum())
    dup = bool(title) and core(title[:8]) in core(text)
    return {"loggedIn": True, "duplicate": dup}


async def upload_video(page, video_path: str):
    log(f"[抖音] 上传视频: {video_path}")
    inputs = await page.query_selector_all("input[type=file]")
    for inp in inputs:
        acc = await inp.get_attribute("accept") or ""
        if "video" in acc or ".mp4" in acc:
            await inp.set_input_files(video_path)
            log("[抖音] 视频文件已选择")
            return
    if inputs:
        await inputs[0].set_input_files(video_path)
        log("[抖音] 视频文件已选择（第一个 input）")
    else:
        exit_failed("抖音：找不到视频 file input")


async def wait_upload_done(page, timeout=300):
    """等视频上传完成：标题 input 出现 且 进度条消失"""
    log("[抖音] 等待视频上传完成...")
    for i in range(timeout // 5):
        await asyncio.sleep(5)
        done = await page.evaluate("""
        () => {
          const hasTitle  = !!document.querySelector('input[placeholder*="标题"]');
          const uploading = !!document.querySelector(
            '[class*=upload-status],[class*=uploadProgress],[class*=upload-progress]'
          );
          return hasTitle && !uploading;
        }
        """)
        if done:
            log(f"[抖音] 上传完成（{(i+1)*5}s）")
            return True
    return False


async def fill_title(page, title: str):
    log(f"[抖音] 填写标题: {title[:20]}...")
    await page.evaluate("""
    (title) => {
      const inp = document.querySelector('input[placeholder*="标题"]');
      if (!inp) return;
      const nativeSet = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      nativeSet.call(inp, title);
      inp.dispatchEvent(new Event('input', {bubbles: true}));
    }
    """, title)
    await asyncio.sleep(2)


async def fill_desc(page, desc: str, tags: list = None):
    """
    填写描述正文 + 话题。
    desc: 纯正文（不含 #话题）
    tags: ['苹果', 'Python'] 不含 # 前缀，最多 5 个
    """
    log("[抖音] 填写描述")
    await page.evaluate("""
    (desc) => {
      const eds = [...document.querySelectorAll('[contenteditable=true]')]
        .filter(e => e.offsetHeight > 40 && e.offsetWidth > 200);
      if (!eds.length) return;
      const ed = eds[0];
      ed.focus();
      document.execCommand('selectAll', false, null);
      document.execCommand('delete', false, null);
      document.execCommand('insertText', false, desc);
    }
    """, desc)
    await asyncio.sleep(1.5)

    if tags:
        tags = tags[:5]
        log(f"[抖音] 添加话题: {tags}")
        for tag in tags:
            await page.evaluate("""
            () => {
              const btn = [...document.querySelectorAll('[class*=toolbar-button]')]
                .find(e => e.textContent.includes('添加话题') && e.offsetHeight > 0);
              if (btn) btn.click();
            }
            """)
            await asyncio.sleep(0.8)
            await page.keyboard.type(tag)
            await asyncio.sleep(2.5)
            await page.keyboard.press("Enter")
            await asyncio.sleep(1.0)

        await page.evaluate("""
        () => { const inp = document.querySelector('input[placeholder*="标题"]'); if (inp) inp.focus(); }
        """)
        await asyncio.sleep(0.5)

    await asyncio.sleep(0.5)


async def set_cover(page, cover34_path: str, cover43_path: str):
    """
    上传竖封面(3:4)和横封面(4:3)。
    ⚠️  必须在视频上传完成后调用（否则弹窗结构不同）。

    流程：
      1. 点"选择封面"打开弹窗
      2. mouse.click "上传封面"按钮 + expect_file_chooser → 竖封面
      3. 点"设置横封面" → 轮询等按钮重新出现 → mouse.click + expect_file_chooser → 横封面
      4. 点"完成"关弹窗
    """
    if not cover34_path and not cover43_path:
        log("[抖音] 无封面，跳过")
        return

    # 等待视频处理完毕（封面预览帧生成），轮询"选择封面"按钮可点击
    log("[抖音] 等待视频处理完成，封面按钮可点击...")
    cover_btn_ready = False
    for i in range(60):  # 最多 120s，每 2s 检查
        await asyncio.sleep(2)
        cover_btn_ready = await page.evaluate("""
        () => {
          const el = [...document.querySelectorAll('*')]
            .find(e => e.textContent.trim() === '选择封面'
              && e.offsetHeight > 0 && e.offsetHeight < 60);
          return !!el;
        }
        """)
        if cover_btn_ready:
            log(f"[抖音] 封面按钮就绪（{(i+1)*2}s）")
            break
    if not cover_btn_ready:
        log("[抖音] 等待封面按钮超时（120s），继续尝试")

    log("[抖音] 打开封面弹窗")
    await page.evaluate("""
    () => {
      const els = [...document.querySelectorAll('*')]
        .filter(e => e.textContent.trim() === '选择封面'
          && e.offsetHeight > 0 && e.offsetHeight < 60);
      if (els[0]) { els[0].click(); return; }
      const slots = [...document.querySelectorAll('[class*=coverControl]')]
        .filter(e => e.offsetHeight > 0);
      if (slots[0]) slots[0].click();
    }
    """)
    await asyncio.sleep(5)  # 弹窗打开动画 + canvas 初始化

    # JS 检测"上传封面"按钮是否可见
    CHECK_UPLOAD_BTN_JS = """
    () => !!([...document.querySelectorAll('*')]
      .find(e => e.textContent.trim() === '上传封面'
        && e.offsetHeight > 0 && e.offsetHeight < 60 && e.offsetWidth > 60))
    """

    GET_UPLOAD_BTN_COORDS_JS = """
    () => {
      const el = [...document.querySelectorAll('*')]
        .find(e => e.textContent.trim() === '上传封面'
          && e.offsetHeight > 0 && e.offsetHeight < 60 && e.offsetWidth > 60);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    }
    """

    async def wait_upload_btn(timeout_s=60, label=""):
        """轮询等"上传封面"按钮出现，最多 timeout_s 秒"""
        for i in range(timeout_s // 2):
            await asyncio.sleep(2)
            has = await page.evaluate(CHECK_UPLOAD_BTN_JS)
            if has:
                log(f"[抖音] {label}上传封面按钮就绪（{(i+1)*2}s）")
                return True
        log(f"[抖音] {label}等待上传封面按钮超时（{timeout_s}s）")
        return False

    async def upload_via_btn(cover_path, label, max_retries=2):
        """上传封面，失败自动重试"""
        for attempt in range(max_retries):
            coords = await page.evaluate(GET_UPLOAD_BTN_COORDS_JS)
            if not coords:
                log(f"[抖音] {label}：未找到上传封面按钮（第{attempt+1}次）")
                if attempt < max_retries - 1:
                    await asyncio.sleep(3)
                continue
            try:
                async with page.expect_file_chooser(timeout=15000) as fc_info:
                    await page.mouse.click(coords['x'], coords['y'])
                fc = await fc_info.value
                await fc.set_files(cover_path)
                log(f"[抖音] {label} 上传成功")
                # 等封面渲染预览完成
                await asyncio.sleep(5)
                return True
            except Exception as e:
                log(f"[抖音] {label} 上传失败（第{attempt+1}次）: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(3)
        return False

    if cover34_path and os.path.exists(cover34_path):
        await wait_upload_btn(timeout_s=60, label="竖封面 ")
        await asyncio.sleep(5)  # canvas 初始化稳定时间
        await upload_via_btn(cover34_path, "竖封面3:4")

    if cover43_path and os.path.exists(cover43_path):
        # 切换到横封面 tab
        await page.evaluate("""
        () => {
          const btn = [...document.querySelectorAll('button')]
            .find(e => e.textContent.trim() === '设置横封面' && e.offsetHeight > 0);
          if (btn) btn.click();
        }
        """)
        await wait_upload_btn(timeout_s=60, label="横封面 ")
        await asyncio.sleep(5)  # canvas 切换后同样需要稳定时间
        await upload_via_btn(cover43_path, "横封面4:3")

    await page.evaluate("""
    () => {
      const btn = [...document.querySelectorAll('button')]
        .find(e => e.textContent.trim() === '完成' && e.offsetHeight > 0);
      if (btn) btn.click();
    }
    """)
    await asyncio.sleep(2)
    # 清理任何遗留的 file chooser 或 dialog
    await page.keyboard.press("Escape")
    await asyncio.sleep(0.5)
    log("[抖音] 封面弹窗关闭")


async def set_schedule(page, dtime: str):
    log(f"[抖音] 设置定时: {dtime}")
    dtime_short = dtime[:16]

    await page.evaluate("""
    () => {
      const el = [...document.querySelectorAll('*')]
        .find(e => e.textContent.trim() === '定时发布'
          && e.offsetHeight > 0 && e.offsetHeight < 60);
      if (el) el.click();
    }
    """)
    await asyncio.sleep(1)

    await page.evaluate("""
    (val) => {
      const inp = document.querySelector('input[placeholder="日期和时间"]');
      if (!inp) return;
      const nativeSet = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      nativeSet.call(inp, val);
      inp.dispatchEvent(new Event('input', {bubbles: true}));
      inp.dispatchEvent(new Event('change', {bubbles: true}));
    }
    """, dtime_short)
    await asyncio.sleep(0.8)
    await page.keyboard.press("Enter")
    await asyncio.sleep(2)

    val = await page.evaluate(
        "() => document.querySelector('input[placeholder=\"日期和时间\"]')?.value"
    )
    log(f"[抖音] 定时验证: {val}")


async def publish(page) -> bool:
    log("[抖音] 点击发布")
    await page.evaluate("""
    () => {
      const btn = [...document.querySelectorAll('button')]
        .find(e => e.textContent.trim() === '发布' && e.offsetHeight > 0);
      if (btn) btn.click();
    }
    """)
    await asyncio.sleep(8)
    url  = await page.evaluate("() => location.href")
    text = await page.evaluate("() => document.body.innerText.slice(0, 200)")
    return "manage" in url or "发布成功" in text or "upload" not in url


async def main():
    log_argv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--video",   required=True)
    parser.add_argument("--title",   required=True)
    parser.add_argument("--desc",    default="")
    parser.add_argument("--tags",    default="", help="话题，逗号分隔，不含#，最多5个")
    parser.add_argument("--cover34", default="")
    parser.add_argument("--cover43", default="")
    parser.add_argument("--dtime",   default="")
    args = parser.parse_args()

    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None

    # 启动前先关掉可能残留的 OS 文件选择框
    os.system("osascript -e 'tell application \"System Events\" to key code 53'")
    await asyncio.sleep(0.3)

    pw, browser = await connect_browser()
    try:
        page = await new_tab(browser, MANAGE_URL)
        result = await check_login_and_duplicate(page, args.title)
        if not result.get("loggedIn"):
            exit_need_login("抖音")
        if result.get("duplicate"):
            log("[抖音] 视频已存在，标记为 published")
            exit_published(args.dtime)

        await page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        await upload_video(page, args.video)
        ok = await wait_upload_done(page)
        if not ok:
            exit_failed("抖音：视频上传超时")

        await fill_title(page, args.title)
        await fill_desc(page, args.desc, tags=tags)
        await set_cover(page, args.cover34, args.cover43)
        # 确保封面相关弹窗/文件选择框全部关闭
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)

        if args.dtime:
            await set_schedule(page, args.dtime)

        ok = await publish(page)
        if ok:
            exit_published(args.dtime)
        else:
            exit_failed("抖音：发布后未检测到成功状态")

    except Exception as e:
        import traceback
        traceback.print_exc()
        exit_failed(str(e))
    finally:
        await safe_disconnect(pw, browser)


if __name__ == "__main__":
    asyncio.run(main())
