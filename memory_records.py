#!/usr/bin/env python3
"""可更正的记忆：同一件事变了写新行，旧的标 superseded，永不删除。

  records.jsonl 一行一条，只追加
  {id, ts, day, kind, gist, detail, why, tags, factKey, supersedes:[id...], longTerm}

factKey = object.aspect（例：他.睡觉时间）。**值永远不写进 key。**

用法：
  python3 memory_records.py add --kind note --gist "他习惯熬到两点" \
      --detail "11月6日他说两点才睡，第二天请假补觉" \
      --why "别在凌晨催他睡" --tags 作息,身体 --key 他.睡觉时间
  python3 memory_records.py search "他几点睡"
  python3 memory_records.py list
"""
import fcntl
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone

DSH_HOME = os.environ.get("DSH_HOME", os.path.expanduser("~/.dsh"))
HEART = os.path.join(DSH_HOME, "heart")
PATH = os.path.join(HEART, "records.jsonl")
LOCK = os.path.join(HEART, ".records.lock")
TZ = timezone(timedelta(hours=8))
GIST_MAX = 60
DAILY_CAP = 8          # 一天最多记这么些条，多了就只加固旧的
HALF_LIFE_BASE = 30.0  # 普通一条 30 天淡一半
# 重要度：决定半衰期，也决定被想起来的难易
HEAVY_WORDS = ("答应", "约好", "第一次", "生日", "生病", "住院", "难受", "撑不住", "约定", "永远", "怕")



def _locked():
    os.makedirs(HEART, exist_ok=True)
    f = open(LOCK, "w")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def load():
    try:
        return [json.loads(l) for l in open(PATH, encoding="utf-8").read().splitlines() if l.strip()]
    except OSError:
        return []


def alive(rows=None):
    """活着的记录：没有被任何人 supersede 掉的。"""
    rows = rows if rows is not None else load()
    dead = set()
    for r in rows:
        for old in r.get("supersedes") or []:
            dead.add(old)
    return [r for r in rows if r.get("id") not in dead]


def importance(r):
    """1–5：越重要忘得越慢。"""
    score = 1
    text = " ".join([r.get("gist", ""), r.get("why", ""), " ".join(r.get("tags") or [])])
    if r.get("why"):
        score += 1
    if any(w in text for w in HEAVY_WORDS):
        score += 1
    if r.get("longTerm"):
        score += 1
    score += min(2, int((r.get("hits") or 0) / 2))
    if r.get("kind") in ("correction", "promise"):
        score += 1
    return max(1, min(5, score))


def half_life_days(r):
    return HALF_LIFE_BASE * (0.4 + 0.3 * importance(r))   # 重要的一天顶四五十天


def freshness(r, now=None):
    """0–1：按半衰期衰减，但不归零。"""
    now = now or datetime.now(TZ)
    try:
        at = datetime.fromisoformat(str(r.get("ts")).replace("Z", "+00:00"))
    except Exception:
        return 0.5
    age = max(0.0, (now - at).total_seconds() / 86400)
    return 0.5 ** (age / max(1.0, half_life_days(r)))


def _today_count(rows):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    return sum(1 for r in rows if r.get("day") == today)


