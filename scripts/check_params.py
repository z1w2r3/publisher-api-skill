#!/usr/bin/env python3
"""
发布参数验证脚本
用法: python3 check_params.py --platform <平台> [参数...]
成功: exit 0，输出 PARAMS_OK
失败: exit 1，输出 ERROR: <具体说明>
"""
import argparse
import os
import re
import sys
from datetime import datetime

PLATFORMS = ["bilibili", "kuaishou", "weixin-channels", "douyin"]

REQUIRED = {
    "bilibili":        ["video", "title", "desc", "tags", "cookie"],
    "kuaishou":        ["video", "desc"],
    "weixin-channels": ["video", "desc", "short_title"],
    "douyin":          ["video", "title", "desc", "tags"],
}

MAX_TAGS = {
    "bilibili": 12,
    "douyin":   5,
}

CHINESE_COMMA = "，"


def normalize_spaces(s: str) -> str:
    """去除中英文之间多余空格（保留纯英文单词间空格）"""
    s = re.sub(r"([\u4e00-\u9fff\u3000-\u303f])\s+([A-Za-z0-9])", r"\1\2", s)
    s = re.sub(r"([A-Za-z0-9])\s+([\u4e00-\u9fff\u3000-\u303f])", r"\1\2", s)
    return s.strip()


def check_no_chinese_comma(value: str, param: str, errors: list):
    if CHINESE_COMMA in value:
        errors.append(f"--{param} 含中文逗号「，」，请改用英文逗号「,」: {value!r}")


def check_no_extra_spaces(value: str, param: str, errors: list):
    normalized = normalize_spaces(value)
    if normalized != value:
        errors.append(
            f"--{param} 含多余中英文间空格:\n"
            f"  原值: {value!r}\n"
            f"  应为: {normalized!r}"
        )


def check_file_exists(path: str, param: str, errors: list):
    if not path:
        return
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        errors.append(f"--{param} 文件不存在: {expanded}")


def check_dtime(dtime: str, platform: str, errors: list):
    if not dtime:
        return
    # bilibili 用 T 分隔，其他用空格
    fmt_t = "%Y-%m-%dT%H:%M:%S"
    fmt_s = "%Y-%m-%d %H:%M:%S"
    ok = False
    for fmt in [fmt_t, fmt_s]:
        try:
            datetime.strptime(dtime, fmt)
            ok = True
            break
        except ValueError:
            pass
    if not ok:
        errors.append(
            f"--dtime 格式错误: {dtime!r}\n"
            f"  bilibili 应为: 2026-03-04T21:00:00\n"
            f"  其他平台应为: 2026-03-04 21:00:00"
        )
    # 检查是否用了错误格式
    if platform == "bilibili" and " " in (dtime or ""):
        errors.append(f"--dtime bilibili 应用 T 分隔，不是空格: {dtime!r}")
    if platform != "bilibili" and "T" in (dtime or ""):
        errors.append(f"--dtime {platform} 应用空格分隔，不是 T: {dtime!r}")


def main():
    parser = argparse.ArgumentParser(description="发布参数验证")
    parser.add_argument("--platform", required=True, choices=PLATFORMS)
    parser.add_argument("--video",       default="")
    parser.add_argument("--title",       default="")
    parser.add_argument("--desc",        default="")
    parser.add_argument("--tags",        default="")
    parser.add_argument("--short-title", dest="short_title", default="")
    parser.add_argument("--cookie",      default="")
    parser.add_argument("--cover34",     default="")
    parser.add_argument("--cover43",     default="")
    parser.add_argument("--cover169",    default="")
    parser.add_argument("--dtime",       default="")
    parser.add_argument("--dedup-kw",    default="")
    parser.add_argument("--zone",        default="")
    args = parser.parse_args()

    platform = args.platform
    errors = []

    # 1. 必填参数检查
    required = REQUIRED.get(platform, [])
    field_map = {
        "video":       args.video,
        "title":       args.title,
        "desc":        args.desc,
        "tags":        args.tags,
        "short_title": args.short_title,
        "cookie":      args.cookie,
    }
    for f in required:
        if not field_map.get(f, "").strip():
            param_name = f.replace("_", "-")
            errors.append(f"--{param_name} 为必填项，不能为空")

    # 2. 文件存在检查
    check_file_exists(args.video,    "video",    errors)
    check_file_exists(args.cover34,  "cover34",  errors)
    check_file_exists(args.cover43,  "cover43",  errors)
    check_file_exists(args.cover169, "cover169", errors)
    if platform == "bilibili":
        check_file_exists(args.cookie, "cookie", errors)

    # 3. tags 格式检查
    if args.tags:
        check_no_chinese_comma(args.tags, "tags", errors)
        tag_list = [t.strip() for t in args.tags.split(",") if t.strip()]
        max_t = MAX_TAGS.get(platform)
        if max_t and len(tag_list) > max_t:
            errors.append(
                f"--tags {platform} 最多 {max_t} 个，当前 {len(tag_list)} 个: {args.tags!r}"
            )
        # tags 不应含 #
        if any(t.startswith("#") for t in tag_list):
            errors.append(f"--tags 不应含 # 号，请去掉: {args.tags!r}")

    # 4. 标题/短标题空格检查
    if args.title:
        check_no_extra_spaces(args.title, "title", errors)
    if args.short_title:
        check_no_extra_spaces(args.short_title, "short-title", errors)
        # 视频号短标题长度
        if platform == "weixin-channels":
            l = len(args.short_title)
            if l < 6 or l > 16:
                errors.append(
                    f"--short-title 长度应为 6-16 字符，当前 {l} 字符: {args.short_title!r}"
                )

    # 5. dtime 格式检查
    if args.dtime:
        check_dtime(args.dtime, platform, errors)

    # 输出结果
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    else:
        print("PARAMS_OK")
        sys.exit(0)


if __name__ == "__main__":
    main()
