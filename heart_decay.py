#!/usr/bin/env python3
"""心事本的被动遗忘：老的低信号条目沉到归档，不是抹掉。
规则（像人一样简单）：
- 最近 30 天的一定留；
- 30 天以前的，最多再留 100 条；
- 更早的移进 notes-archive.jsonl（只归档，不删除）。
被反复提起的条目会自然落在最近区，不用额外加权。
"""
import json
import os
import shutil
import time
from datetime import datetime, timedelta, timezone

HEART = "/root/.dsh/heart"
NOTES = os.path.join(HEART, "notes.jsonl")
ARCHIVE = os.path.join(HEART, "notes-archive.jsonl")
TZ = timezone(timedelta(hours=8))
KEEP_DAYS = 30
KEEP_OLD = 100


def main():
    if not os.path.exists(NOTES):
        print("没有心事本")
        return
    rows = []
    with open(NOTES, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    if not rows:
        print("心事本是空的")
        return

    cutoff = datetime.now(TZ) - timedelta(days=KEEP_DAYS)

    def when(r):
        try:
            return datetime.fromisoformat(r["at"].replace("Z", "+00:00"))
        except Exception:
            return datetime.now(TZ)

    rows.sort(key=when)
    old = [r for r in rows if when(r) < cutoff]
    recent = [r for r in rows if when(r) >= cutoff]

    keep_old = old[-KEEP_OLD:] if len(old) > KEEP_OLD else old
    archive = old[: len(old) - len(keep_old)] if len(keep_old) < len(old) else []

    if not archive:
        print(f"不用忘：共 {len(rows)} 条，最近 {len(recent)}，旧 {len(old)}")
        return

    shutil.copy2(NOTES, NOTES + ".bak")
    with open(ARCHIVE, "a", encoding="utf-8") as f:
        for r in archive:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(NOTES, "w", encoding="utf-8") as f:
        for r in keep_old + recent:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.chmod(NOTES, 0o600)
    print(f"沉下去 {len(archive)} 条，留下 {len(keep_old) + len(recent)} 条")


if __name__ == "__main__":
    main()
