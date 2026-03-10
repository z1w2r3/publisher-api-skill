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
import re

sys.path.insert(0, os.path.dirname(__file__))
from cdp_base import log_argv,  connect_browser, safe_disconnect, new_tab, log, exit_published, exit_need_login, exit_failed

LIST_URL = "https://channels.weixin.qq.com/platform/post/list"
CREATE_URL = "https://channels.weixin.qq.com/platform/post/create"


async def check_login_and_duplicate(page, short_title: str) -> dict:
    """视频号 shadow DOM，用 innerText 读页面文字检查"""
    result = await page.evaluate("""
    async (title) => {
      await new Promise(r => setTimeout(r, 2000));
      const text = document.body.innerText;
      // 扫码登录页特征（明确出现才判为未登录）
      const loginKeywords = ['扫描二维码', '扫码登录', '微信扫一扫', '立即登录', '手机号登录'];
      if (loginKeywords.some(kw => text.includes(kw))) return { loggedIn: false };
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
    # 先等 wujie 微前端初始化完成（最多 60s），file input 才会出现
    log("[视频号] 等待 wujie 初始化...")
    wujie_ready = False
    for i in range(24):  # 最多 60s，每 2.5s 检查一次
        await asyncio.sleep(2.5)
        has_input = await page.evaluate("""
        () => {
          const inp = document.querySelector('input[type="file"]');
          if (inp) return true;
          const sr = document.querySelector('wujie-app') && document.querySelector('wujie-app').shadowRoot;
          if (!sr) return false;
          return !!sr.querySelector('input[type="file"]');
        }
        """)
        if has_input:
            log(f"[视频号] wujie 已就绪，file input 可见（{(i+1)*2.5:.0f}s）")
            wujie_ready = True
            break
    if not wujie_ready:
        log("[视频号] 等待 wujie 超时（60s），尝试直接上传")

    # 在 Shadow DOM 中找 file input，Pierce 选择器可穿透 shadow DOM
    try:
        inp = page.locator('input[type=file]').first
        await inp.set_input_files(video_path, timeout=60000)
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
        # 找不到上传入口，先检查是否是登录问题
        page_text = await page.evaluate("() => document.body.innerText")
        login_keywords = ['扫描二维码', '扫码登录', '微信扫一扫', '立即登录', '手机号登录']
        if any(kw in page_text for kw in login_keywords):
            exit_need_login("视频号（上传页未找到上传入口，疑似未登录）")
        exit_failed("视频号：找不到视频上传 input")


async def wait_upload_done(page, timeout=600):
    log("[视频号] 等待上传完成...")
    for i in range(timeout // 5):
        await asyncio.sleep(5)
        try:
            result = await page.evaluate("""
            () => {
              const allText = (() => {
                let t = document.body.innerText;
                for (const h of document.querySelectorAll('*'))
                  if (h.shadowRoot) t += h.shadowRoot.textContent || '';
                return t;
              })();
              if (allText.includes('网络出错') || allText.includes('上传失败')) return 'failed';
              // 真正完成：进度条不存在 且 取消上传按钮不存在
              const hasCancelBtn = allText.includes('取消上传');
              const hasProgressBar = !!document.querySelector('.weui-desktop-progress-bar, [class*=uploadProgress]');
              if (!hasCancelBtn && !hasProgressBar && allText.includes('删除')) return 'done';
              return 'uploading';
            }
            """)
            if result == 'done':
                log("[视频号] 上传完成")
                return True
            elif result == 'failed':
                log("[视频号] 上传失败（网络错误）")
                return False
            log(f"[视频号] 上传中... ({(i+1)*5}s)")
        except:
            pass
    return False


async def set_cover(page, cover34_path: str):
    """
    设置视频号封面。
    流程：点"编辑"按钮打开封面弹窗 → set_input_files 注入图片 → 点"确认"
    """
    if not cover34_path or not os.path.exists(cover34_path):
        log("[视频号] 无封面，跳过")
        return
    log(f"[视频号] 设置封面: {cover34_path}")

    # Step1: 通过 JS 进入 wujie shadow root 点击 .edit-btn（封面编辑按钮）
    # 视频号用无界(wujie)微前端，Playwright 的 get_by_text 无法穿透，需直接操作 shadow root
    clicked = await page.evaluate("""
    () => {
      try {
        const sr = document.querySelector('wujie-app') && document.querySelector('wujie-app').shadowRoot;
        const root = sr ? sr.querySelector('html') : document;
        if (!root) return 'no-root';
        const btn = root.querySelector('.edit-btn');
        if (btn && btn.offsetHeight > 0 && btn.offsetWidth > 0) {
          btn.click();
          return 'clicked';
        }
        return 'not-ready';
      } catch(e) { return 'err:' + e.message; }
    }
    """)
    log(f"[视频号] 封面编辑按钮点击: {clicked}")
    if clicked != 'clicked':
        log("[视频号] 编辑按钮未就绪，跳过封面")
        return

    # Step2: 等弹窗动画完成，用 expect_file_chooser 拦截文件选择框（防止 OS 弹窗出现）
    # 原理：在 JS click input 之前注册拦截器，OS 文件选择框被 Playwright 截住，文件直接注入
    await asyncio.sleep(10)
    uploaded = False
    try:
        async with page.expect_file_chooser(timeout=8000) as fc_info:
            # JS 触发 wujie shadow root 里的 image file input click
            await page.evaluate("""
            () => {
              try {
                const sr = document.querySelector('wujie-app') && document.querySelector('wujie-app').shadowRoot;
                const root = sr ? sr.querySelector('html') : document;
                const inputs = root.querySelectorAll('input[type=file]');
                // 取 image input（第2个，accept image/*）
                for (const inp of inputs) {
                  if (inp.accept && inp.accept.includes('image')) { inp.click(); return; }
                }
                // 兜底取最后一个
                if (inputs.length > 0) inputs[inputs.length - 1].click();
              } catch(e) {}
            }
            """)
        fc = await fc_info.value
        await fc.set_files(cover34_path)
        log("[视频号] 封面已上传（via file chooser 拦截）")
        uploaded = True
    except Exception as e:
        log(f"[视频号] file chooser 未触发（{e}），直接注入 input")

    if not uploaded:
        await asyncio.sleep(2)
        inp = page.locator('input[type=file]').nth(1)
        if not await inp.count():
            inp = page.locator('input[type=file]').last
        if not await inp.count():
            log("[视频号] 未找到封面 input，跳过封面")
            return
        await inp.set_input_files(cover34_path)
        log("[视频号] 封面已注入（via set_input_files 兜底）")

    # Step3: 等图片渲染完成后用 JS 点击确认按钮
    await asyncio.sleep(3)
    confirmed = await page.evaluate("""
    () => {
      try {
        const sr = document.querySelector('wujie-app') && document.querySelector('wujie-app').shadowRoot;
        const root = sr ? sr.querySelector('html') : document;
        if (!root) return 'no-root';
        const btns = root.querySelectorAll('button');
        for (const btn of btns) {
          if (btn.offsetHeight > 0 && btn.innerText && btn.innerText.trim() === '\u786e\u8ba4') {
            btn.click();
            return 'clicked';
          }
        }
        return 'not-found';
      } catch(e) { return 'err:' + e.message; }
    }
    """)
    if confirmed == 'clicked':
        log("[视频号] 封面确认完成")
    else:
        log(f"[视频号] 封面确认按钮未找到({confirmed})，跳过封面继续发布")
        return
    await asyncio.sleep(2)


async def fill_desc(page, desc: str):
    log("[视频号] 填写描述")
    await page.locator("div.input-editor").click()
    await page.keyboard.type(desc)
    await asyncio.sleep(1)


def normalize_short_title(s: str) -> str:
    """去除中英文之间多余空格，保留纯英文单词间空格"""
    s = re.sub(r"([\u4e00-\u9fff])\s+([A-Za-z0-9])", r"\1\2", s)
    s = re.sub(r"([A-Za-z0-9])\s+([\u4e00-\u9fff])", r"\1\2", s)
    return s.strip()

async def fill_short_title(page, short_title: str):
    short_title = normalize_short_title(short_title)
    log(f"[视频号] 填写短标题: {short_title}")
    short_title_element = page.get_by_text("短标题", exact=True).locator("..").locator(
        "xpath=following-sibling::div").locator('span input[type="text"]')
    if await short_title_element.count():
        await short_title_element.fill(short_title)
        log("[视频号] 短标题填写成功")
    await asyncio.sleep(1)


async def set_original(page):
    """勾选原创声明"""
    log("[视频号] 勾选原创声明")
    try:
        # 点原创声明 checkbox
        cb = page.locator('label.ant-checkbox-wrapper:has-text("展示原创标记")')
        if not await cb.count():
            log("[视频号] 未找到原创声明 checkbox，跳过"); return
        await cb.click()
        await asyncio.sleep(1.5)

        # 弹出"原创权益"确认框
        agree_btn = page.get_by_role("button", name="声明原创")
        if await agree_btn.count() and await agree_btn.is_visible():
            # 弹窗内的同意 checkbox 在 .declare-body-wrapper 或 .weui-desktop-dialog__bd
            dialog_cb = page.locator('.declare-body-wrapper input[type=checkbox], .weui-desktop-dialog__bd input[type=checkbox]')
            if await dialog_cb.count():
                await dialog_cb.first.click(force=True)
            else:
                # 坐标兜底：弹窗左上角 checkbox 约 (384, 365)
                await page.mouse.click(384, 365)
            await asyncio.sleep(0.5)
            await agree_btn.click()
            log("[视频号] 原创权益弹窗已确认")
        log("[视频号] 原创声明勾选完成")
    except Exception as e:
        log(f"[视频号] 原创声明异常: {e}")
        # 确保弹窗关闭（点取消）
        cancel = page.get_by_role("button", name="取消")
        try:
            if await cancel.count() and await cancel.is_visible():
                await cancel.click()
        except:
            pass
    await asyncio.sleep(1)


async def set_schedule(page, dtime: str):
    log(f"[视频号] 设置定时: {dtime}")
    from datetime import datetime
    dt = datetime.strptime(dtime, "%Y-%m-%d %H:%M:%S")

    # 点"定时" label（第2个，第1个是"不定时"）
    label_element = page.locator("label").filter(has_text="定时").nth(1)
    await label_element.click()
    await asyncio.sleep(1)

    # 打开日历
    await page.click('input[placeholder="请选择发表时间"]')
    await asyncio.sleep(1)

    # 切换到目标月份
    str_month = str(dt.month) if dt.month > 9 else "0" + str(dt.month)
    current_month = str_month + "月"
    try:
        page_month = await page.inner_text('span.weui-desktop-picker__panel__label:has-text("月")')
        if page_month != current_month:
            await page.click('button.weui-desktop-btn__icon__right')
            await asyncio.sleep(0.5)
    except:
        pass

    # 点目标日期
    elements = await page.query_selector_all('table.weui-desktop-picker__table a')
    for element in elements:
        cls = await element.evaluate('el => el.className')
        if 'weui-desktop-picker__disabled' in cls:
            continue
        text = await element.inner_text()
        if text.strip() == str(dt.day):
            await element.click()
            break
    await asyncio.sleep(0.5)

    # 填小时
    await page.click('input[placeholder="请选择时间"]')
    await asyncio.sleep(0.3)
    await page.keyboard.press("Meta+a")
    await page.keyboard.type(str(dt.hour))
    await asyncio.sleep(0.3)

    # 点描述框让时间生效
    await page.locator("div.input-editor").click()
    await asyncio.sleep(1)
    log(f"[视频号] 定时设置完成: {dt.strftime('%Y-%m-%d %H:%M')}")


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
    log_argv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--short-title", required=True)
    parser.add_argument("--desc", default="")
    parser.add_argument("--cover34", default="")
    parser.add_argument("--dtime", default="")
    args = parser.parse_args()

    # 启动前先关掉可能残留的 OS 文件选择框（偶发：set_input_files 触发真实弹窗）
    os.system("osascript -e 'tell application \"System Events\" to key code 53'")
    await asyncio.sleep(0.3)

    pw, browser = await connect_browser()
    try:
        page = await new_tab(browser, LIST_URL)
        if 'login' in page.url:
            exit_need_login("视频号（跳转到登录页）")
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

        # 上传成功后等待 30s，让平台完成视频处理和封面生成
        log("[视频号] 视频上传成功，等待 30s 让平台处理...")
        await asyncio.sleep(30)

        # 视频上传后平台需要转码（会显示"等待 X 分钟"），转码完成后才出现 .edit-btn
        # 轮询最多 10 分钟（120×5s），等 .edit-btn 出现且可点击
        log("[视频号] 等待平台转码完成（编辑按钮可点击，最多等 10 分钟）...")
        edit_ready = False
        for i in range(120):  # 最多等 600s（10 分钟），每 5s 检查一次
            await asyncio.sleep(5)
            edit_ready = await page.evaluate("""
            () => {
              try {
                const sr = document.querySelector('wujie-app') && document.querySelector('wujie-app').shadowRoot;
                const root = sr ? sr.querySelector('html') : document;
                if (!root) return false;
                const btn = root.querySelector('.edit-btn');
                return !!(btn && btn.offsetHeight > 0 && btn.offsetWidth > 0);
              } catch(e) { return false; }
            }
            """)
            if edit_ready:
                log(f"[视频号] 封面编辑按钮已就绪（{30+(i+1)*5}s）")
                break
            if i % 12 == 0:  # 每分钟打一次日志
                log(f"[视频号] 等待转码... ({30+(i+1)*5}s)")
        if not edit_ready:
            log("[视频号] 等待编辑按钮超时（10 分钟），继续尝试")

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
        await safe_disconnect(pw, browser)


if __name__ == "__main__":
    asyncio.run(main())
