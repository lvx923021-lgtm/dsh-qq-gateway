#!/usr/bin/env python3
"""摘要层：把放久了的细节收成一条摘要，再把细节沉进归档。

用法：
  python3 heart_summarize.py list            # 列出该收的旧细节（JSON）
  python3 heart_summarize.py apply "摘要" id1,id2,...   # 写入摘要 + 把细节归档

规则：
- 只收 14 天以前、还没被收过的；
- 摘要写进 notes.jsonl，带 summary_of 标记，方便追溯；
- 原细节移进 notes-archive.jsonl（不删）；
- 全程加同一把心事本锁。
"""
import fcntl
import json
import os
import sys
from datetime import datetime, timedelta, timezone

DSH_HOME = os.environ.get("DSH_HOME", os.path.expanduser("~/.dsh"))
HEART = os.path.join(DSH_HOME, "heart")
NOTES = os.path.join(HEART, "notes.jsonl")
ARCHIVE = os.path.join(HEART, "notes-archive.jsonl")
LOCK = os.path.join(HEART, ".notes.lock")
TZ = timezone(timedelta(hours=8))
AGE_DAYS = 14


def _load():
    try:
        return [json.loads(l) for l in open(NOTES, encoding="utf-8").read().splitlines() if l.strip()]
    except OSError:
        return []


def _when(r):
    try:
        return datetime.fromisoformat(str(r.get("at", "")).replace("Z", "+00:00"))
    except Exception:
        return datetime.now(TZ)


def _locked():
    os.makedirs(HEART, exist_ok=True)
    f = open(LOCK, "w")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def cmd_list():
    cutoff = datetime.now(TZ) - timedelta(days=AGE_DAYS)
    rows = _load()
    old = [r for r in rows if _when(r) < cutoff and not r.get("summary_of")]
    print(json.dumps({"count": len(old), "items": [
        {"id": r.get("id"), "at": str(r.get("at"))[:10], "text": (r.get("text") or "")[:200]} for r in old
    ]}, ensure_ascii=False, indent=2))


def cmd_apply(summary: str, ids: str):
    want = {int(x) for x in ids.replace(" ", "").split(",") if x.strip().isdigit()}
    if not want or not summary.strip():
        print("参数不全")
        return
    lock = _locked()
    try:
        rows = _load()
        keep, moved = [], []
        for r in rows:
            if r.get("id") in want and not r.get("summary_of"):
                moved.append(r)
            else:
                keep.append(r)
        if not moved:
            print("没有可收的条目")
            return
        nid = max((r.get("id") or 0) for r in rows) + 1
        rec = {
            "id": nid,
            "at": datetime.now(TZ).isoformat(timespec="milliseconds").replace("+08:00", "Z"),
            "session": "summarize",
            "summary_of": sorted(want),
            "text": summary.strip()[:600],
        }
        keep.append(rec)
        try:
            import shutil
            shutil.copy2(NOTES, NOTES + ".bak")
        except Exception:
            pass
        with open(NOTES, "w", encoding="utf-8") as f:
            for r in keep:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.chmod(NOTES, 0o600)
        with open(ARCHIVE, "a", encoding="utf-8") as f:
            for r in moved:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"收成 1 条摘要，归档 {len(moved)} 条细节（摘要 #{nid}）")
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    if sys.argv[1] == "list":
        cmd_list()
    elif sys.argv[1] == "apply" and len(sys.argv) >= 4:
        cmd_apply(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
