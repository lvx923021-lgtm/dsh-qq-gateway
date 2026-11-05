#!/usr/bin/env python3
"""只读：他在 B 站最近在看什么。输出一段给小鱼看的短句。
缓存 10 分钟，不登录、不写任何东西。
"""
import json
import os
import time
import urllib.request

CRED = "/root/.dsh/bili-humanism/credential.json"
CACHE = "/root/qq-gateway/watching.json"
TTL = 600
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"


def _get(url, sessdata):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com/",
            "Cookie": "SESSDATA=" + sessdata,
        },
    )
    return json.load(urllib.request.urlopen(req, timeout=15))


def fetch():
    try:
        with open(CRED, encoding="utf-8") as f:
            sess = json.load(f)["sessdata"]
    except Exception:
        return ""
    lines = []
    try:
        hist = _get(
            "https://api.bilibili.com/x/web-interface/history/cursor?ps=6", sess
        )["data"].get("list") or []
        items = []
        for h in hist[:5]:
            title = (h.get("title") or "").strip()
            author = ((h.get("author_name") or "")).strip()
            if title:
                items.append(f"《{title[:26]}》" + (f"（{author[:12]}）" if author else ""))
        if items:
            lines.append("最近在看：" + "；".join(items))
    except Exception:
        pass
    try:
        tv = _get("https://api.bilibili.com/x/v2/history/toview", sess)["data"]
        lst = tv.get("list") or []
        if lst:
            names = "；".join(f"《{(v.get('title') or '')[:24]}》" for v in lst[:3])
            lines.append(f"稍后再看 {tv.get('count') or len(lst)} 条：{names}")
    except Exception:
        pass
    return "\n".join(lines)[:600]


def main():
    try:
        if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < TTL:
            with open(CACHE, encoding="utf-8") as f:
                print(json.load(f).get("text", ""))
            return
    except Exception:
        pass
    text = fetch()
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({"at": time.time(), "text": text}, f, ensure_ascii=False)
        os.chmod(CACHE, 0o600)
    except Exception:
        pass
    print(text)


if __name__ == "__main__":
    main()
