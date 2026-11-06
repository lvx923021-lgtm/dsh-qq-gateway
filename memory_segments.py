#!/usr/bin/env python3
"""把原话切段、按 BM25 检索，并记"哪段被想起过"。

段：按时间间隔（>20 分钟）或长度（>12 轮）切开。
打分：BM25（中文二元组 + 英文词）× 时间衰减 × 用进废退。
用法：
  python3 memory_segments.py search "西安 擀面皮"
  python3 memory_segments.py stats
"""
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

DSH_HOME = os.environ.get("DSH_HOME", os.path.expanduser("~/.dsh"))
RAW = os.path.join(DSH_HOME, "heart", "raw")
REH = os.path.join(DSH_HOME, "heart", "rehearsals.json")
TZ = timezone(timedelta(hours=8))
GAP_MIN = 20
MAX_TURNS = 12
K1, B = 1.2, 0.75


def _rows(days=120):
    try:
        files = sorted(f for f in os.listdir(RAW) if f.endswith(".jsonl"))[-days:]
    except OSError:
        return []
    out = []
    for name in files:
        try:
            with open(os.path.join(RAW, name), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
        except OSError:
            continue
    return out


def _when(r):
    try:
        return datetime.fromisoformat(str(r.get("at", "")).replace("Z", "+00:00"))
    except Exception:
        return None


def segments(rows=None):
    """把流水切成段。切完每段带 id = 首行时间戳。"""
    rows = rows if rows is not None else _rows()
    segs, cur, last_t = [], [], None
    for r in rows:
        t = _when(r)
        if t is None:
            continue
        if cur and ((t - last_t) > timedelta(minutes=GAP_MIN) or len(cur) >= MAX_TURNS):
            segs.append(cur)
            cur = []
        cur.append((t, r))
        last_t = t
    if cur:
        segs.append(cur)
    out = []
    for s in segs:
        start = s[0][0]
        body = "\n".join(
            ("他：" if r.get("role") in ("he", "user") else "我：") + (r.get("text") or "")
            for _, r in s
        )
        out.append({
            "id": start.strftime("%Y%m%d-%H%M"),
            "at": start.isoformat(timespec="minutes"),
            "turns": len(s),
            "text": body,
        })
    return out


def _tokens(s: str):
    s = (s or "").lower()
    latin = re.findall(r"[a-z0-9_]{2,}", s)
    han = re.sub(r"[^\u4e00-\u9fff]", " ", s)
    bigr = []
    for part in han.split():
        bigr += [part[i : i + 2] for i in range(len(part) - 1)]
    return latin + bigr


def _rehearsals():
    try:
        return json.load(open(REH, encoding="utf-8"))
    except Exception:
        return {}


def _bump(ids):
    d = _rehearsals()
    for i in ids:
        d[i] = d.get(i, 0) + 1
    try:
        json.dump(d, open(REH, "w", encoding="utf-8"), ensure_ascii=False)
        os.chmod(REH, 0o600)
    except Exception:
        pass


def search(query: str, k: int = 3):
    segs = segments()
    if not segs:
        return []
    qt = Counter(_tokens(query))
    if not qt:
        return []
    docs = [Counter(_tokens(s["text"])) for s in segs]
    n = len(docs)
    avg = sum(sum(d.values()) for d in docs) / max(1, n)
    df = Counter()
    for d in docs:
        for t in d:
            df[t] += 1
    reh = _rehearsals()
    now = datetime.now(TZ)
    scored = []
    for seg, d in zip(segs, docs):
        score = 0.0
        for t, qf in qt.items():
            if t not in d:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            tf = d[t]
            score += qf * idf * (tf * (K1 + 1)) / (tf + K1 * (1 - B + B * sum(d.values()) / max(1, avg)))
        if score <= 0:
            continue
        try:
            age = max(0.0, (now - datetime.fromisoformat(seg["at"])).total_seconds() / 86400)
        except Exception:
            age = 30.0
        recency = 1.0 / (1.0 + age / 30.0)
        use = 1.0 + 0.35 * math.log(1 + reh.get(seg["id"], 0))
        scored.append((round(score * recency * use, 2), seg))
    scored.sort(key=lambda x: -x[0])
    picked = scored[:k]
    _bump([s["id"] for _, s in picked])
    return [{"id": s["id"], "at": s["at"], "score": sc, "turn": s["turns"],
             "said": re.sub(r"\s+", " ", s["text"])[:120]} for sc, s in picked]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    if sys.argv[1] == "search":
        q = " ".join(sys.argv[2:])
        for h in search(q):
            print(f"- [{h['id']}] 分 {h['score']} · {h['turn']} 轮 · {h['said']}")
    elif sys.argv[1] == "stats":
        segs = segments()
        print(f"段数：{len(segs)}｜有 rehear 记录的：{len(_rehearsals())}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
