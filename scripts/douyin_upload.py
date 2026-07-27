#!/usr/bin/env python3
"""
抖音视频上传脚本（CDP 连接 MC 托管浏览器）
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
from cdp_base import (
    log_argv,
    connect_browser,
    safe_disconnect,
    new_tab,
    log,
    exit_published,
    exit_need_login,
    exit_failed,
    set_file_input_files_via_cdp,
)

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
    if await set_file_input_files_via_cdp(
        page,
        video_path,
        accept_keywords=["video", ".mp4"],
        token_prefix="omc-dy-video-input",
    ):
        log("[抖音] 视频文件已选择(CDP)")
        return

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
      2. CDP 直传（set_file_input_files_via_cdp）→ 竖封面（不触发原生文件对话框）
      3. 点"设置横封面" → 轮询等上传控件出现 → CDP 直传 → 横封面
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
        """上传封面:用 playwright set_input_files 定位封面弹窗 Semi Upload 隐藏 input。
        (CDP DOM.setFileInputFiles 对 Semi Upload 只触发预览、不触发真实上传,实测保存后
        封面为空;封面文件小无 50MB 限制,用 playwright 原生方式,与 SAU 一致。)"""
        for attempt in range(max_retries):
            try:
                # SAU 精确定位:封面弹窗内 div[class^='semi-upload upload'] 容器里的隐藏 input
                fi = page.locator("div[id*='creator-content-modal'] div[class^='semi-upload upload'] input.semi-upload-hidden-input").first
                if not await fi.count():
                    fi = page.locator("div[class^='semi-upload upload'] input.semi-upload-hidden-input").first
                if not await fi.count():
                    fi = page.locator("input.semi-upload-hidden-input").last
                await fi.set_input_files(cover_path)
                log(f"[抖音] {label} 上传成功（playwright set_input_files）")
                await asyncio.sleep(6)  # 等 Semi Upload 渲染 + 上传完成
                return True
            except Exception as e:
                log(f"[抖音] {label} 上传失败(第{attempt+1}次): {e}")
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

    # 关弹窗保存:封面文件异步上传到抖音服务器,太早点「完成」会提示"封面保存失败"
    # (实测时机不稳:有时一次成功、有时需手动重点一下)。改为点「完成」后检测发布页
    # 「选择封面」占位是否消失,未成功就等待异步上传后重试,最多 4 次。
    # 关弹窗保存:横封面异步上传/渲染,「完成」按钮在传完前是 disabled,点太早点不动
    # (实测人工等一下、按钮变亮再点完成就成功)。等待 + 只点「可点(非 disabled)」的完成,重试。
    # 先保守等 10s 让横封面异步上传完成(否则「完成」按钮 disabled、点太早保存失败),
    # 再点可点的「完成」;万一仍未就绪,循环重试兜底。
    await asyncio.sleep(10)
    saved = False
    for save_attempt in range(6):
        result = await page.evaluate("""
        () => {
          for (const label of ['完成', '保存', '确定']) {
            const btns = [...document.querySelectorAll('button')]
              .filter(e => e.textContent.trim() === label && e.offsetHeight > 0);
            for (const btn of btns) {
              const dis = btn.disabled || btn.getAttribute('aria-disabled') === 'true'
                || (btn.className || '').includes('disabled');
              if (!dis) { btn.click(); return label + ':clicked'; }
            }
            if (btns.length) return label + ':disabled';
          }
          return 'none';
        }
        """)
        await asyncio.sleep(2)
        ph = await page.evaluate("""() => [...document.querySelectorAll('*')].filter(e => e.textContent.trim() === '选择封面' && e.offsetHeight > 0).length""")
        log(f"[抖音] 封面保存尝试 {save_attempt+1}: [{result}] → 占位={ph}")
        if ph == 0:
            saved = True
            break
    if saved:
        log("[抖音] 封面保存成功（发布页无占位）")
    else:
        log("[抖音][警告] 封面保存多次未成功（发布页仍有占位）")


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

    try:
        inp = page.locator('input[placeholder="日期和时间"]').first
        await inp.click(timeout=5000, force=True)
        await inp.fill(dtime_short, timeout=5000)
        await inp.press("Enter")
        await asyncio.sleep(1)
        await page.keyboard.press("Escape")
    except Exception as e:
        log(f"[抖音] 定时输入框 fill 失败，尝试 JS 设置: {e}")

    await page.evaluate("""
    (val) => {
      const inp = document.querySelector('input[placeholder="日期和时间"]');
      if (!inp) return;
      inp.removeAttribute('readonly');
      const nativeSet = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      inp.focus();
      nativeSet.call(inp, val);
      inp.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertReplacementText', data: val}));
      inp.dispatchEvent(new Event('change', {bubbles: true}));
      inp.blur();
    }
    """, dtime_short)
    await asyncio.sleep(0.8)
    await page.keyboard.press("Enter")
    await asyncio.sleep(2)

    val = await page.evaluate(
        "() => document.querySelector('input[placeholder=\"日期和时间\"]')?.value"
    )
    log(f"[抖音] 定时验证: {val}")
    return val == dtime_short


async def set_declaration(page, option_text: str = "内容由AI生成"):
    """新版抖音发布页必填自主声明；AI 视频默认选择“内容由AI生成”。"""
    log(f"[抖音] 选择自主声明: {option_text}")

    opened = await page.evaluate("""
    (optionText) => {
      const visible = (e) => {
        const r = e.getBoundingClientRect();
        return (r.width || r.height || e.getClientRects().length)
          && getComputedStyle(e).visibility !== 'hidden'
          && getComputedStyle(e).display !== 'none';
      };
      const all = [...document.querySelectorAll('*')].filter(visible);
      const current = all.find(e => e.textContent.trim() === optionText);
      const placeholder = all.find(e => e.textContent.trim() === '请选择自主声明');
      if (!placeholder && current && !document.body.innerText.includes('对作品内容添加声明')) {
        return { success: true, already: true };
      }
      const target = placeholder || all.find(e => e.textContent.includes('自主声明') && e.textContent.includes('请选择'));
      if (!target) return { success: false, error: '自主声明入口未找到' };
      const trigger = target.closest('button,[role=button],[class*=select],[class*=Select],[class*=declaration]')
        || target;
      trigger.scrollIntoView({ block: 'center' });
      const r = trigger.getBoundingClientRect();
      return { success: true, x: r.x + r.width / 2, y: r.y + r.height / 2, text: trigger.textContent.trim().slice(0, 100) };
    }
    """, option_text)
    if not opened.get("success"):
        exit_failed(f"抖音：自主声明打开失败：{opened.get('error')}")
    if opened.get("already"):
        log(f"[抖音] 自主声明已选择: {option_text}")
        return

    await page.mouse.click(opened["x"], opened["y"])
    await asyncio.sleep(1)

    selected = False
    last_state = {}
    for _ in range(3):
        try:
            label = page.locator("label.semi-radio").filter(has_text=option_text).first
            await label.wait_for(state="visible", timeout=3000)
            box = await label.bounding_box()
            if box:
                await page.mouse.click(box["x"] + 18, box["y"] + box["height"] / 2)
            else:
                await label.click(timeout=3000, force=True)
        except Exception:
            await page.evaluate("""
            (optionText) => {
              const visible = (e) => {
                const r = e.getBoundingClientRect();
                return (r.width || r.height || e.getClientRects().length)
                  && getComputedStyle(e).visibility !== 'hidden'
                  && getComputedStyle(e).display !== 'none';
              };
              const label = [...document.querySelectorAll('label.semi-radio,label,[role=radio]')]
                .filter(visible)
                .find(e => e.textContent.trim() === optionText);
              const target = label?.querySelector('.semi-radio-addon,.semi-radio-inner-display,input')
                || label;
              if (target) {
                target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                target.click();
                target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
              }
            }
            """, option_text)

        await asyncio.sleep(0.8)
        last_state = await page.evaluate("""
        () => {
          const visible = (e) => {
            const r = e.getBoundingClientRect();
            return (r.width || r.height || e.getClientRects().length)
              && getComputedStyle(e).visibility !== 'hidden'
              && getComputedStyle(e).display !== 'none';
          };
          const ok = [...document.querySelectorAll('button')]
            .filter(visible)
            .find(e => e.textContent.trim() === '确定');
          const checked = !!document.querySelector('label.semi-radio-checked,.semi-radio-checked,[aria-checked=true]');
          return {
            checked,
            okEnabled: !!ok && !ok.disabled && ok.getAttribute('aria-disabled') !== 'true',
            okText: ok?.textContent.trim() || '',
          };
        }
        """)
        if last_state.get("checked") or last_state.get("okEnabled"):
            selected = True
            break

    if not selected:
        exit_failed(f"抖音：自主声明选项未选中 state={last_state}")

    confirmed = await page.evaluate("""
    () => {
      const visible = (e) => {
        const r = e.getBoundingClientRect();
        return (r.width || r.height || e.getClientRects().length)
          && getComputedStyle(e).visibility !== 'hidden'
          && getComputedStyle(e).display !== 'none';
      };
      const btn = [...document.querySelectorAll('button')]
        .filter(visible)
        .find(e => e.textContent.trim() === '确定' && !e.disabled && e.getAttribute('aria-disabled') !== 'true');
      if (!btn) return false;
      btn.click();
      return true;
    }
    """)
    if not confirmed:
        exit_failed("抖音：自主声明确定按钮不可用")

    await asyncio.sleep(1.5)
    verified = await page.evaluate("""
    (optionText) => ({
      modalOpen: document.body.innerText.includes('对作品内容添加声明'),
      hasOption: document.body.innerText.includes(optionText),
      stillPlaceholder: document.body.innerText.includes('请选择自主声明'),
    })
    """, option_text)
    if verified.get("modalOpen") or verified.get("stillPlaceholder"):
        exit_failed(f"抖音：自主声明未生效 state={verified}")
    log(f"[抖音] 自主声明已选择: {option_text}")


async def publish(page) -> bool:
    log("[抖音] 点击发布")
    await page.evaluate("""
    () => {
      const btn = [...document.querySelectorAll('button')]
        .find(e => e.textContent.trim() === '发布' && e.offsetHeight > 0);
      if (btn) btn.click();
    }
    """)
    last = {}
    for _ in range(12):
        await asyncio.sleep(5)
        last = await page.evaluate("""
        () => {
          const text = document.body.innerText || '';
          const url = location.href;
          const visible = (e) => {
            const r = e.getBoundingClientRect();
            return (r.width || r.height || e.getClientRects().length)
              && getComputedStyle(e).visibility !== 'hidden'
              && getComputedStyle(e).display !== 'none';
          };
          const hasPublishForm = (url.includes('/content/post/video') || url.includes('/content/upload'))
            || (text.includes('自主声明') && text.includes('定时发布')
              && [...document.querySelectorAll('button')].filter(visible).some(e => e.textContent.trim() === '发布'));
          const success = url.includes('/content/manage')
            || text.includes('发布成功')
            || text.includes('作品管理');
          return { url, success, hasPublishForm, body: text.slice(0, 300) };
        }
        """)
        if last.get("success") and not last.get("hasPublishForm"):
            log(f"[抖音] 发布成功状态: {last.get('url')}")
            return True
        if not last.get("hasPublishForm") and last.get("success"):
            return True
    log(f"[抖音] 未检测到成功状态: {last}")
    return False


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
    parser.add_argument("--no-publish", action="store_true",
                        help="跑到发布前即停、不点发布(草稿保留,测试封面用)")
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

        if args.no_publish:
            log("[抖音] --no-publish:封面已处理,停在此(不走声明/发布,测试用)")
            await asyncio.sleep(2)
            return

        await set_declaration(page)

        if args.dtime:
            ok = await set_schedule(page, args.dtime)
            if not ok:
                exit_failed(f"抖音：定时设置未生效，期望 {args.dtime[:16]}")

        if args.no_publish:
            log("[抖音] --no-publish:封面/文案/声明/定时已就绪,停在发布前(不点发布,草稿保留供人工核对封面)")
            return

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
