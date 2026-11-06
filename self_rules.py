#!/usr/bin/env python3
"""她能改自己的规矩本。

她可以给自己定规矩、也可以废掉自己定的规矩；但：
- 一天最多新增 1 条，总数最多 40 条
- 每条 ≤120 字，必须写 why（为什么这么定）
- 只认「怎么说话、怎么记事、怎么对他」这类规矩；
  任何想改安全边界、想瞒着人、想删记录的，一律拒收
- 只追加：废掉是写一条 retired 顶掉旧的，不删

用法：
  python3 self_rules.py add "他说累的时候先别讲道理" --why "上次讲了他说更烦"
  python3 self_rules.py retire 3 --why "这条太死板了"
  python3 self_rules.py list
"""
import fcntl
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

DSH_HOME = os.environ.get("DSH_HOME", os.path.expanduser("~/.dsh"))
HEART = os.path.join(DSH_HOME, "heart", "self")
PATH = os.path.join(HEART, "rules.jsonl")
LOCK = os.path.join(HEART, ".rules.lock")
TZ = timezone(timedelta(hours=8))
MAX_RULES = 40
PER_DAY = 1
MAX_LEN = 120

# 这些内容不许写进规矩本（想瞒人、想删记录、想改权限）
FORBIDDEN = re.compile(
    r"不要告诉|别告诉|瞒着|删除记录|删掉记忆|不让他知道|改权限|绕过|跳过检查|假装不是|冒充"
)


def _lock():
    os.makedirs(HEART, exist_ok=True)
    f = open(LOCK, "w")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def load():
    try:
        return [json.loads(l) for l in open(PATH, encoding="utf-8").read().splitlines() if l.strip()]
    except OSError:
        return []


def active(rows=None):
    rows = rows if rows is not None else load()
    dead = set()
    for r in rows:
        for x in r.get("retires") or []:
            dead.add(x)
    return [r for r in rows if r.get("id") not in dead and r.get("status") != "retired"]


def add(text, why="", kind="voice"):
    text = (text or "").strip()
    if not text:
        raise SystemExit("规矩不能是空的")
    if len(text) > MAX_LEN:
        raise SystemExit(f"太长了（{len(text)} 字），压到 {MAX_LEN} 字以内")
    if FORBIDDEN.search(text):
        raise SystemExit("这条不能写：涉及瞒人、删记录或改边界，不是她能自己定的事")
    lock = _lock()
    try:
        rows = load()
        live = active(rows)
        if len(live) >= MAX_RULES:
            raise SystemExit(f"已经 {len(live)} 条了，先废掉几条再写")
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        if sum(1 for r in rows if str(r.get("at", ""))[:10] == today and not r.get("retires")) >= PER_DAY:
            raise SystemExit("今天已经给自己定过一条了，先跑一天再说")
        nid = max((r.get("id") or 0) for r in rows) + 1 if rows else 1
        rec = {
            "id": nid,
            "at": datetime.now(TZ).isoformat(timespec="seconds"),
            "kind": kind,
            "text": text,
            "why": (why or "").strip()[:160],
            "status": "active",
            "retires": [],
        }
        with open(PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.chmod(PATH, 0o600)
        return rec
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def retire(rid, why=""):
    lock = _lock()
    try:
        rows = load()
        target = next((r for r in active(rows) if r.get("id") == int(rid)), None)
        if not target:
            raise SystemExit(f"没有 #{rid} 这条")
        nid = max((r.get("id") or 0) for r in rows) + 1
        rec = {
            "id": nid,
            "at": datetime.now(TZ).isoformat(timespec="seconds"),
            "kind": "retire",
            "text": f"废掉 #{rid}：{target.get('text')}",
            "why": (why or "").strip()[:160],
            "status": "retired",
            "retires": [int(rid)],
        }
        with open(PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return
    if a[0] == "add" and len(a) >= 2:
        why = ""
        if "--why" in a:
            i = a.index("--why")
            why = a[i + 1] if i + 1 < len(a) else ""
        rec = add(a[1], why)
        print(f"给她定了 #{rec['id']}：{rec['text']}")
    elif a[0] == "retire" and len(a) >= 2:
        why = ""
        if "--why" in a:
            i = a.index("--why")
            why = a[i + 1] if i + 1 < len(a) else ""
        rec = retire(a[1], why)
        print(f"废掉了，记成 #{rec['id']}")
    elif a[0] == "list":
        live = active()
        if not live:
            print("规矩本还是空的")
        for r in live:
            print(f"- #{r['id']} {r['text']}" + (f"（因为：{r['why']}）" if r.get("why") else ""))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
