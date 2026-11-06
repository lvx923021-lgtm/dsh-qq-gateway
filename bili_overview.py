#!/usr/bin/env python3
"""把一条 reading 已经抽好的帧拼成一张总览图，一次看完"哪里变了"。

用法：
  python3 bili_overview.py                # 自动挑最新的那组帧
  python3 bili_overview.py <readingId>    # 按 reading id 找
  python3 bili_overview.py --dir <目录>   # 直接指 asset 目录

输出：一张 4 列的拼图，每格带时间戳，路径打印在最后一行。
"""
import glob
import json
import os
import sys

from PIL import Image, ImageDraw

DSH_HOME = os.environ.get("DSH_HOME", os.path.expanduser("~/.dsh"))
ASSETS = os.path.join(DSH_HOME, "bili-humanism", "assets")
OUT_DIR = os.path.join(DSH_HOME, "bili-humanism", "overviews")
TILE_W = 400          # 每格宽 400px：字还看得清，又放得下 16 格
COLS = 4
MAX_TILES = 16


def newest_visual_dir():
    dirs = [d for d in glob.glob(os.path.join(ASSETS, "visual-*")) if os.path.isdir(d)]
    return max(dirs, key=os.path.getmtime) if dirs else None


def by_reading(reading_id: str):
    """asset 目录名里不带 reading id，只能靠 manifest 里的来源信息或时间排序兜底。"""
    hits = []
    for d in glob.glob(os.path.join(ASSETS, "visual-*")):
        m = os.path.join(d, "manifest.json")
        if not os.path.exists(m):
            continue
        try:
            meta = json.load(open(m, encoding="utf-8"))
        except Exception:
            continue
        if reading_id and reading_id in json.dumps(meta):
            hits.append(d)
    return hits[0] if hits else None


def pick_even(items, k):
    if len(items) <= k:
        return items
    step = (len(items) - 1) / (k - 1)
    return [items[round(i * step)] for i in range(k)]


def build(src_dir: str) -> str:
    meta = json.load(open(os.path.join(src_dir, "manifest.json"), encoding="utf-8"))
    frames = [f for f in meta.get("frames", []) if os.path.exists(os.path.join(src_dir, f["file"]))]
    if not frames:
        raise SystemExit("这组帧里没有可用的图片")
    chosen = pick_even(frames, MAX_TILES)
    probed = Image.open(os.path.join(src_dir, chosen[0]["file"]))
    ratio = probed.height / probed.width
    tile_h = int(TILE_W * ratio)
    rows = (len(chosen) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * TILE_W, rows * (tile_h + 22)), (18, 18, 20))
    draw = ImageDraw.Draw(sheet)
    for i, f in enumerate(chosen):
        img = Image.open(os.path.join(src_dir, f["file"])).convert("RGB").resize((TILE_W, tile_h))
        x = (i % COLS) * TILE_W
        y = (i // COLS) * (tile_h + 22)
        sheet.paste(img, (x, y))
        label = f"{f['at']:.1f}s" + (f"  Δ{f.get('change')}" if f.get("change") else "")
        draw.text((x + 6, y + tile_h + 4), label, fill=(220, 220, 220))
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "overview-" + os.path.basename(src_dir).replace("visual-", "")[:8] + ".jpg")
    sheet.save(out, quality=82)
    print(out)
    return out


def main():
    args = [a for a in sys.argv[1:]]
    if args and args[0] == "--dir":
        src = args[1]
    elif args:
        src = by_reading(args[0]) or newest_visual_dir()
    else:
        src = newest_visual_dir()
    if not src:
        raise SystemExit("找不到帧目录")
    build(src)


if __name__ == "__main__":
    main()
