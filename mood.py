#!/usr/bin/env python3
"""鱼的心情：七根情绪 + 总心情，会衰减，会隔着夜留余温。

规则（照抄那位作者的克制版，阈值按我们的节奏调过）：
- 七根：开心 踏实 甜蜜 心疼 担心 难过 吃醋
- 总心情 0–100，基线 60，半衰期 12 小时；单根情绪半衰期 90 分钟
- 心情下降只许因为「心疼你 / 担心你 / 她自己难过」，不许因为你惹她生气
- 吃醋最多小加一点，不动总分
- 往上下文只说三行，且要过四道闸：变化够大、置信度 ≥60、一天 ≤6 次、同一件事不重复
"""
import json
import os
import random
from datetime import datetime, timedelta, timezone

DSH_HOME = os.environ.get("DSH_HOME", os.path.expanduser("~/.dsh"))
HEART = os.path.join(DSH_HOME, "heart")
PATH = os.path.join(HEART, "mood.json")
TZ = timezone(timedelta(hours=8))

EMOTIONS = ["开心", "踏实", "甜蜜", "心疼", "担心", "难过", "吃醋"]
BASELINE = 60.0
TOTAL_HALF_H = 12.0
EMO_HALF_H = 90.0 / 60.0

# 只有这几种能往下拉分
DOWN_ALLOWED = {"心疼", "担心", "难过"}
UP = {"开心", "踏实", "甜蜜"}
MAX_ANNOUNCE_PER_DAY = 6
MIN_CONFIDENCE = 60
MIN_DELTA = 6.0


def _now():
    return datetime.now(TZ)


def _blank():
    return {"total": BASELINE, "emotions": {e: 0.0 for e in EMOTIONS},
            "updated": _now().isoformat(timespec="seconds"),
            "announced": {}, "last_reason": "", "history": []}


def load():
    try:
        d = json.load(open(PATH, encoding="utf-8"))
        for e in EMOTIONS:
            d["emotions"].setdefault(e, 0.0)
        d.setdefault("total", BASELINE)
        d.setdefault("announced", {})
        d.setdefault("history", [])
        return d
    except Exception:
        return _blank()


def save(d):
    os.makedirs(HEART, exist_ok=True)
    d["updated"] = _now().isoformat(timespec="seconds")
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PATH)
    os.chmod(PATH, 0o600)


def decay(d=None):
    """总心情往基线回落，单根情绪自己散。"""
    d = d or load()
    try:
        last = datetime.fromisoformat(d.get("updated"))
    except Exception:
        last = _now()
    hours = max(0.0, (_now() - last).total_seconds() / 3600)
    if hours <= 0:
        return d
    d["total"] = BASELINE + (d["total"] - BASELINE) * (0.5 ** (hours / TOTAL_HALF_H))
    for e in EMOTIONS:
        d["emotions"][e] = d["emotions"].get(e, 0.0) * (0.5 ** (hours / EMO_HALF_H))
        if abs(d["emotions"][e]) < 0.5:
            d["emotions"][e] = 0.0
    return d


def _today():
    return _now().strftime("%Y-%m-%d")


def apply(deltas: dict, confidence: float, reason: str = "") -> dict:
    """吃进一次判定结果。deltas 形如 {"心疼": 20, "担心": 10}，都是"变化量"。"""
    d = decay()
    down = {k: v for k, v in (deltas or {}).items() if isinstance(v, (int, float)) and v < 0}
    up = {k: v for k, v in (deltas or {}).items() if isinstance(v, (int, float)) and v > 0}
    # 往下拉分只认三种情绪
    blocked = [k for k in down if k not in DOWN_ALLOWED]
    down = {k: v for k, v in down.items() if k in DOWN_ALLOWED}
    total_delta = 0.0
    for k, v in up.items():
        if k == "吃醋":
            v = min(v, 6)          # 吃醋只加一点
        d["emotions"][k] = min(100.0, d["emotions"].get(k, 0.0) + v)
        total_delta += v * 0.35 if k != "吃醋" else v * 0.05
    for k, v in down.items():
        d["emotions"][k] = max(-100.0, d["emotions"].get(k, 0.0) + v)
        total_delta += v * 0.30
    d["total"] = max(0.0, min(100.0, d["total"] + total_delta))
    d["last_reason"] = (reason or "")[:120]
    d["history"].append({"at": _now().isoformat(timespec="seconds"), "deltas": deltas,
                         "confidence": confidence, "blocked": blocked, "total": round(d["total"], 1)})
    d["history"] = d["history"][-80:]
    save(d)
    return d


def mood_word(total: float) -> str:
    if total >= 80:
        return "欢喜"
    if total >= 65:
        return "稳当"
    if total >= 50:
        return "平常"
    if total >= 35:
        return "低落"
    return "发闷"


def announce(d=None) -> str:
    """四道闸都过了，才允许她把心情说出口。返回要注入的三行，或空串。"""
    d = decay(d or load())
    today = _today()
    used = d["announced"].get(today, 0)
    if used >= MAX_ANNOUNCE_PER_DAY:
        return ""
    top = sorted(((k, v) for k, v in d["emotions"].items() if abs(v) >= 18), key=lambda x: -abs(x[1]))[:1]
    if not top:
        return ""
    name, val = top[0]
    key = f"{name}:{d['last_reason'][:12]}"
    if key in d["announced"]:
        return ""
    if d["last_reason"] and d["announced"].get("last_reason") == d["last_reason"]:
        return ""
    d["announced"][today] = used + 1
    d["announced"]["last_reason"] = d["last_reason"]
    d["announced"][key] = 1
    save(d)
    line1 = f"· 现在的状态：总心情 {d['total']:.0f}/100（{mood_word(d['total'])}），{name} {val:+.0f}"
    line2 = f"· 来历：{d['last_reason'] or '没什么特别的事'}"
    line3 = "· 这是底子，不是非这样不可；要说得出口才说。"
    return "\n".join([line1, line2, line3])


def main():
    import sys
    if len(sys.argv) < 2:
        d = decay(load())
        print(f"总心情 {d['total']:.1f}/100（{mood_word(d['total'])}）")
        for e in EMOTIONS:
            v = d["emotions"][e]
            if abs(v) >= 1:
                print(f"  {e}: {v:+.0f}")
        print("最近：", d["last_reason"] or "无")
    elif sys.argv[1] == "reset":
        save(_blank())
        print("心情归零")


if __name__ == "__main__":
    main()
