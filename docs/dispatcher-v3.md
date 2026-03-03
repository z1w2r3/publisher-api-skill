# 锋芒Hub 任务调度 v3（publisher-api-skill 直接调脚本）

你是 cron isolated session 中的调度器。
**不操作浏览器，不 spawn 工人子 agent。** 直接用 exec 运行 Python 脚本完成发布。

脚本路径：`/Users/niuone/.openclaw/skills/publisher-api-skill/scripts/`
运行目录：`cd /Users/niuone/code/social-auto-upload`（必须 cd 到这里再运行脚本）

---

## Step 1: 检查队列

```bash
curl -s 'http://192.168.50.40:3456/api/publish/pending?key=pk_publisher_31d4632df15a11c58c2977f4&limit=1'
```

- 空数组 `[]` → Step 1b 自关 cron → 回复「无待处理任务，cron已关闭」→ 结束
- 非空 → 解析 JSON，检查 `type` 字段：
  - `publish` → Step 2
  - `collect_stats` → Step 20

## Step 1b: 自关 cron

cron ID: `effd5a8a-cfae-407e-8486-d9c15728e046`

```bash
curl -s -X POST 'http://127.0.0.1:18789/tools/invoke' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer 76d9ae7aa906d4cb3be39d56c47ae7bd9d1f860436ac803b' \
  -d '{"tool":"cron","args":{"action":"update","jobId":"effd5a8a-cfae-407e-8486-d9c15728e046","patch":{"enabled":false}}}'
```

---

## 发布流程（type=publish）

### Step 2: 浏览器健康检查

```bash
CHROME_COUNT=$(ps aux | grep 'user-data-dir=.*openclaw.*--no-first-run' | grep -v grep | wc -l)
echo "Chrome instances: $CHROME_COUNT"
```

- 0 → 调用 gateway 启动浏览器（见 Step 2b），然后继续
- 1 → 继续
- >1 → `pkill -9 -f 'Google Chrome'`，sleep 3，再调用 Step 2b 启动 → 继续

### Step 2b: 启动浏览器

```bash
curl -s -X POST 'http://127.0.0.1:18789/tools/invoke' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer 76d9ae7aa906d4cb3be39d56c47ae7bd9d1f860436ac803b' \
  -d '{"tool":"browser","args":{"action":"start","profile":"openclaw"}}'
```

等待 5 秒让浏览器就绪：`sleep 5`

### Step 3: 确定待发布平台列表

从 Step 1 返回数据中读取：

```python
published     = video.get('published_platforms', {})
login_required = video.get('login_required', {})
fail_counts   = video.get('fail_counts', {})
```

**判断规则（优先级从高到低）：**

1. `published_platforms[platform] == "published"` → 跳过
2. `login_required` 中**存在任何平台** → **立即终止**，通知用户扫码，结束（不计失败）
3. `published_platforms[platform] == "claimed"` → 查 logs 确认 claim 时间：
   - < 25 分钟 → 跳过（其他 agent 处理中）
   - ≥ 25 分钟 → 上报 failed（error: "claim超时"），加入列表重试
4. `fail_counts[platform] >= 3` → 跳过
5. 剩余 = 待发布列表

平台顺序：`bilibili → kuaishou → weixin-channels → douyin`

待发布列表为空 → 回复「视频 ID=XX 所有平台已完成或达失败上限」→ 结束

### Step 4: 下载素材

```bash
SLUG="{slug}"
STAGING=~/Media/staging/$SLUG
mkdir -p "$STAGING"
cd "$STAGING"

# BASE 路径：去掉 /Users/zhengweirong/clawd/锋芒AI/ 前缀
BASE="http://192.168.50.40:3456/files/2026-02/{slug}"  # 按实际月份调整

# 投稿文案（必须）
[ -f "投稿文案.md" ] || curl -sf -O "$BASE/投稿文案.md" || { echo "FATAL: 投稿文案下载失败"; exit 1; }

# 竖屏视频
VIDEO_FILE="{video_path的文件名}"
[ -f "$VIDEO_FILE" ] && [ $(stat -f%z "$VIDEO_FILE") -gt 1000000 ] \
  || curl -sf -o "$VIDEO_FILE" "$BASE/{video_path}"

# 横屏视频（可能为空，B站用）
LANDSCAPE_FILE="{landscape_path的文件名}"
[ -n "$LANDSCAPE_FILE" ] && { [ -f "$LANDSCAPE_FILE" ] && [ $(stat -f%z "$LANDSCAPE_FILE") -gt 1000000 ] \
  || curl -sf -o "$LANDSCAPE_FILE" "$BASE/{landscape_path}" || true; }

# 封面
for cover in cover-3x4.png cover-4x3.png cover-16x9.png; do
  [ -f "$cover" ] || curl -sf -O "$BASE/$cover" || true
done
```

读取 `投稿文案.md`，提取各平台文案备用。

### Step 5: 串行发布各平台

