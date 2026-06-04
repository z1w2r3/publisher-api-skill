#!/usr/bin/env python3
"""
B站数据回收脚本
用法：python3 bili_stats.py --cookie /path/to/account.json --bvid BV1xxxx [--bvid BV2xxxx ...]

输出（每行一个结果）：
  STATS {"bvid":"BV1xx","views":123,"likes":1,...}
  FAILED bvid=BV1xx error=...

exit 0: 全部成功，exit 1: 至少一个失败
"""
import argparse, json, sys, time, urllib.request

COOKIE_PATH = "/tmp/sau-test/cookies/bilibili_uploader/account.json"

def load_cookie(path):
    d = json.load(open(path))
    return "; ".join(f'{c["name"]}={c["value"]}' for c in d["cookie_info"]["cookies"])

def fetch_stat(bvid, cookie_str):
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    req = urllib.request.Request(url, headers={
        "Cookie": cookie_str,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com/",
    })
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.load(resp)
    code = data.get("code", 0)
    msg  = data.get("message", "")
    if code == 62003:
        # 定时待发布，视频还未公开，跳过（不算失败）
        raise ValueError(f"PENDING msg={msg}")
    if code != 0:
        raise ValueError(f"API error code={code} msg={msg}")
    s = data["data"]["stat"]
    return {
        "bvid":      bvid,
        "views":     s.get("view", 0),
        "likes":     s.get("like", 0),
        "comments":  s.get("reply", 0),
        "shares":    s.get("share", 0),
        "coins":     s.get("coin", 0),
        "danmaku":   s.get("danmaku", 0),
        "favorites": s.get("favorite", 0),
    }

def list_archives(cookie_str, max_videos=80):
    """批量列出投稿（含 stat），免 per-bvid。state==0 为已开放浏览，其余视为 pending。"""
    out = []
    pn, ps = 1, 50
    headers = {
        "Cookie": cookie_str,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://member.bilibili.com/platform/upload-manager/article",
    }
    while len(out) < max_videos:
        url = (f"https://member.bilibili.com/x/web/archives"
               f"?status=is_pubing,pubed,not_pubed&pn={pn}&ps={ps}")
        req = urllib.request.Request(url, headers=headers)
        data = json.load(urllib.request.urlopen(req, timeout=20))
        if data.get("code") != 0:
            break
        d = data.get("data") or {}
        arcs = d.get("arc_audits") or []
        if not arcs:
            break
        for it in arcs:
            a = it.get("Archive") or {}
            s = it.get("stat") or {}
            state = a.get("state")
            out.append({
                "id":        a.get("bvid"),
                "title":     a.get("title") or "",
                "views":     s.get("view", 0),
                "likes":     s.get("like", 0),
                "comments":  s.get("reply", 0),
                "shares":    s.get("share", 0),
                "favorites": s.get("favorite", 0),
                "coins":     s.get("coin", 0),
                "danmaku":   s.get("danmaku", 0),
                "pending":   state != 0,
            })
        count = (d.get("page") or {}).get("count", 0)
        if pn * ps >= count:
            break
        pn += 1
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cookie", default=COOKIE_PATH)
    parser.add_argument("--bvid", action="append")
    parser.add_argument("--list", action="store_true", help="批量列出全部投稿+stat（一次拿全部）")
    parser.add_argument("--max", type=int, default=80)
    args = parser.parse_args()

    cookie_str = load_cookie(args.cookie)

    if args.list:
        videos = list_archives(cookie_str, args.max)
        print("STATS_BATCH " + json.dumps(
            {"platform": "bilibili", "count": len(videos), "videos": videos},
            ensure_ascii=False), flush=True)
        sys.exit(0)

    if not args.bvid:
        print("FAILED error=need --bvid or --list", flush=True)
        sys.exit(1)

    has_error = False

    for bvid in args.bvid:
        try:
            stat = fetch_stat(bvid, cookie_str)
            print(f"STATS {json.dumps(stat, ensure_ascii=False)}", flush=True)
        except ValueError as e:
            err_str = str(e)
            if err_str.startswith("PENDING"):
                print(f"PENDING bvid={bvid} {err_str}", flush=True)  # 不计入失败
            else:
                print(f"FAILED bvid={bvid} error={err_str}", flush=True)
                has_error = True
        except Exception as e:
            print(f"FAILED bvid={bvid} error={e}", flush=True)
            has_error = True
        time.sleep(0.5)

    sys.exit(1 if has_error else 0)

if __name__ == "__main__":
    main()
