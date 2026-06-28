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
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from cdp_base import log_argv, connect_browser, safe_disconnect, new_tab, log, exit_published, exit_need_login, exit_failed

MANAGE_URL = "https://member.bilibili.com/platform/upload-manager/article"
UPLOAD_URL = "https://member.bilibili.com/platform/upload/video/frame"


async def set_video_file_via_cdp(page, video_path: str) -> bool:
    """通过 CDP 直接传浏览器本机路径，避免 Playwright CDP 模式 50MB 文件传输限制。"""
    token = f"omc-video-input-{int(time.time() * 1000)}"
    target = await page.evaluate("""
    (token) => {
      const preferred = document.querySelector('.bcc-upload-wrapper input[type=file]');
      const inputs = [...document.querySelectorAll('input[type=file]')];
      const byAccept = inputs.find(e => {
        const accept = e.getAttribute('accept') || '';
        return accept.includes('.mp4') || accept.includes('video');
      });
      const input = preferred || byAccept;
      if (!input) return { success: false, error: '视频上传 input 未找到' };
      input.setAttribute('data-omc-file-input-token', token);
      return { success: true, accept: input.getAttribute('accept') || '', hidden: getComputedStyle(input).display === 'none' };
    }
    """, token)
    if not target.get('success'):
        log(f"[B站] CDP 设置视频文件失败: {target.get('error')}")
        return False

    client = await page.context.new_cdp_session(page)
    doc = await client.send('DOM.getDocument', {'depth': -1, 'pierce': True})
    node = await client.send('DOM.querySelector', {
        'nodeId': doc['root']['nodeId'],
        'selector': f'input[data-omc-file-input-token="{token}"]',
    })
    node_id = node.get('nodeId')
    if not node_id:
        log("[B站] CDP 设置视频文件失败: input nodeId 未找到")
        return False

    await client.send('DOM.setFileInputFiles', {'nodeId': node_id, 'files': [video_path]})
    verified = await page.evaluate("""
    (token) => {
      const input = document.querySelector(`input[data-omc-file-input-token="${token}"]`);
      const file = input?.files?.[0];
      // DOM.setFileInputFiles 已原生派发 input/change 事件；不能再手动派发，
      // 否则 B站上传组件收到两次 change → 同一文件上传两遍 → 重复分P
      if (input) input.removeAttribute('data-omc-file-input-token');
      return file ? { success: true, name: file.name, size: file.size } : { success: false };
    }
    """, token)
    if verified.get('success'):
        log(f"[B站] 视频文件已选择(CDP): {verified.get('name')} {verified.get('size')} bytes")
        return True
    log("[B站] CDP 设置视频文件失败: files 为空")
    return False


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
    """上传视频文件"""
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

    if await set_video_file_via_cdp(page, video_path):
        return

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
            # 先检查按钮是否存在，避免在 expect_file_chooser 内部提前 return 导致 15s 超时
            has_btn = await page.evaluate(f"""
            () => {{
              const is43 = '{section_title}' === '4:3';
              const titleText = is43 ? '首页推荐封面（4:3）' : '个人空间封面（16:9）';
              const title = [...document.querySelectorAll('*')]
                .find(e => e.textContent.includes(titleText));
              if (!title) return false;
              const titleRect = title.getBoundingClientRect();
              const buttons = [...document.querySelectorAll('*')].filter(e => {{
                if (e.textContent.trim() !== '上传封面') return false;
                const rect = e.getBoundingClientRect();
                return rect.top > titleRect.top && rect.top < titleRect.bottom + 300;
              }});
              return buttons.length > 0;
            }}
            """)
            if not has_btn:
                log(f"[B站] {label}：未找到对应区域的上传按钮")
                return False

            async with page.expect_file_chooser(timeout=15000) as fc_info:
                # 直接点击唯一的"上传封面"按钮（tab 已切换，只有一个可见）
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
            await asyncio.sleep(8)
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

    # 上传 16:9 封面 - 点击 span.text "个人空间封面（16:9）" 切换画布
    if cover169_path and os.path.exists(cover169_path):
        log("[B站] 切换到16:9封面区域")
        switched = await page.evaluate("""
        () => {
          // 点击 span.text 包含"个人空间封面"的元素（实测有效）
          const span = [...document.querySelectorAll('span.text')]
            .find(e => e.textContent.includes('个人空间封面'));
          if (span) { span.click(); return 'span.text'; }
          // 备选: 找任何包含16:9的标题 span
          const fallback = [...document.querySelectorAll('span')]
            .find(e => e.textContent.includes('个人空间') && e.textContent.includes('16'));
          if (fallback) { fallback.click(); return 'fallback span'; }
          return null;
        }
        """)
        log(f"[B站] 16:9切换方式: {switched}")
        await asyncio.sleep(3)
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
    - 先打开定时开关
    - 按完整目标日期 YYYY-MM-DD 选择日期（不能只按日号盲点）
    - 设置小时/分钟
    - 强校验日期和时间，不一致直接失败，避免误当定时成功
    注意：B站要求定时 >= 当前时间 + 2小时
    """
    dt = datetime.strptime(dtime, "%Y-%m-%d %H:%M:%S")
    target_date = dt.strftime("%Y-%m-%d")
    day = str(dt.day)
    hh = f"{dt.hour:02d}"
    mm = f"{dt.minute:02d}"
    target_time = f"{hh}:{mm}"

    log(f"[B站] 设置定时: {dtime}")

    opened = await page.evaluate("""
    () => {
      const h3 = [...document.querySelectorAll('h3')].find(e => e.textContent.trim() === '定时发布');
      if (!h3) return { success: false, error: '定时发布标题未找到' };
      const section = h3.closest('[class*=item]') || h3.parentElement?.parentElement || h3.parentElement;
      const sw = section?.querySelector('[role=switch]')
        || [...(section?.querySelectorAll('*') || [])].find(e => String(e.className || '').includes('switch') && e.offsetWidth > 20 && e.offsetWidth < 100);
      if (!sw) return { success: false, error: '定时发布开关未找到' };
      const cls = String(sw.className || '');
      const aria = sw.getAttribute('aria-checked');
      const active = aria === 'true' || /checked|active|open/i.test(cls);
      if (!active) sw.click();
      return { success: true, activeBefore: active, className: cls, aria };
    }
    """)
    if not opened.get('success'):
        exit_failed(f"B站：定时开关打开失败：{opened.get('error')}")
    await asyncio.sleep(2)

    date_clicked = False
    last_diag = None
    for attempt in range(8):
        diag = await page.evaluate("""
        () => {
          const visible = e => {
            const r = e.getBoundingClientRect();
            const st = getComputedStyle(e);
            return (r.width || r.height || e.getClientRects().length) && st.display !== 'none' && st.visibility !== 'hidden';
          };
          const navOpen = [...document.querySelectorAll('.date-picker-nav-wrp')]
            .find(e => visible(e) && e.querySelector('.date-picker-nav-title'));
          if (navOpen) return { success: true, alreadyOpen: true, current: navOpen.innerText?.trim() || '' };
          const h3 = [...document.querySelectorAll('h3')].find(e => e.textContent.trim() === '定时发布');
          const section = h3?.closest('[class*=item]') || h3?.parentElement?.parentElement || h3?.parentElement;
          if (!section) return { success: false, error: '定时发布区域未找到' };
          const els = [...section.querySelectorAll('.date-show')];
          const dateEl = els.find(e => /\d{4}-\d{2}-\d{2}/.test(e.textContent || ''));
          if (!dateEl) return { success: false, error: '定时发布区域内日期显示元素未找到', texts: els.map(e => e.textContent?.trim()) };
          dateEl.scrollIntoView({ block: 'center' });
          dateEl.click();
          return { success: true, current: dateEl.textContent?.trim() || '' };
        }
        """)
        if not diag.get('success'):
            exit_failed(f"B站：打开日期面板失败：{diag.get('error')}")
        await asyncio.sleep(0.8)

        clicked = await page.evaluate("""
        ({ targetDate, day }) => {
          const visible = (e) => {
            const r = e.getBoundingClientRect();
            const st = getComputedStyle(e);
            return (r.width || r.height || e.getClientRects().length) && st.display !== 'none' && st.visibility !== 'hidden';
          };
          const disabled = (e) => {
            const cls = String(e.className || '').toLowerCase();
            const aria = e.getAttribute('aria-disabled');
            return aria === 'true' || /disabled|disable/.test(cls);
          };
          const all = [...document.querySelectorAll('.date-picker-body-item, .date-item, [role=gridcell], td, li')]
            .filter(e => visible(e) && !disabled(e));

          const exact = all.find(e => {
            const attrs = [
              e.getAttribute('title'), e.getAttribute('aria-label'), e.getAttribute('data-date'),
              e.getAttribute('data-value'), e.getAttribute('value'), e.textContent
            ].filter(Boolean).map(x => String(x));
            return attrs.some(x => x.includes(targetDate));
          });
          if (exact) { exact.click(); return { success: true, mode: 'exact', text: exact.textContent?.trim() || '', cls: String(exact.className || '') }; }

          const dayItems = all.filter(e => (e.textContent || '').trim() === String(day));
          const currentMonthItems = dayItems.filter(e => {
            const cls = String(e.className || '').toLowerCase();
            const aria = String(e.getAttribute('aria-label') || e.getAttribute('title') || '');
            if (/prev|previous|next|other|outside/.test(cls)) return false;
            if (aria && !aria.includes(targetDate) && /\d{4}[-年]/.test(aria)) return false;
            const opacity = Number(getComputedStyle(e).opacity || 1);
            return opacity > 0.5;
          });
          const target = currentMonthItems[0] || dayItems[0];
          if (target) { target.click(); return { success: true, mode: 'day-fallback', text: target.textContent?.trim() || '', cls: String(target.className || ''), candidates: dayItems.length }; }

          return {
            success: false,
            error: '目标日期项未找到',
            sample: all.slice(0, 40).map(e => ({ text: (e.textContent || '').trim().slice(0, 30), cls: String(e.className || '').slice(0,80), title: e.getAttribute('title'), aria: e.getAttribute('aria-label') })).filter(x => x.text || x.title || x.aria)
          };
        }
        """, {"targetDate": target_date, "day": day})
        last_diag = clicked
        if clicked.get('success'):
            date_clicked = True
            log(f"[B站] 日期选择点击: {clicked}")
            await asyncio.sleep(1.5)
            break

        moved = await page.evaluate("""
        () => {
          const visible = e => {
            const r = e.getBoundingClientRect();
            const st = getComputedStyle(e);
            return (r.width || r.height || e.getClientRects().length) && st.display !== 'none' && st.visibility !== 'hidden';
          };
          const nav = [...document.querySelectorAll('.date-picker-nav-wrp')]
            .find(e => visible(e) && e.querySelector('.date-picker-nav-title'));
          if (!nav) return { success: false, error: '日期导航未找到' };
          // B站当前控件：单右箭头 class=next-btn-day 是“下个月”；双右箭头 next-btn-month 是“下一年”。
          const next = nav.querySelector('.next-btn-day:not(.date-select-disabled)')
            || nav.querySelector('svg[class*=next-btn-day]:not(.date-select-disabled)');
          if (next) { next.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window })); return { success: true, text: next.textContent?.trim() || '', cls: String(next.getAttribute('class') || '') }; }
          return { success: false, error: '下个月按钮未找到/不可点', nav: nav.outerHTML.slice(0, 500) };
        }
        """)
        log(f"[B站] 日期未命中，尝试翻月 attempt={attempt+1}: {moved}")
        if not moved.get('success'):
            break
        await asyncio.sleep(1)

    if not date_clicked:
        exit_failed(f"B站：目标日期 {target_date} 未找到/未点击，诊断={last_diag}")

    coords = await page.evaluate("""
    () => {
      const h3 = [...document.querySelectorAll('h3')].find(e => e.textContent.trim() === '定时发布');
      const section = h3?.closest('[class*=item]') || h3?.parentElement?.parentElement || h3?.parentElement;
      const els = section ? section.querySelectorAll('.date-show') : [];
      const timeEl = [...els].find(e => /^\d{2}:\d{2}$/.test((e.textContent || '').trim()));
      if (!timeEl) return null;
      timeEl.scrollIntoView({ block: 'center' });
      const r = timeEl.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    }
    """)
    if coords:
        await page.mouse.click(coords['x'], coords['y'])
        log(f"[B站] 点击时间元素打开面板: ({coords['x']:.0f}, {coords['y']:.0f})")
        await asyncio.sleep(2)
    else:
        exit_failed("B站：未找到时间元素")

    log(f"[B站] 选择时间: {hh}:{mm}")

    hour_coords = await page.evaluate(f"""
    () => {{
      const wrps = document.querySelectorAll('.time-picker-panel-select-wrp');
      if (!wrps.length) return null;
      const hourCol = wrps[0];
      const target = [...hourCol.querySelectorAll('.time-picker-panel-select-item')]
        .find(e => e.textContent.trim() === '{hh}');
      if (!target) return null;
      target.scrollIntoView({{ block: 'center' }});
      const r = target.getBoundingClientRect();
      return {{ x: r.x + r.width / 2, y: r.y + r.height / 2 }};
    }}
    """)
    if hour_coords:
        await page.mouse.click(hour_coords['x'], hour_coords['y'])
        log(f"[B站] 点击小时: {hh}")
    else:
        exit_failed(f"B站：小时选项 {hh} 未找到")
    await asyncio.sleep(1)

    min_coords = await page.evaluate(f"""
    () => {{
      const wrps = document.querySelectorAll('.time-picker-panel-select-wrp');
      if (wrps.length < 2) return null;
      const minCol = wrps[1];
      const target = [...minCol.querySelectorAll('.time-picker-panel-select-item')]
        .find(e => e.textContent.trim() === '{mm}');
      if (!target) return null;
      target.scrollIntoView({{ block: 'center' }});
      const r = target.getBoundingClientRect();
      return {{ x: r.x + r.width / 2, y: r.y + r.height / 2 }};
    }}
    """)
    if min_coords:
        await page.mouse.click(min_coords['x'], min_coords['y'])
        log(f"[B站] 点击分钟: {mm}")
    else:
        exit_failed(f"B站：分钟选项 {mm} 未找到")
    await asyncio.sleep(2)

    result = await page.evaluate("""
    () => {
      const h3 = [...document.querySelectorAll('h3')].find(e => e.textContent.trim() === '定时发布');
      h3?.click();
      const section = h3?.closest('[class*=item]') || h3?.parentElement?.parentElement || h3?.parentElement;
      const els = section ? section.querySelectorAll('.date-show') : [];
      const date = [...els].find(e => /\d{4}-\d{2}-\d{2}/.test(e.textContent || ''))?.textContent?.trim();
      const time = [...els].find(e => /^\d{2}:\d{2}$/.test((e.textContent || '').trim()))?.textContent?.trim();
      return { date, time };
    }
    """)
    log(f"[B站] 定时验证: {result}")
    if result.get('date') != target_date or result.get('time') != target_time:
        exit_failed(f"B站：定时校验失败，目标={target_date} {target_time}，实际={result}")

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

    # 验证是否成功
    last = {}
    for _ in range(8):
        await asyncio.sleep(5)
        result = await page.evaluate("""
        () => ({
          url: location.href,
          success: document.body.innerText.includes('稿件投递成功')
            || document.body.innerText.includes('投稿成功'),
          leftUploadForm: !location.href.includes('/platform/upload/video/frame')
            && ![...document.querySelectorAll('*')]
              .some(e => e.textContent.trim() === '立即投稿'
                && e.offsetHeight > 0 && e.offsetHeight < 60),
          body: document.body.innerText.substring(0, 300)
        })
        """)
        last = result
        if result.get('success'):
            log("[B站] 稿件投递成功")
            return True
        if result.get('leftUploadForm'):
            log(f"[B站] 投稿后已离开上传表单: {result.get('url')}")
            return True

    log(f"[B站] 未检测到成功状态: {last.get('body', '')[:160]}")
    return False


async def select_creation_declaration(page, option_text: str = "含AI生成内容"):
    """选择 B站创作声明。新版页面下拉框在标题的兄弟节点里，不能从 h3 父级向下找。"""
    log(f"[B站] 选择创作声明: {option_text}")
    opened = await page.evaluate("""
    (optionText) => {
      const title = [...document.querySelectorAll('h3')]
        .find(e => e.textContent.trim() === '创作声明');
      const root = document.querySelector('.creation-statement-container')
        || title?.closest('.form-item')
        || title?.closest('.statement-main');
      if (!root) return { success: false, error: '创作声明容器未找到' };

      const input = root.querySelector('.bcc-select-input-inner, input[placeholder*="创作声明"]');
      if (!input) {
        return {
          success: false,
          error: '创作声明下拉框未找到',
          html: root.outerHTML.slice(0, 500)
        };
      }

      if ((input.value || '').trim() === optionText) {
        return { success: true, already: true, value: input.value };
      }

      input.scrollIntoView({ block: 'center' });
      input.click();
      return { success: true, opened: true };
    }
    """, option_text)
    if not opened.get('success'):
        exit_failed(f"B站：创作声明打开失败：{opened.get('error')}")
    if opened.get('already'):
        log(f"[B站] 创作声明已选择: {opened.get('value')}")
        return

    await asyncio.sleep(1)
    selected = await page.evaluate("""
    (optionText) => {
      const root = document.querySelector('.creation-statement-container');
      if (!root) return { success: false, error: '创作声明容器未找到' };

      const options = [...root.querySelectorAll('.bcc-option')]
        .filter(e => {
          const r = e.getBoundingClientRect();
          return (r.width || r.height || e.getClientRects().length)
            && getComputedStyle(e).display !== 'none';
        });
      const option = options.find(e => e.textContent.trim() === optionText);
      if (!option) {
        return {
          success: false,
          error: '创作声明选项未找到',
          options: options.map(e => e.textContent.trim()).filter(Boolean)
        };
      }
      option.click();
      return { success: true, clicked: option.textContent.trim() };
    }
    """, option_text)
    if not selected.get('success'):
        exit_failed(f"B站：创作声明选择失败：{selected.get('error')} options={selected.get('options')}")

    await asyncio.sleep(1)
    verified = await page.evaluate("""
    (optionText) => {
      const input = document
        .querySelector('.creation-statement-container .bcc-select-input-inner, .creation-statement-container input[placeholder*="创作声明"]');
      const value = (input?.value || '').trim();
      return { success: value === optionText, value };
    }
    """, option_text)
    if not verified.get('success'):
        exit_failed(f"B站：创作声明未生效，当前值={verified.get('value')}")
    log(f"[B站] 创作声明已选择: {verified.get('value')}")


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
        # 1. 查重（new_tab 可能因 B站重定向触发 ERR_ABORTED，加重试）
        try:
            page = await new_tab(browser, MANAGE_URL)
        except Exception as e:
            log(f"[B站] 首次导航失败，重试: {e}")
            await asyncio.sleep(2)
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await ctx.new_page()
            await page.goto(MANAGE_URL, wait_until="load", timeout=30000)
            await asyncio.sleep(3)
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

        # 7. 创作声明（必选）
        await select_creation_declaration(page)

        # 8. 定时
        if args.dtime:
            await set_schedule(page, args.dtime)

        # 9. 投稿 + 验证
        ok = await publish(page)
        if ok:
            exit_published(args.dtime)
        else:
            exit_failed("B站：投稿后未检测到成功状态")

    except Exception as e:
        import traceback
        traceback.print_exc()
        exit_failed(str(e))
    finally:
        await safe_disconnect(pw, browser)


if __name__ == "__main__":
    asyncio.run(main())
