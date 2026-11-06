#!/usr/bin/env python3
"""按情绪找旧事：她说"好累"的时候，先想起上次"累"是因为什么。

用法：
  python3 memory_emotion.py "今天好累"

输出 JSON：命中的情绪、上次的原因片段、时间、权重。
不联网、不写任何东西。
"""
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

DSH_HOME = os.environ.get("DSH_HOME", os.path.expanduser("~/.dsh"))
HEART = os.path.join(DSH_HOME, "heart")
TZ = timezone(timedelta(hours=8))

# 情绪词 → 强度（1-3）。越具体越靠前。
EMO = {
    "崩": 3, "撑不住": 3, "难受": 3, "委屈": 3, "焦虑": 2, "孤独": 2,
    "累": 2, "烦": 2, "压力": 2, "失眠": 2, "疼": 2, "病": 2, "感冒": 2,
    "生气": 2, "怕": 2, "慌": 2,
    "开心": 1, "高兴": 1, "喜欢": 1,
}
# 原因常常跟在这些词后面
CAUSE_HINT = re.compile(r"(?:因为|由于|都是|被|在忙|那边|的事|的事堆|加班|考试|论文|答辩|项目|学生会|开会|作业|面试|家里)")


def _rows():
    out = []
    try:
        with open(os.path.join(HEART, "notes.jsonl"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    except OSError:
        pass
    return out


def _age_days(at: str) -> float:
    try:
        t = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        return max(0.0, (datetime.now(TZ) - t).total_seconds() / 86400)
    except Exception:
        return 30.0


def _cause(text: str, emo: str) -> str:
    """情绪词附近那句"因为什么"：优先挑带原因提示的句子。"""
    sents = [x.strip() for x in re.split(r"(?<=[。！？])", text) if x.strip()]
    idx = next((i for i, x in enumerate(sents) if emo in x), None)
    if idx is None:
        return ""
    window = sents[idx : idx + 3]
    best = next((x for x in window if CAUSE_HINT.search(x)), None)
    if best is None:
        best = window[-1] if len(window) > 1 else window[0]
    body = best.strip("」。，、 「」()（）")
    body = re.sub(r"^[^，。]{0,6}?(?:说|他|我)?[，、]?", "", body) if emo in body[:6] else body
    body = re.sub(r"^(?:又|也|还是|真的|有点|特别|好|是)", "", body)
    body = body.replace("他", "你").strip("，、 「」")
    return body[:56]


def detect(text: str):
    hits = [(w, s) for w, s in EMO.items() if w in (text or "")]
    if not hits:
        return None
    hits.sort(key=lambda x: -x[1])
    return hits[0][0]


def recall(text: str, limit: int = 2):
    emo = detect(text)
    if not emo:
        return {"emotion": None, "items": []}
    rows = _rows()
    # 同一情绪的旧事，按"提过几次 × 新鲜度 × 强度"定权重
    cand = []
    for r in rows:
        t = (r.get("text") or "")
        if emo not in t:
            continue
        w = EMO.get(emo, 1)
        age = _age_days(r.get("at", ""))
        weight = round(w * 2 + max(0.0, 3.0 - age / 7.0), 1)
        reason = _cause(t, emo)
        if not reason:
            continue
        kind = "thought" if "（我想的）" in t else "fact"
        reason = reason.replace("想的）", "").strip() if kind == "thought" else reason
        if kind == "thought":
            reason = "（我猜的）" + reason
        cand.append({
            "when": str(r.get("at"))[:16],
            "reason": reason[:60],
            "weight": weight,
            "kind": kind,
        })
    cand.sort(key=lambda x: -x["weight"])
    return {"emotion": emo, "items": cand[:limit]}


def main():
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    print(json.dumps(recall(q), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