def add(kind, gist, detail="", why="", tags=None, fact_key="", long_term=False):
    gist = (gist or "").strip()[:GIST_MAX]
    if not gist:
        raise SystemExit("gist 不能为空")
    lock = _locked()
    try:
        rows = load()
        # 同一件事说第二遍：不新增，只加固（hits+1，时间刷新）
        for r in alive(rows):
            old = r.get("gist") or ""
            if old and gist and (old == gist or (len(set(old) & set(gist)) / max(1, len(set(gist)))) >= 0.75):
                nid = max((x.get("id") or 0) for x in rows) + 1
                now = datetime.now(TZ)
                rec = dict(r)
                rec.update({
                    "id": nid,
                    "ts": now.isoformat(timespec="seconds"),
                    "day": now.strftime("%Y-%m-%d"),
                    "hits": (r.get("hits") or 0) + 1,
                    "supersedes": [r.get("id")],
                    "detail": detail or r.get("detail", ""),
                    "why": why or r.get("why", ""),
                })
                with open(PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                os.chmod(PATH, 0o600)
                return rec
        if _today_count(rows) >= DAILY_CAP:
            raise SystemExit(f"今天已经记了 {DAILY_CAP} 条，够了；同一件事提第二遍只会加固旧的那条")
        nid = max((r.get("id") or 0) for r in rows) + 1 if rows else 1
        sup = []
        if fact_key:
            # 同一 factKey 的旧记录让位（不是删，是标死）
            for r in alive(rows):
                if r.get("factKey") == fact_key and (r.get("gist") or "") != gist:
                    sup.append(r.get("id"))
        now = datetime.now(TZ)
        rec = {
            "id": nid,
            "ts": now.isoformat(timespec="seconds"),
            "day": now.strftime("%Y-%m-%d"),
            "kind": kind or "note",
            "gist": gist,
            "detail": (detail or "").strip()[:600],
            "why": (why or "").strip()[:200],
            "tags": [t for t in (tags or []) if t],
            "factKey": fact_key or "",
            "supersedes": sup,
            "longTerm": bool(long_term),
        }
        with open(PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.chmod(PATH, 0o600)
        return rec
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def _tokens(s):
    s = (s or "").lower()
    latin = re.findall(r"[a-z0-9_]{2,}", s)
    han = re.sub(r"[^\u4e00-\u9fff]", " ", s)
    big = []
    for part in han.split():
        big += [part[i : i + 2] for i in range(len(part) - 1)]
    return latin + big


def search(query, k=3):
    rows = alive()
    if not rows:
        return []
    docs = []
    for r in rows:
        text = " ".join([r.get("gist", ""), r.get("detail", ""), r.get("why", ""),
                         r.get("factKey", ""), " ".join(r.get("tags") or [])])
        docs.append((r, _tokens(text)))
    qt = _tokens(query)
    if not qt:
        return []
    n = len(docs)
    df = {}
    for _, toks in docs:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    scored = []
    for r, toks in docs:
        score = 0.0
        for t in qt:
            if t not in toks:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * toks.count(t)
        if score > 0:
            scored.append((score * (0.4 + 0.6 * freshness(r)) * (0.7 + 0.12 * importance(r)), r))
    if not scored:
        # 换个说法也要能翻到：退一步做子串命中
        for r, toks in docs:
            hay = " ".join([r.get("gist", ""), r.get("detail", ""), r.get("why", ""), r.get("factKey", "")])
            chars = {c for c in query if c.strip()}
            hit = sum(1 for c in chars if c in hay)
            if chars and hit / len(chars) >= 0.6:
                scored.append((hit / len(chars), r))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:k]]


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return
    if a[0] == "add":
        kw, i = {}, 0
        a = a[1:]
        while i < len(a):
            if a[i].startswith("--"):
                key = a[i][2:].replace("-", "_")
                val = a[i + 1] if i + 1 < len(a) else ""
                kw[key] = val
                i += 2
            else:
                i += 1
        rec = add(
            kw.get("kind", "note"), kw.get("gist", ""), kw.get("detail", ""),
            kw.get("why", ""), (kw.get("tags") or "").split(","), kw.get("key", ""),
            kw.get("long_term") in ("1", "true", "yes"),
        )
        print(f"记下 #{rec['id']}：{rec['gist']}" + (f"（顶掉 {rec['supersedes']}）" if rec["supersedes"] else ""))
    elif a[0] == "search":
        for r in search(" ".join(a[1:])):
            print(f"- #{r['id']} [{r.get('factKey') or '-'}] {r['gist']}" + (f"｜why：{r['why']}" if r.get("why") else ""))
    elif a[0] == "list":
        for r in alive():
            print(f"- #{r['id']} {r['day']} {r['kind']} {r['gist']}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
