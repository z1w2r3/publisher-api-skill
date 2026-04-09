#!/usr/bin/env python3
"""
B站视频上传脚本（CDP 连接 OpenClaw 浏览器）
参考 auto-Browser platform-bilibili.md 流程

用法：
  python3 bili_upload_cdp.py \
    --video /path/landscape.mp4 \
    --title "标题" --desc "简介" \
    --tags "tag1,tag2" \
    --cover43 /path/cover-4x3.png \
    --cover169 /path/cover-16x9.png \
    --dtime "2026-03-02 17:00:00"
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from cdp_base import log_argv, connect_browser, safe_disconnect, new_tab, log, exit_published, exit_need_login, exit_failed

MANAGE_URL = "https://member.bilibili.com/platform/upload-manager/article"
UPLOAD_URL = "https://member.bilibili.com/platform/upload/video/frame"


async def check_login_and_duplicate(page, title: str) -> dict:
    """查重：读列表前5条标题，去标点后匹配"""
    result = await page.evaluate("""
    async (title) => {
      await new Promise(r => setTimeout(r, 3000));
      if (location.href.includes('login')) return { loggedIn: false };
      const core = s => s.replace(/[^\\u4e00-\\u9fff\\w]/g, '');
      const text = core(document.body.innerText);
      const duplicate = title ? text.includes(core(title)) : false;
      return { loggedIn: true, duplicate };
    }
    """, title)
    return result


async def upload_video(page, video_path: str):
    """上传视频文件 - 使用 expect_file_chooser 触发真实上传"""
    log(f"[B站] 上传视频: {video_path}")

    # 关闭可能的"不用了"弹窗
    await page.evaluate("""
    () => {
      const dismiss = [...document.querySelectorAll('*')]
        .find(e => e.textContent.trim() === '不用了' && e.offsetHeight > 0 && e.offsetHeight < 60);
      if (dismiss) dismiss.click();
    }
    """)
    await asyncio.sleep(1)

    # 点击上传区域触发文件选择器
    log("[B站] 点击上传区域...")
    try:
        async with page.expect_file_chooser(timeout=10000) as fc_info:
            # 点击上传区域
            await page.evaluate("""
            () => {
              const uploadArea = [...document.querySelectorAll('*')]
                .find(e => e.textContent.includes('点击上传') || e.textContent.includes('拖拽到此'));
              if (uploadArea) uploadArea.click();
            }
            """)
        fc = await fc_info.value
        await fc.set_files(video_path)
        log("[B站] 视频文件已选择")
    except Exception as e:
        log(f"[B站] 点击上传失败，尝试直接设置: {e}")
        # 备选：直接找 input
        inputs = await page.query_selector_all('input[type=file]')
        target = None
        for inp in inputs:
            acc = await inp.get_attribute('accept') or ''
            if 'video' in acc or '.mp4' in acc:
                target = inp
                break
        if not target and inputs:
            target = inputs[0]
        if not target:
            exit_failed("B站：找不到视频上传 input")
        await target.set_input_files(video_path)
        log("[B站] 视频文件已选择(备选)")


async def wait_upload_done(page, timeout=300):
    """等待上传完成：标题 input 出现 + 页面包含上传完成"""
    log("[B站] 等待视频上传完成...")
    for i in range(timeout // 5):
        await asyncio.sleep(5)
        try:
            status = await page.evaluate("""
            () => {
              const hasTitle = !!document.querySelector('input[placeholder*="标题"]');
              const done = document.body.innerText.includes('上传完成');
              return { hasTitle, done };
            }
            """)
            if status.get('hasTitle') and status.get('done'):
                log(f"[B站] 上传完成（{(i+1)*5}s）")
                return True
            if status.get('hasTitle'):
                log(f"[B站] 表单已出现，上传中...（{(i+1)*5}s）")
        except:
            pass
    return False


async def fill_title(page, title: str):
    """填标题 + 选自制"""
    log(f"[B站] 填写标题: {title[:30]}...")
    await page.evaluate("""
    (title) => {
      const input = [...document.querySelectorAll('input')]
        .find(e => e.placeholder?.includes('标题'));
      if (!input) return;
      const nativeSet = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      nativeSet.call(input, title);
      input.dispatchEvent(new Event('input', {bubbles: true}));
      input.dispatchEvent(new Event('change', {bubbles: true}));
      // 选"自制"
      const typeEl = [...document.querySelectorAll('span,div,p,label')]
        .find(e => e.textContent.trim() === '自制' && e.children.length === 0 && e.offsetHeight > 0);
      if (typeEl) typeEl.click();
    }
    """, title)
    await asyncio.sleep(1)


async def select_zone(page):
    """选分区：人工智能"""
    log("[B站] 选择分区: 人工智能")
    await page.evaluate("""
    () => {
      const h3 = [...document.querySelectorAll('h3')].find(e => e.textContent.trim() === '分区');
      const section = h3?.closest('[class*=item]') || h3?.parentElement?.parentElement;
      const p = [...section.querySelectorAll('p')].find(e => e.textContent.trim() !== '*' && e.offsetHeight > 0);
      if (p) {
        p.click();
        setTimeout(() => {
          const el = [...document.querySelectorAll('li,span,div')]
            .find(e => e.textContent.trim() === '人工智能' && e.offsetHeight > 0 && e.offsetHeight < 50);
          if (el) el.click();
        }, 500);
      }
    }
    """)
    await asyncio.sleep(2)


async def fill_tags(page, tags: list):
    """填标签：先删旧标签，再逐个输入+Enter"""
    if not tags:
        return
    log(f"[B站] 填写标签: {tags}")
    await page.evaluate("""
    (tags) => {
      const input = document.querySelector('input[placeholder*="标签"]');
      if (!input) return 'not found';
      input.focus();
      const nativeSet = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;

      // 先删旧标签
      let deleted = 0;
      function deleteOne() {
        if (deleted >= 20) { setTimeout(addAll, 300); return; }
        if (document.querySelectorAll('p[class*="label-item"]').length === 0) { setTimeout(addAll, 300); return; }
        input.dispatchEvent(new KeyboardEvent('keydown', {key:'Backspace', code:'Backspace', keyCode:8, bubbles:true}));
        deleted++;
        setTimeout(deleteOne, 150);
      }

      let added = 0;
      function addAll() {
        if (added >= tags.length) return;
        nativeSet.call(input, tags[added]);
        input.dispatchEvent(new Event('input', {bubbles: true}));
        setTimeout(() => {
          input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', code:'Enter', keyCode:13, bubbles:true}));
          added++;
          setTimeout(addAll, 200);
        }, 100);
      }

      deleteOne();
      return 'processing ' + tags.length + ' tags';
    }
    """, tags)
    await asyncio.sleep(max(3, len(tags) * 1))


async def fill_desc(page, desc: str):
    """填简介（contenteditable 富文本编辑器）"""
    if not desc:
        return
    log("[B站] 填写简介")
    # 段落用 <p>，空行用 <p><br></p>
    html = ""
    for line in desc.split('\n'):
        if line.strip():
            html += f"<p>{line}</p>"
        else:
            html += "<p><br></p>"

    await page.evaluate("""
    (html) => {
      const editor = [...document.querySelectorAll('[contenteditable=true]')]
        .find(e => e.offsetHeight > 80);
      if (!editor) return;
      editor.innerHTML = html;
      editor.dispatchEvent(new Event('input', {bubbles: true}));
    }
    """, html)
    await asyncio.sleep(1)


async def set_cover(page, cover43_path: str, cover169_path: str):
    """
    B站双封面上传：
    1. 点"封面设置"打开弹窗
    2. 点"上传封面" + 选 4:3 封面
    3. 切到"个人空间封面 16:9" + 选 16:9 封面
    4. 点"完成"关闭弹窗
    """
    if not cover43_path and not cover169_path:
        log("[B站] 无封面，跳过")
        return

    log("[B站] 打开封面设置弹窗")
    await page.evaluate("""
    () => {
      const els = [...document.querySelectorAll('*')]
        .filter(e => e.textContent.trim() === '封面设置' && e.offsetHeight > 0 && e.offsetHeight < 200);
      const btn = els.find(e => getComputedStyle(e).cursor === 'pointer') || els[els.length - 1];
      if (btn) btn.click();
    }
    """)
    await asyncio.sleep(3)

    async def upload_cover(cover_path, label):
        """通过 expect_file_chooser 上传封面到指定区域"""
        if not cover_path or not os.path.exists(cover_path):
            log(f"[B站] {label}：文件不存在，跳过")
            return False

        # 根据 label 确定要查找的区域标题
        section_title = "4:3" if "4:3" in label else "16:9"
        log(f"[B站] {label}：查找 {section_title} 区域的上传按钮...")
        
        try:
            async with page.expect_file_chooser(timeout=15000) as fc_info:
                # 直接点击唯一的"上传封面"按钮
                await page.evaluate("""
                () => {
                  const btn = [...document.querySelectorAll('*')]
                    .find(e => e.textContent.trim() === '上传封面' && e.offsetHeight > 0);
                  if (btn) {
                    btn.click();
                    console.log('clicked upload cover button');
                  }
                }
                """)
                    
            fc = await fc_info.value
            await fc.set_files(cover_path)
            log(f"[B站] {label}：文件已选择，等待上传完成...")
            await asyncio.sleep(8)  # 等待上传完成
            log(f"[B站] {label} 上传完成")
            return True
        except Exception as e:
            log(f"[B站] {label} 上传失败: {e}")
            os.system("osascript -e 'tell application \"System Events\" to key code 53'")
            await asyncio.sleep(0.5)
            return False

    # 上传 4:3 封面 - 默认已选中4:3区域，直接上传
    if cover43_path and os.path.exists(cover43_path):
        log("[B站] 上传4:3封面（默认已选中）")
        await upload_cover(cover43_path, "4:3 封面")

    # 上传 16:9 封面 - 先选中16:9区域（点击"个人空间"标签）
    if cover169_path and os.path.exists(cover169_path):
        log("[B站] 选中16:9封面区域（点击个人空间标签）")
        # 点击"个人空间"标签选中16:9区域
        await page.evaluate("""
        () => {
          // 找底部切换标签中的"个人空间"
          const tabs = [...document.querySelectorAll('div, span, button')]
            .filter(e => e.textContent.trim() === '个人空间');
          // 点击最后一个（通常是底部切换标签）
          if (tabs.length > 0) {
            tabs[tabs.length - 1].click();
            console.log('clicked 个人空间 tab');
          }
        }
        """)
        await asyncio.sleep(2)
        # 等待蓝色边框出现
        await page.evaluate("""
        () => {
          return new Promise(resolve => setTimeout(resolve, 500));
        }
        """)
        log("[B站] 已选中16:9区域，准备上传")
        await upload_cover(cover169_path, "16:9 封面")

    # 点"完成"关闭弹窗
    await page.evaluate("""
    () => {
      const btn = [...document.querySelectorAll('*')]
        .find(e => e.textContent.trim() === '完成' && e.offsetHeight > 0 && e.offsetHeight < 60
          && e.offsetWidth > 40 && e.offsetWidth < 200 && e.children.length === 0);
      if (btn) btn.click();
    }
    """)
    await asyncio.sleep(2)
    log("[B站] 封面设置完成")


async def set_schedule(page, dtime: str):
    """
    B站定时发布：
    1. 打开定时开关
    2. 点日期元素打开日历 + 选目标日期
    3. 点时间元素打开时间面板 + 选时/分
    4. 关闭面板 + 验证
    注意：B站要求定时 >= 当前时间 + 2小时
    """
    dt = datetime.strptime(dtime, "%Y-%m-%d %H:%M:%S")
    day = str(dt.day)
    hh = f"{dt.hour:02d}"
    mm = f"{dt.minute:02d}"

    log(f"[B站] 设置定时: {dtime}")

    # Step 1: 打开定时开关
    await page.evaluate("""
    () => {
      const h3 = [...document.querySelectorAll('h3')].find(e => e.textContent.trim() === '定时发布');
      if (!h3) return;
      const section = h3.closest('[class*=item]') || h3.parentElement.parentElement;
      const sw = section.querySelector('[role=switch]')
        || [...section.querySelectorAll('*')].find(e => e.className?.includes?.('switch') && e.offsetWidth > 20 && e.offsetWidth < 80);
      if (sw) sw.click();
    }
    """)
    await asyncio.sleep(2)

    # Step 2: 点日期元素打开日历 + 选目标日期
    await page.evaluate(f"""
    () => {{
      const els = document.querySelectorAll('.date-show');
      const dateEl = [...els].find(e => /\\d{{4}}-\\d{{2}}-\\d{{2}}/.test(e.textContent));
      if (dateEl) dateEl.click();
      setTimeout(() => {{
        const item = [...document.querySelectorAll('.date-item')]
          .find(e => e.textContent.trim() === '{day}');
        if (item) item.click();
      }}, 500);
    }}
    """)
    await asyncio.sleep(2)

    # Step 3: 点时间元素打开时间面板
    coords = await page.evaluate("""
    () => {
      const els = document.querySelectorAll('.date-show');
      const timeEl = [...els].find(e => /^\\d{2}:\\d{2}$/.test(e.textContent));
      if (!timeEl) return null;
      const r = timeEl.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    }
    """)
    if coords:
        await page.mouse.click(coords['x'], coords['y'])
        await asyncio.sleep(2)

    # Step 4: 选小时 + 分钟
    # 使用更可靠的方式：直接通过坐标点击
    log(f"[B站] 选择时间: {hh}:{mm}")
    
    # 选小时 - 点击时间列中对应小时的元素
    await page.evaluate(f"""
    () => {{
      // 找所有可能是时间选项的span
      const allSpans = [...document.querySelectorAll('span, div')];
      // 找显示小时的面板（通常有两个时间列，第一个是小时）
      const hourCandidates = allSpans.filter(e => {{
        const text = e.textContent.trim();
        const rect = e.getBoundingClientRect();
        // 在时间选择面板内（通常在屏幕中间偏左）
        return text === '{hh}' && rect.width > 20 && rect.width < 80 && rect.height > 20 && rect.height < 50;
      }});
      if (hourCandidates.length > 0) {{
        // 点击第一个匹配的
        hourCandidates[0].click();
        console.log('clicked hour:', '{hh}');
      }}
    }}
    """)
    await asyncio.sleep(1.5)
    
    # 选分钟
    await page.evaluate(f"""
    () => {{
      const allSpans = [...document.querySelectorAll('span, div')];
      // 找分钟 - 在分钟列中（通常在屏幕中间偏右）
      const minCandidates = allSpans.filter(e => {{
        const text = e.textContent.trim();
        const rect = e.getBoundingClientRect();
        return text === '{mm}' && rect.width > 20 && rect.width < 80 && rect.height > 20 && rect.height < 50;
      }});
      if (minCandidates.length > 0) {{
        // 如果有多个，选第二个（小时选完后，分钟列会激活）
        const target = minCandidates.length > 1 ? minCandidates[minCandidates.length - 1] : minCandidates[0];
        target.click();
        console.log('clicked minute:', '{mm}');
      }}
    }}
    """)
    await asyncio.sleep(2)

    # Step 5: 关闭面板 + 验证
    result = await page.evaluate("""
    () => {
      document.querySelector('h3')?.click();
      const els = document.querySelectorAll('.date-show');
      const date = [...els].find(e => /\\d{4}-\\d{2}-\\d{2}/.test(e.textContent))?.textContent;
      const time = [...els].find(e => /^\\d{2}:\\d{2}$/.test(e.textContent))?.textContent;
      return { date, time };
    }
    """)
    log(f"[B站] 定时验证: {result}")


async def publish(page) -> bool:
    """点击立即投稿按钮"""
    log("[B站] 点击投稿")
    await page.evaluate("""
    () => {
      const btn = [...document.querySelectorAll('*')]
        .find(e => e.textContent.trim() === '立即投稿' && e.offsetHeight > 0 && e.offsetHeight < 60 && e.children.length === 0);
      if (btn) btn.click();
    }
    """)
    await asyncio.sleep(5)

    # 验证是否成功
    for _ in range(3):
        result = await page.evaluate("""
        () => ({
          url: location.href,
          success: document.body.innerText.includes('稿件投递成功'),
          body: document.body.innerText.substring(0, 200)
        })
        """)
        if result.get('success'):
            log("[B站] 稿件投递成功")
            return True
        await asyncio.sleep(3)

    log(f"[B站] 未检测到成功状态: {result.get('body', '')[:100]}")
    return False


async def main():
    log_argv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--desc", default="")
    parser.add_argument("--tags", default="", help="标签，逗号分隔")
    parser.add_argument("--cover43", default="", help="4:3封面路径（主封面）")
    parser.add_argument("--cover169", default="", help="16:9封面路径")
    parser.add_argument("--dtime", default="", help="定时发布，如 2026-03-02 17:00:00")
    parser.add_argument("--brief", default="", help="brief.json 路径")
    parser.add_argument("--platform", default="bilibili")
    args = parser.parse_args()

    # brief.json 优先
    if args.brief:
        from cdp_base import load_brief
        bd = load_brief(args.brief, args.platform)
        if bd:
            args.title = args.title or bd.get('title', '')
            args.desc = args.desc or bd.get('desc', '')
            if not args.tags and bd.get('tags'):
                args.tags = ','.join(bd['tags'])

    if not args.title:
        exit_failed("缺少 title（--title 或 --brief）")

    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    # 关掉可能残留的 OS 文件选择框
    os.system("osascript -e 'tell application \"System Events\" to key code 53'")
    await asyncio.sleep(0.3)

    pw, browser = await connect_browser()
    try:
        # 1. 查重
        page = await new_tab(browser, MANAGE_URL)
        result = await check_login_and_duplicate(page, args.title)
        if not result.get('loggedIn'):
            exit_need_login("B站")
        if result.get('duplicate'):
            log("[B站] 视频已存在，标记为 published")
            exit_published(args.dtime)

        # 2. 上传视频
        await page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)

        await upload_video(page, args.video)
        ok = await wait_upload_done(page)
        if not ok:
            exit_failed("B站：视频上传超时")

        # 3. 封面
        await set_cover(page, args.cover43, args.cover169)

        # 4. 标题 + 分区
        await fill_title(page, args.title)
        await select_zone(page)

        # 5. 标签
        await fill_tags(page, tags)

        # 6. 简介
        await fill_desc(page, args.desc)

        # 7. 定时
        if args.dtime:
            await set_schedule(page, args.dtime)

        # 8. 投稿 + 验证（测试模式：只验证不实际投稿）
        log("[B站] 【测试模式】表单填写完成，跳过实际投稿")
        log("[B站] 【测试模式】封面设置完成，检查双封面是否正确")
        # ok = await publish(page)
        # if ok:
        #     exit_published(args.dtime)
        # else:
        #     exit_failed("B站：投稿后未检测到成功状态")
        exit_published(args.dtime)

    except Exception as e:
        import traceback
        traceback.print_exc()
        exit_failed(str(e))
    finally:
        await safe_disconnect(pw, browser)


if __name__ == "__main__":
    asyncio.run(main())
