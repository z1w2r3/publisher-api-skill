# Dispatcher 参数测试文档

**目的**：测试不同模型对发布参数的计算是否一致。  
**规则**：只读取任务、计算参数、调用 param_logger，**不 claim、不 complete、不修改 Hub 任何状态**。

---

## Step 1：读取待发布任务（只读，不 claim）

```bash
curl -s 'http://192.168.50.40:3456/api/publish/pending?key=pk_publisher_31d4632df15a11c58c2977f4&limit=1'
```

若返回空数组 `[]`，输出「无待发布任务」并结束。

---

## Step 2：读取投稿文案

素材目录：`~/Media/staging/{slug}/投稿文案.md`

```bash
cat ~/Media/staging/{slug}/投稿文案.md
```

---

## Step 3：确认待发布平台

根据任务数据中的 `published_platforms`、`failed_platforms`、`fail_counts`、`login_required` 字段，按生产规则（同 dispatcher-v3.md Step 3）判断哪些平台待发布。

输出：`待测试平台 = [bilibili, kuaishou, weixin-channels, douyin]`（只列实际待发布的）

---

## Step 4：计算各平台参数

按生产规则计算每个平台的脚本参数（同 dispatcher-v3.md Step 4-5），但**不执行真实脚本**，改为调用 param_logger：

脚本路径：`/Users/niuone/.openclaw/skills/publisher-api-skill/scripts/test/param_logger.py`  
日志路径：`/tmp/dispatch_params.log`（每次追加，不覆盖）

### bilibili

```bash
cd /Users/niuone/code/social-auto-upload && python3 /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/test/param_logger.py \
  --platform bilibili \
  --video "..." \
  --title "..." \
  --desc "..." \
  --cover "..." \
  --dtime "..."
```

### kuaishou

```bash
cd /Users/niuone/code/social-auto-upload && python3 /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/test/param_logger.py \
  --platform kuaishou \
  --video "..." \
  --desc "..." \
  --cover "..." \
  --dtime "..."
```

### weixin-channels

```bash
cd /Users/niuone/code/social-auto-upload && python3 /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/test/param_logger.py \
  --platform weixin-channels \
  --video "..." \
  --short-title "..." \
  --desc "..." \
  --cover34 "..." \
  --dtime "..."
```

### douyin

```bash
cd /Users/niuone/code/social-auto-upload && python3 /Users/niuone/.openclaw/skills/publisher-api-skill/scripts/test/param_logger.py \
  --platform douyin \
  --video "..." \
  --title "..." \
  --desc "..." \
  --cover34 "..." \
  --cover43 "..." \
  --dtime "..."
```

---

## Step 5：输出汇总

输出格式：
```
=== 参数测试完成 ===
视频：{title} (ID={id})
模型：{model_name}
日志已写入：/tmp/dispatch_params.log

各平台参数摘要：
- bilibili: title=... dtime=...
- kuaishou: dtime=...
- weixin-channels: short-title=... dtime=...
- douyin: title=... dtime=...
```

**不调用任何 Hub API（不 claim，不 complete，不 reset）。**