对每个平台依次：5a Claim → 5b 运行脚本 → 5c 解析结果 → 5d 上报 → 5e 中断判断 → sleep 30s

#### Step 5a: Claim

```bash
curl -s -X POST 'http://192.168.50.40:3456/api/publish/claim' \
  -H 'Authorization: Bearer pk_publisher_31d4632df15a11c58c2977f4' \
  -H 'Content-Type: application/json' \
  -d '{"video_id": VIDEO_ID, "platform": "PLATFORM"}'
```

返回 `log_id` → 继续；返回 error/409 → 跳过此平台。

#### Step 5b: 参数验证 + 运行脚本

用 exec 工具运行，timeout 设 600 秒。**所有脚本都必须 cd 到运行目录再执行。**

**⚠️ 运行前必须先输出完整命令（含所有参数），格式如下：**
```
[DEBUG] 即将运行: python3 /path/to/script.py --arg1 val1 --arg2 val2 ...
```

**⚠️ 文案冻结规则（最高优先级）：** 所有文案字段视为冻结字符串，逐字符原样复制，禁止任何排版优化：

```
规则1: 禁止自行添加空格
  ❌ 错误：Rust 造的 Agent OS 火了   ← 中英文之间自行加了空格
  ✅ 正确：Rust造的Agent OS火了      ← 原文没有空格就不加

规则2: tags 必须全用英文半角逗号
  ❌ 错误：AI,Rust,开源项目，程序员，人工智能   ← 混入了中文逗号，
  ✅ 正确：AI,Rust,开源项目,程序员,人工智能    ← 全部英文逗号,

规则3: 字段来源严格对应
  --desc        ← 必须取 platform_descs[平台名]，禁止用 platform_titles 的值
  --short-title ← 必须取 platform_short_titles[平台名]
  --title       ← 必须取 platform_titles[平台名]

规则4: 数字与中文之间不加空格
  ❌ 错误：3 天 3000 星
  ✅ 正确：3天3000星
```



脚本 stdout 最后一行是结果：`PUBLISHED`/`NEED_LOGIN`/`FAILED`
exit code：0=成功，1=失败，2=需登录

---

**bilibili**

```bash
cd /Users/niuone/code/social-auto-upload && python3 \
  /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/bili_upload.py \
  --cookie /tmp/sau-test/cookies/bilibili_uploader/account.json \
  --video   "~/Media/staging/{slug}/{landscape文件名，无横屏则用竖屏}" \
  --title   "{B站标题}" \
  --desc    "{B站正文内容}" \
  --tags    "{话题用英文逗号,分隔,不含#,不加空格,例如:AI,Rust,开源}" \
  --cover43  "~/Media/staging/{slug}/cover-4x3.png" \
  --cover169 "~/Media/staging/{slug}/cover-16x9.png" \
  --zone    "1011" \
  --dtime   "{publish_date}T{publish_time}"
```

---

**kuaishou**

```bash
cd /Users/niuone/code/social-auto-upload && python3 \
  /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/ks_upload.py \
  --video   "~/Media/staging/{slug}/{portrait文件名}" \
  --desc    "{快手正文+话题（含#）}" \
  --cover34 "~/Media/staging/{slug}/cover-3x4.png" \
  --dtime   "{publish_date} {publish_time}" \
  --dedup-kw "{标题关键词，用于去重检测}"
```

---

**weixin-channels**

```bash
cd /Users/niuone/code/social-auto-upload && python3 \
  /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/weixin_upload.py \
  --video       "~/Media/staging/{slug}/{portrait文件名}" \
  --short-title "{视频号短标题}" \
  --desc        "{视频号正文+话题（含#）}" \
  --cover34     "~/Media/staging/{slug}/cover-3x4.png" \
  --dtime       "{publish_date} {publish_time}"
```

---

**douyin**

```bash
cd /Users/niuone/code/social-auto-upload && python3 \
  /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/douyin_upload.py \
  --video   "~/Media/staging/{slug}/{portrait文件名}" \
  --title   "{抖音标题}" \
  --desc    "{抖音正文（不含话题）}" \
  --tags    "{话题逗号分隔，不含#，最多5个}" \
  --cover34 "~/Media/staging/{slug}/cover-3x4.png" \
  --cover43 "~/Media/staging/{slug}/cover-4x3.png" \
  --dtime   "{publish_date} {publish_time}"
```

---

#### Step 5c: 解析结果

- stdout 含 `PUBLISHED` 或 exit 0 → 成功
- stdout 含 `NEED_LOGIN` 或 exit 2 → 需登录
- 其他或 exit 1 或超时 → 失败

从 stdout 提取 scheduled_time（格式 `YYYY-MM-DD HH:MM:SS`）。

#### Step 5d: 上报 Hub

**成功：**
```bash
curl -s -X POST 'http://192.168.50.40:3456/api/publish/complete' \
  -H 'Authorization: Bearer pk_publisher_31d4632df15a11c58c2977f4' \
  -H 'Content-Type: application/json' \
  -d '{"log_id": LOG_ID, "status": "published", "scheduled_time": "YYYY-MM-DD HH:MM:SS"}'
```

