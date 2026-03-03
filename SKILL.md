---
name: publisher-api
description: 视频发布 API/CDP 版：B站用 API（0 tokens），其他平台用 CDP 固定脚本。稳定、模型无关。当 orchestrator 指定 publisher-api-skill 时触发。
metadata: {"openclaw":{"os":["darwin"],"emoji":"🚀"}}
---

# Publisher API Skill（低 token 版）

> **模型角色：调度者** — 你只负责：读任务 → 组参数 → exec 调脚本 → 上报结果
> **不操作浏览器，不调 snapshot/screenshot，不做视觉判断**

---

## 脚本目录

```
/Users/niuone/.openclaw/skills/publisher-api-skill/scripts/
├── bili_upload.py      # B站 API 上传（0 tokens，纯 HTTP）
├── douyin_upload.py    # 抖音 CDP 脚本
├── ks_upload.py        # 快手 CDP 脚本
├── weixin_upload.py    # 视频号 CDP 脚本（Shadow DOM）
└── cdp_base.py         # CDP 公共基础库
```

**Python 解释器**: `cd /Users/niuone/code/social-auto-upload && python3`
（必须在 social-auto-upload 目录下运行，否则 biliup 无法导入）

---

## 平台配置

| 平台 | 脚本 | 视频文件 | Cookie |
|------|------|---------|--------|
| bilibili | bili_upload.py | landscape（横屏） | `/tmp/sau-test/cookies/bilibili_uploader/account.json` |
| douyin | douyin_upload.py | portrait（竖屏） | 复用 OpenClaw 浏览器登录态 |
| kuaishou | ks_upload.py | portrait（竖屏） | 复用 OpenClaw 浏览器登录态 |
| weixin-channels | weixin_upload.py | portrait（竖屏） | 复用 OpenClaw 浏览器登录态 |

---

## 执行流程

### 1. 读取任务

```bash
PENDING=$(curl -s 'http://192.168.50.40:3456/api/publish/pending?key=pk_publisher_31d4632df15a11c58c2977f4&limit=1')
```

### 2. 下载素材（同 fengmang-dispatcher-v2.md Step 4）

```bash
SLUG="{slug}"
STAGING=~/Media/staging/$SLUG
mkdir -p "$STAGING" && cd "$STAGING"
BASE="http://192.168.50.40:3456/files/{project_path去掉/Users/zhengweirong/clawd/锋芒AI/前缀}"

[ -f "投稿文案.md" ] || curl -sf -O "$BASE/投稿文案.md"
[ -f "portrait.mp4" ] && [ $(stat -f%z portrait.mp4) -gt 1000000 ] || curl -sf -o portrait.mp4 "$BASE/{video_path}"
[ -f "landscape.mp4" ] && [ $(stat -f%z landscape.mp4) -gt 1000000 ] || curl -sf -o landscape.mp4 "$BASE/{landscape_path}" || true
for cover in cover-3x4.png cover-4x3.png cover-16x9.png; do
  [ -f "$cover" ] || curl -sf -O "$BASE/$cover" || true
done
```

### 3. 解析文案

从 `投稿文案.md` 提取各平台文案（标题、简介、话题），拼成脚本参数。

### 4. 按平台执行脚本

**B站：**
```bash
cd /Users/niuone/code/social-auto-upload
python3 /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/bili_upload.py \
  --cookie /tmp/sau-test/cookies/bilibili_uploader/account.json \
  --video "$STAGING/landscape.mp4" \
  --title "B站标题" \
  --desc "B站简介" \
  --tags "标签1,标签2" \
  --cover43 "$STAGING/cover-4x3.png" \
  --cover169 "$STAGING/cover-16x9.png" \
  --zone 1011 \
  --dtime "2026-03-02T12:00:00"
```

**抖音：**
```bash
cd /Users/niuone/code/social-auto-upload
python3 /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/douyin_upload.py \
  --video "$STAGING/portrait.mp4" \
  --title "抖音标题" \
  --desc "简介内容\n#话题1 #话题2" \
  --cover34 "$STAGING/cover-3x4.png" \
  --cover43 "$STAGING/cover-4x3.png" \
  --dtime "2026-03-02 12:00:00"
```

