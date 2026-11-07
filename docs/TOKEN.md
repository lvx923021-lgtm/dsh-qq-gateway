# Token 账本与工具瘦身

## 钱花在哪

DeepSeek 的计费分三档，**输出最贵**：

| 项目 | 离峰 | 峰时 |
| --- | --- | --- |
| 输入·缓存命中 | $0.003 / 百万 | $0.006 |
| 输入·未命中 | $0.15 / 百万 | $0.30 |
| 输出 | $0.60 / 百万 | $1.20 |

峰时是 UTC 01–04、06–10 的工作日（北京时间 09:00–12:00、14:00–18:00）。

所以省 token 只有三条路：**少读、少想、少重读**。

## 我们的实测数字

同一句 `_`，在三种配置下量到的上下文大小：

| 配置 | totalTokens |
| --- | --- |
| 只装 base + headless（无插件） | 9,184 |
| 加 bili + heart 插件 | 15,547 |
| 加 `dsh-tool-search`（默认） | 11,329 |
| 加 core 白名单 | 10,976 |
| 再加本地 rerank | 10,995 |

**结论：插件带的工具定义占了约 41%。**折叠掉长尾之后回到 1.1 万，接近纯底座。

## 三个已经落地的省法

### 1. 思考档位

简单消息关掉 thinking（输出单价是输入的 4 倍）：

```
短句「在吗」        thinking=关   输出  58 tokens
长句「今天好累…」   thinking=开   输出 398 tokens
```

规则：`need_deep()` 为真才开；收拾句子、用工具查完再说话这两步永远关。

### 2. 本地应答

「在吗 / 在不在 / 人呢」直接本地回「在。」，**一次请求都不发**。同一句话五分钟内只本地答一次，避免像复读机。

### 3. 工具瘦身（`dsh-tool-search`）

- 长尾工具折叠到三个桥工具（`tool_search` / `tool_describe` / `tool_call`）后面；
- 常用的钉成 core（永远可见，不用搜索）：`heart_note` `heart_record` `heart_recall` `heart_summary` `bili_humanism_read`；
- **不要配远程 rerank**——那位留言里的 20 秒延迟就是它造成的。

### 版本坑

`dsh-tool-search@0.1.10` 要求 `@deepseek-ai/dsh-*` ≥ `0.2.1-rc.4`。在 0.2.1-rc.2 上会直接被拒绝安装。

**用 0.1.8**（peer 是 `^0.2.1-rc.2`）：

```bash
dsh plugin --profile headless add dsh-tool-search@0.1.8
```

## 本地 rerank

`tool_rerank.py` 提供一个兼容 `/v1/rerank` 的本地端点，BM25 打分，毫秒返回：

```bash
python3 tool_rerank.py &     # 127.0.0.1:3199
curl -s -X POST http://127.0.0.1:3199/v1/rerank \
  -d '{"query":"读视频字幕","documents":["bili_humanism_read 读取字幕","heart_note 记心事"],"top_n":2}'
# → {"results":[{"index":0,"relevance_score":7.09}]}
```

配置写在 `~/.dsh/dsh-tool-search.json`：

```json
{
  "version": 1,
  "scope": "user",
  "groups": [
    { "name": "bili", "prefixes": ["bili_humanism_", "bili_"] },
    { "name": "heart", "prefixes": ["heart_"] }
  ],
  "core": ["heart_note", "heart_record", "heart_recall", "heart_summary", "bili_humanism_read"],
  "matcher": {
    "endpoint": "http://127.0.0.1:3199/v1/rerank",
    "apiKey": "local",
    "model": "local-bm25",
    "topN": 20
  },
  "preload": { "enabled": false }
}
```

`supervise.sh` 会看着这个服务，掉了 60 秒内拉起。

## 怎么对账

每次 API 调用往 `tokens.log` 记一行：

```json
{"at":"2026-11-06T01:28:37+08:00","kind":"msg","thinking":true,"in":1317,"hit":0,"out":398}
```

`hit` 是缓存命中的输入 token。想看当天花了多少：

```bash
python3 - <<'EOF'
import json
rows=[json.loads(l) for l in open('/root/qq-gateway/tokens.log') if l.strip()]
print('调用', len(rows), '次 | 输入', sum(r['in'] or 0 for r in rows), '| 输出', sum(r['out'] or 0 for r in rows))
EOF
```

## 一天的估算（30–60 条消息）

```
输入 ≈ 76 万 tokens（DSH 约 40 次 × 2 万 + 快路约 30 次 × 2 千）
输出 ≈ 9 千 tokens
离峰 ≈ ¥0.3/天   峰时 ≈ ¥0.6/天   一个月 ≈ ¥9–18
```

数字是估算，精确值看 `tokens.log`。