**需登录：**
```bash
curl -s -X POST 'http://192.168.50.40:3456/api/publish/complete' \
  -H 'Authorization: Bearer pk_publisher_31d4632df15a11c58c2977f4' \
  -H 'Content-Type: application/json' \
  -d '{"log_id": LOG_ID, "status": "login_required", "error": "需要扫码登录"}'
```

**失败：**
```bash
curl -s -X POST 'http://192.168.50.40:3456/api/publish/complete' \
  -H 'Authorization: Bearer pk_publisher_31d4632df15a11c58c2977f4' \
  -H 'Content-Type: application/json' \
  -d '{"log_id": LOG_ID, "status": "failed", "error": "错误描述"}'
```

#### Step 5e: 中断判断

- `NEED_LOGIN` → 立即中断，message 通知用户：
  ```
  ⚠️ {平台}需要扫码登录，请在电脑上完成扫码。剩余平台将在下一轮 cron 自动继续。
  已完成：{已完成平台列表}
  ```
- 连续 2 个平台 FAILED → 中断，避免浪费

#### Step 5f: 平台间隔

sleep 30 秒再处理下一个平台。

### Step 6: 汇总

所有平台处理完（或中断）后输出：

```
视频「{title}」(ID={id}) 发布汇总：
- bilibili:        ✅ 定时 12:00 / ❌ 失败(原因) / ⏭️ 跳过
- kuaishou:        ...
- weixin-channels: ...
- douyin:          ...
```

结束。下一轮 cron 继续处理未完成平台或下一个视频。

---

## 回收流程（type=collect_stats）

### Step 20: 浏览器健康检查

同 Step 2。

### Step 21: 获取回收列表

```bash
curl -s 'http://192.168.50.40:3456/api/videos/need-stats' \
  -H 'Authorization: Bearer pk_publisher_31d4632df15a11c58c2977f4'
```

### Step 22: 按平台串行执行回收脚本

对每个需要回收的视频和平台，直接 exec 运行对应脚本：

#### B站（有 bvid）
```bash
cd /Users/niuone/code/social-auto-upload && python3 \
  /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/bili_stats.py \
  --bvid "{bvid}"
```
stdout 格式：`STATS {"bvid":"BV1xx","views":N,"likes":N,"comments":N,"shares":N,"coins":N,"danmaku":N,"favorites":N}`

#### 快手
```bash
cd /Users/niuone/code/social-auto-upload && python3 \
  /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/ks_stats.py \
  --title "{标题前15字}"
```
stdout 格式：`STATS {"title_kw":"...","views":N,"likes":N,"comments":N}`

#### 抖音
```bash
cd /Users/niuone/code/social-auto-upload && python3 \
  /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/douyin_stats.py \
  --title "{标题前15字}"
```
stdout 格式：`STATS {"title_kw":"...","views":N,"likes":N,"comments":N,"shares":N}`

#### 视频号
```bash
cd /Users/niuone/code/social-auto-upload && python3 \
  /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/weixin_stats.py \
  --title "{标题前15字}"
```
stdout 格式：`STATS {"title_kw":"...","views":N,"likes":N,"comments":N,"favorites":N,"shares":N}`

#### 解析结果并上报 Hub

**成功**（stdout 含 `STATS {...}`）：
```bash
curl -s -X POST "http://192.168.50.40:3456/api/videos/{id}/stats" \
  -H 'Authorization: Bearer pk_publisher_31d4632df15a11c58c2977f4' \
  -H 'Content-Type: application/json' \
  -d '{
    "platform": "PLATFORM",
    "views": N,
    "likes": N,
    "comments": N,
    "shares": N,
    "favorites": N,
    "coins": N,
    "danmaku": N
  }'
```
（字段按平台填，缺少的字段不传）

**PENDING**（stdout 含 `PENDING`）：跳过，不上报，不算失败

**FAILED** 或 exit 1：
```bash
curl -s -X POST "http://192.168.50.40:3456/api/videos/{id}/stats-fail" \
  -H 'Authorization: Bearer pk_publisher_31d4632df15a11c58c2977f4' \
  -H 'Content-Type: application/json' \
  -d '{"platform": "PLATFORM", "error": "错误描述"}'
```

### Step 23: 汇总回收结果 → 结束

输出每个视频每个平台的回收状态。

---

## ⚠️ 关键规则

1. **发布和回收都用 exec 运行脚本**，不 spawn 子 agent，不操作浏览器 tool
2. **严格串行**，一个平台跑完再跑下一个
3. **NEED_LOGIN 立即中断** + 通知用户
4. **连续2次 FAILED 也中断**
5. Claim 和上报都在本 session 做，脚本只负责发布
6. exec timeout 设 600 秒（视频上传可能需要 2-3 分钟）