**快手：**
```bash
cd /Users/niuone/code/social-auto-upload
python3 /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/ks_upload.py \
  --video "$STAGING/portrait.mp4" \
  --desc "内容+#话题" \
  --cover34 "$STAGING/cover-3x4.png" \
  --dtime "2026-03-02 12:00:00" \
  --dedup-kw "内容第一句"
```

**视频号：**
```bash
cd /Users/niuone/code/social-auto-upload
python3 /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/weixin_upload.py \
  --video "$STAGING/portrait.mp4" \
  --short-title "短标题6-16字" \
  --desc "描述+#话题" \
  --cover34 "$STAGING/cover-3x4.png" \
  --dtime "2026-03-02 12:00:00"
```

### 5. 解析脚本输出

脚本 stdout 最后一行为状态：

| 输出 | 含义 | 操作 |
|------|------|------|
| `PUBLISHED [scheduled_time=...]` | 成功 | 上报 published |
| `NEED_LOGIN ...` | 需要扫码 | 上报 login_required，通知用户，中断 |
| `FAILED error=...` | 失败 | 上报 failed，继续下一平台 |
| 脚本 exit code ≠ 0 | 异常 | 视为 FAILED |

### 6. 上报 Hub

```bash
# 成功
curl -s -X POST 'http://192.168.50.40:3456/api/publish/complete?key=pk_publisher_31d4632df15a11c58c2977f4' \
  -H 'Content-Type: application/json' \
  -d '{"log_id": LOG_ID, "status": "published", "scheduled_time": "YYYY-MM-DD HH:MM:SS"}'

# 失败
curl -s -X POST 'http://192.168.50.40:3456/api/publish/complete?key=pk_publisher_31d4632df15a11c58c2977f4' \
  -H 'Content-Type: application/json' \
  -d '{"log_id": LOG_ID, "status": "failed", "error": "错误描述"}'

# 需要登录
curl -s -X POST 'http://192.168.50.40:3456/api/publish/complete?key=pk_publisher_31d4632df15a11c58c2977f4' \
  -H 'Content-Type: application/json' \
  -d '{"log_id": LOG_ID, "status": "login_required", "error": "需要扫码登录"}'
```

---

## 注意事项

1. **抖音/快手/视频号**：OpenClaw 浏览器必须已启动，脚本复用已有 tab 导航，不会新开 tab
2. **B站**：不需要浏览器，纯 API 调用
3. **视频号 Shadow DOM**：weixin_upload.py 已处理，无需额外操作
4. **定时时间格式**：bili 用 ISO8601（`T`分隔），其他用空格（`YYYY-MM-DD HH:MM:SS`）
5. **平台顺序**：bilibili → kuaishou → weixin-channels → douyin
6. **Claim 和上报**：与 fengmang-dispatcher-v2.md 相同，orchestrator 负责

---

## 快速测试（单平台）

```bash
# 测试 B站上传
cd /Users/niuone/code/social-auto-upload
python3 /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/bili_upload.py \
  --cookie /tmp/sau-test/cookies/bilibili_uploader/account.json \
  --video ~/Media/staging/0228-deer-flow/landscape.mp4 \
  --title "测试标题" --desc "测试简介" --tags "测试"
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `scripts/check_params.py` | 发布参数验证脚本，在执行发布前必须先通过验证 |
| `scripts/bili_upload.py` | B站发布脚本 |
| `scripts/ks_upload.py` | 快手发布脚本 |
| `scripts/weixin_upload.py` | 视频号发布脚本 |
| `scripts/douyin_upload.py` | 抖音发布脚本 |
| `scripts/bili_stats.py` | B站数据回收 |
| `scripts/ks_stats.py` | 快手数据回收 |
| `scripts/douyin_stats.py` | 抖音数据回收 |
| `scripts/weixin_stats.py` | 视频号数据回收 |
| `docs/dispatcher-v3.md` | cron 调度器提示词文档 |
