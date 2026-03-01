#!/usr/bin/env python3
"""
B站视频上传脚本（API方式，0 tokens）
- 视频上传：biliup（多线程分片）
- 封面上传：biliup cover_up()
- 提交：直接调 v3 接口（支持 human_type2 新版分区 + 双封面）

用法：
  python3 bili_upload.py \
    --cookie /path/to/account.json \
    --video /path/to/landscape.mp4 \
    --title "标题" --desc "简介" \
    --tags "tag1,tag2" \
    --cover43 /path/cover-4x3.png \
    --cover169 /path/cover-16x9.png \
    --zone 1011 \
    --dtime "2026-03-02T17:00:00"
"""
import argparse
import json
import sys
import os
import time
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Any, Union

SAU_PATH = "/Users/niuone/code/social-auto-upload"
sys.path.insert(0, SAU_PATH)

from biliup.plugins.bili_webup import BiliBili, Data


def load_cookie(cookie_path: str):
    """返回 (raw_json, extracted_dict)"""
    with open(cookie_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    keys = ["SESSDATA", "bili_jct", "DedeUserID__ckMd5", "DedeUserID", "access_token"]
    extracted = {}
    for c in raw.get("cookie_info", {}).get("cookies", []):
        if c["name"] in keys:
            extracted[c["name"]] = c["value"]
    if "access_token" in raw.get("token_info", {}):
        extracted["access_token"] = raw["token_info"]["access_token"]
    return raw, extracted


def submit_v3(session, bili_jct: str, video_data: dict) -> dict:
    """
    直接调 v3 接口提交稿件，支持新版分区(human_type2)和双封面(cover43/cover169)
    POST https://member.bilibili.com/x/vu/web/add/v3?csrf={csrf}&type_mode=2
    """
    url = f"https://member.bilibili.com/x/vu/web/add/v3?csrf={bili_jct}&type_mode=2"
    print(f"[提交] 调用 v3 接口，分区 human_type2={video_data.get('human_type2')}", flush=True)
    ret = session.post(url, timeout=30, json=video_data).json()
    return ret


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cookie", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--desc", default="")
    parser.add_argument("--tags", default="")
    parser.add_argument("--cover43", default="", help="4:3封面路径（主封面）")
    parser.add_argument("--cover169", default="", help="16:9封面路径")
    parser.add_argument("--zone", default="1011", help="新版分区ID，默认1011=人工智能")
    parser.add_argument("--tid", default="21", help="旧版分区占位，默认21")
    parser.add_argument("--dtime", default="", help="定时发布 ISO8601，如 2026-03-02T17:00:00")
    args = parser.parse_args()

    raw_cookie, cookie_data = load_cookie(args.cookie)
    print(f"[Cookie] DedeUserID={cookie_data.get('DedeUserID', '?')}", flush=True)

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    # 定时时间戳（距提交 > 2小时）
    dtime = 0
    if args.dtime:
        dt = datetime.fromisoformat(args.dtime)
        dtime = int(dt.timestamp())

    # Data 对象（用于视频上传阶段）
    data = Data()
    data.copyright = 1
    data.title = args.title
    data.desc = args.desc
    data.tid = int(args.tid)
    data.set_tag(tags)
    data.dtime = dtime

    print(f"[上传] 开始上传视频: {args.video}", flush=True)
    print(f"[配置] 标题={args.title[:20]}... | zone={args.zone} | 定时={args.dtime or '立即'}", flush=True)

    try:
        with BiliBili(data) as bili:
            bili.login_by_cookies(raw_cookie)
            bili.access_token = cookie_data.get("access_token")
            bili_jct = cookie_data.get("bili_jct", "")

            # Step 1: 上传视频分片
            video_part = bili.upload_file(str(args.video), lines="AUTO", tasks=3)
            video_part["title"] = args.title
            data.append(video_part)
            print("[上传] 视频上传完成", flush=True)

            # Step 2: 上传封面
            cover_url = ""
            cover43_url = ""
            cover169_url = ""

            if args.cover43 and os.path.exists(args.cover43):
                cover_url = bili.cover_up(args.cover43).replace("http:", "https:")
                cover43_url = cover_url
                print(f"[封面] 4:3 上传成功: {cover_url[:60]}...", flush=True)

            if args.cover169 and os.path.exists(args.cover169):
                cover169_url = bili.cover_up(args.cover169).replace("http:", "https:")
                print(f"[封面] 16:9 上传成功: {cover169_url[:60]}...", flush=True)

            # Step 3: 组装 v3 提交体
            video_dict = asdict(data)
            # 新版分区（覆盖 tid 逻辑，由 human_type2 决定实际分区）
            video_dict["human_type2"] = int(args.zone)
            # 封面
            if cover43_url:
                video_dict["cover"] = cover43_url   # 主封面 = 4:3（横屏显示）
                video_dict["cover43"] = cover43_url
            if cover169_url:
                video_dict["cover169"] = cover169_url

            # Step 4: 提交 v3
            ret = submit_v3(bili._BiliBili__session, bili_jct, video_dict)

            if ret.get("code") == 0:
                bvid = ret.get("data", {}).get("bvid", "")
                print(f"PUBLISHED bvid={bvid}", flush=True)
                sys.exit(0)
            else:
                print(f"FAILED code={ret.get('code')} msg={ret.get('message')}", flush=True)
                sys.exit(1)

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"FAILED error={e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
