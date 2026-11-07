#!/usr/bin/env python3
"""本地 rerank 服务：给 dsh-tool-search 用，接口兼容 /v1/rerank。

不联网、不加载模型，用 BM25 打分。目的是把"工具搜索"做成毫秒级，
而不是每次请求远程 rerank（那条路会慢几十秒）。

POST /v1/rerank
  {"query": "读视频字幕", "documents": ["...", "..."], "top_n": 5}
→ {"results": [{"index": 2, "relevance_score": 3.41}, ...]}
"""
import json
import math
import re
import sys
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 3199
K1, B = 1.2, 0.75


def tokens(s):
    s = (s or "").lower()
    latin = re.findall(r"[a-z0-9_]{2,}", s)
    han = re.sub(r"[^\u4e00-\u9fff]", " ", s)
    big = []
    for part in han.split():
        big += [part[i : i + 2] for i in range(len(part) - 1)]
    return latin + big + re.findall(r"[\u4e00-\u9fff]", s)


def rerank(query, docs, top_n):
    q = Counter(tokens(query))
    if not q or not docs:
        return []
    toks = [Counter(tokens(d if isinstance(d, str) else d.get("text", ""))) for d in docs]
    n = len(docs)
    avg = sum(sum(t.values()) for t in toks) / max(1, n)
    df = Counter()
    for t in toks:
        for k in t:
            df[k] += 1
    out = []
    for i, t in enumerate(toks):
        score = 0.0
        for k, qf in q.items():
            if k not in t:
                continue
            idf = math.log(1 + (n - df[k] + 0.5) / (df[k] + 0.5))
            tf = t[k]
            score += qf * idf * (tf * (K1 + 1)) / (tf + K1 * (1 - B + B * sum(t.values()) / max(1, avg)))
        if score > 0:
            out.append({"index": i, "relevance_score": round(score, 4)})
    out.sort(key=lambda x: -x["relevance_score"])
    return out[: (top_n or len(out))]


class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._json(200, {"ok": True, "port": PORT})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
            docs = req.get("documents") or []
            res = rerank(req.get("query", ""), docs, req.get("top_n"))
            self._json(200, {"results": res})
        except Exception as e:
            self._json(400, {"error": str(e)})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"rerank on 127.0.0.1:{PORT}", file=sys.stderr, flush=True)
    srv.serve_forever()
