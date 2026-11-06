#!/usr/bin/env python3
"""原话留底 + 按线索召回。

归档：~/.dsh/heart/raw/YYYY-MM-DD.jsonl，一行一轮
  {"at":"...","role":"he","text":"原话","channel":"qq"}
只追加，带文件锁；不做摘要、不改写。

召回：python3 memory_archive.py search "西安 擀面皮"
  按线索词去命中原话，返回最多 3 张候选卡（谁、什么时候、说了什么开头），
  不把正文塞进上下文——要看哪张，再取全文。
"""
import fcntl
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

DSH_HOME = os.environ.get("DSH_HOME", os.path.expanduser("~/.dsh"))
RAW_DIR = os.path.join(DSH_HOME, "heart", "raw")
LOCK = os.path.join(DSH_HOME, "heart", ".raw.lock")
TZ = timezone(timedelta(hours=8))
KEEP_DAYS = 180


def _lock():
    os.makedirs(RAW_DIR, exist_ok=True)
    f = open(LOCK, "w")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def append(role: str, text: str, channel: str = "qq", extra: dict | None = None) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    now = datetime.now(TZ)
    rec = {"at": now.isoformat(timespec="seconds"), "role": role, "text": text[:4000], "channel": channel}
    if extra:
        rec.update(extra)
    lk = _lock()
    try:
        path = os.path.join(RAW_DIR, now.strftime("%Y-%m-%d") + ".jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
        return path
    finally:
        fcntl.flock(lk, fcntl.LOCK_UN)
        lk.close()


def _terms(query: str):
    # 中文按 2~4 字滑窗切，英文/数字整词
    q = (query or "").strip()
    out = set(re.findall(r"[A-Za-z0-9_]{2,}", q))
    han = re.sub(r"[^\u4e00-\u9fff]", " ", q)
    for seg in han.split():
        for n in (2, 3):
            for i in range(0, max(1, len(seg) - n + 1)):
                out.add(seg[i : i + n])
    return {t for t in out if t}


def search(query: str, limit: int = 3):
    terms = _terms(query)
    if not terms:
        return []
    rows = []
    try:
        files = sorted(os.listdir(RAW_DIR))[-30:]
    except OSError:
        return []
    for name in files:
        if not name.endswith(".jsonl"):
            continue
        try:
            with open(os.path.join(RAW_DIR, name), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    text = r.get("text") or ""
                    hit = sum(1 for t in terms if t in text)
                    if hit:
                        rows.append((hit, r))
        except OSError:
            continue
    rows.sort(key=lambda x: (-x[0], x[1].get("at") or ""))
    picked, seen = [], set()
    for score, r in rows:
        key = (r.get("at"), (r.get("text") or "")[:40])
        if key in seen:
            continue
        seen.add(key)
        who = "他" if r.get("role") in ("he", "user") else "我"
        picked.append({
            "when": (r.get("at") or "")[:16],
            "who": who,
            "said": (r.get("text") or "")[:80],
            "score": score,
        })
        if len(picked) >= limit:
            break
    return picked


def prune():
    """删掉太老的日档（默认 180 天）。"""
    cutoff = (datetime.now(TZ) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    removed = 0
    try:
        for name in os.listdir(RAW_DIR):
            if name.endswith(".jsonl") and name[:10] < cutoff:
                os.remove(os.path.join(RAW_DIR, name))
                removed += 1
    except OSError:
        pass
    return removed


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "append":
        role = sys.argv[2]
        text = sys.argv[3]
        print(append(role, text) or "空内容")
    elif cmd == "search":
        q = " ".join(sys.argv[2:])
        for c in search(q):
            print(f"- {c['when']} {c['who']}说：{c['said']}")
    elif cmd == "prune":
        print(f"清掉 {prune()} 个旧日档")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
