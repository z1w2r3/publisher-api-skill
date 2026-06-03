#!/usr/bin/env python3
"""
B站数据回收脚本（创作者稿件列表 API，按标题匹配 — 取代失效的 DOM 抓取）
用法:
  python3 bili_stats_by_title.py --title "标题前15字" [--title ...] [--pages 5]
  python3 bili_stats_by_title.py --brief /path/brief.json --platform bilibili

输出:
  STATS {"title_kw":"xx","bvid":"BVxx","views":..,"likes":..,"comments":..,"shares":..,"coins":..,"danmaku":..,"favorites":..}
  PENDING title_kw=xx      (定时待发布/审核中)
  FAILED title_kw=xx error=...
exit 0: 全部命中(含PENDING), exit 1: 至少一个未找到
"""
import argparse, json, sys, urllib.request, urllib.parse

COOKIE_PATH = "/Users/zhengweirong/.openclaw/cookies/bilibili_uploader/account.json"
LIST_API = "https://member.bilibili.com/x/web/archives"

def load_cookie(path):
    d = json.load(open(path, encoding="utf-8"))
    return "; ".join("%s=%s" % (c["name"], c["value"]) for c in d["cookie_info"]["cookies"])

def fetch_page(cookie_str, pn, ps):
    qs = urllib.parse.urlencode({"pn": pn, "ps": ps, "status": "is_pubing,pubed,not_pubed"})
    req = urllib.request.Request("%s?%s" % (LIST_API, qs), headers={
        "Cookie": cookie_str, "User-Agent": "Mozilla/5.0",
        "Referer": "https://member.bilibili.com/platform/upload-manager/article",
    })
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8"))

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cookie", default=COOKIE_PATH)
    p.add_argument("--title", action="append", default=None)
    p.add_argument("--brief", help="brief.json path; read platform-specific title")
    p.add_argument("--platform", help="platform key for --brief (bilibili)")
    p.add_argument("--pages", type=int, default=5)
    args = p.parse_args()

    titles = list(args.title or [])
    if args.brief and args.platform:
        try:
            with open(args.brief, encoding="utf-8") as bf:
                b = json.load(bf)
            pf = b.get(args.platform) or {}
            t = (pf.get("title") or pf.get("short_title")
                 or ((pf.get("desc") or "").split("\n")[0].strip() or None))
            if t:
                titles.append(t)
        except Exception as e:
            print("FAILED error=brief read failed: %s" % e, flush=True)
    if not titles:
        print("FAILED error=need --title or --brief+--platform", flush=True)
        sys.exit(1)

    kws = [t[:15] for t in titles]
    cookie_str = load_cookie(args.cookie)

    archives, ps = [], 20
    for pn in range(1, args.pages + 1):
        try:
            j = fetch_page(cookie_str, pn, ps)
        except Exception as e:
            print("FAILED error=list api: %s" % e, flush=True); sys.exit(1)
        if j.get("code") != 0:
            print("FAILED error=list api code=%s msg=%s" % (j.get("code"), j.get("message")), flush=True); sys.exit(1)
        data = j.get("data") or {}
        items = data.get("arc_audits") or []
        archives.extend(items)
        cnt = (data.get("page") or {}).get("count") or 0
        if not items or pn * ps >= cnt:
            break

    matched, pending = {}, set()
    for kw in kws:
        for a in archives:
            arc = a.get("Archive") or {}
            title = arc.get("title") or ""
            if kw and kw in title:
                st = a.get("stat") or {}
                published = (arc.get("state_desc") == "开放浏览") or (st.get("view", 0) > 0)
                if published:
                    matched[kw] = {
                        "bvid": arc.get("bvid"),
                        "views": st.get("view", 0), "likes": st.get("like", 0),
                        "comments": st.get("reply", 0), "shares": st.get("share", 0),
                        "coins": st.get("coin", 0), "danmaku": st.get("danmaku", 0),
                        "favorites": st.get("favorite", 0),
                    }
                else:
                    pending.add(kw)
                break

    has_error = False
    for kw in kws:
        if kw in matched:
            print("STATS %s" % json.dumps({"title_kw": kw, **matched[kw]}, ensure_ascii=False), flush=True)
        elif kw in pending:
            print("PENDING title_kw=%s" % kw, flush=True)
        else:
            print("FAILED title_kw=%s error=前%d页未找到" % (kw, args.pages), flush=True)
            has_error = True
    sys.exit(1 if has_error else 0)

if __name__ == "__main__":
    main()
