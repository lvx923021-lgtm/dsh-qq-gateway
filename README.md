# dsh-qq-gateway

让 **DeepSeek Harness** 和 QQ 说话：不是「接个大模型当客服」，而是让**同一个她**在 QQ、B站、本机之间共用一份记忆、一个名字、一套作息。

单文件网关（`gateway.py`，约 900 行），Python 3.10+，只依赖 `aiohttp` 和 `websockets`。


---

## 目录

| 文档 | 讲什么 |
| --- | --- |
| **本页** | 它是什么、装起来要几步、踩过的坑 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 全景图、两条脑怎么分、一轮消息的完整路径 |
| [docs/MEMORY.md](docs/MEMORY.md) | 五层记忆、BM25+用进废退、可更正记录、每轮注入预算 |
| [docs/TOKEN.md](docs/TOKEN.md) | 钱花在哪、实测对照数、思考档位、工具瘦身、本地 rerank |
| [docs/OPS.md](docs/OPS.md) | 进程表、等待式抢锁、各类掉线怎么查、备份恢复 |
| [docs/PATCHES.md](docs/PATCHES.md) | 给两个第三方包打过的补丁（升级会丢） |

---

## 一、它是什么

一句话：**QQ 消息进得来，DSH 的智能体出得去，中间有记忆、有延迟、有边界。**

它解决四件事：

| 问题 | 它的做法 |
| --- | --- |
| 机器人一条消息回一次，用户连发四条就回四遍 | 连发合并窗口，攒够再回 |
| 每条消息新开一个会话，列表越来越长 | 单会话续用，锁残留自愈 |
| 回复永远是「一整段」 | 拆成 1–2 条发，中间有停顿 |
| 机器人只会等你说话 | 每天随机 1–3 次主动开口 |

## 二、它不是什么

- **不是**一个 QQ 客户端。登录、扫码、协议由 OneBot 实现（如 NapCat）负责。
- **不是**一个对话模型。它把话交给 DSH 或 DeepSeek API。
- **不含**任何平台写入能力：不评论、不转发、不私信、不发动态、不投币。仓库里没有这类代码。
- **不打包**任何第三方运行时或模型。

---

## 三、架构

```
                    ┌──────────────────────────────┐
   QQ 客户端 ──▶  OneBot 实现（NapCat 等）           │
                    │   WS  3001  ← 事件             │
                    │   HTTP 3000 ← 发消息           │
                    └──────────┬───────────────────┘
                               │
                    ┌──────────▼───────────────────┐
                    │        gateway.py            │
                    │                              │
                    │  ① 解析消息（文本/图片/表情）  │
                    │  ② 连发合并（MERGE_WINDOW）   │
                    │  ③ 路由                       │
                    │  ④ 打字节奏 / 主动搭话         │
                    └───┬──────────┬───────────┬───┘
                        │          │           │
             视频链接 ──┘          │           └── 群里/陌生人
                        │          │                    │
                        ▼          ▼                    ▼
              DSH headless   DSH headless         DeepSeek API
              + B站插件      + dsh-heart          （直连，1–2 秒）
              （读稿/抽帧）  （心事本定语气）
                        │
                        ▼
                 同一个 DSH 会话（session-id 持久化）
```

**为什么要两条路**：DSH 那条有人格、有工具、记得事，但一条消息 10–15 秒；直连那条快，但只有提示词。私聊值得慢，群里不值得。

---

## 四、消息流程

### 1. 私聊（他自己）

```
QQ 消息
 → WS 事件
 → 解析：文本 / 图片(下载→DSH 看一眼→转成一句话) / 表情(取 faceText)
 → 限频检查（60 秒 6 条）
 → 合并窗口：4 秒内同一个人再发，并入同一条（合并后打印「合并 N 条为一条」）
 → 路由到 OWNER_DSH 分支
 → 组装上下文：
       身份（IDENTITY_FILE 里「对他」那段）
     + 她自己写的一页（~/.dsh/heart/self.md）
     + 今天几点 + 最近在看什么（B站只读桥）
     + 最近 6 句对话
     + 这次他说的话
 → dsh --profile headless --json --session-id <持久 id> "<prompt>"
 → 解析 JSON 事件流里的 final 文本
 → 拆成 1–2 条发出，中间停 1.2–2.8 秒
```

### 2. 群里

- 默认必须 @ 才回（`GROUP_REPLY_NEED_AT=1`），`GROUP_ALWAYS_REPLY` 里的群例外。
- 用身份文件里「在群里」那段：同一张脸，但关系不摆台面。
- 走直连 API，不走 DSH。

### 3. 陌生人私聊

- 身份文件里「对陌生人」那段：还是她，但只到客气那层。
- 不走 DSH，不碰心事本。

### 4. 视频链接

- 命中 `BV…` / `b23.tv/…` / `bilibili.com/video/…` 就走这条路。
- 先跟随短链跳转解析出 BV（插件只认 BV，直接给短链会报 INVALID_VIDEO）。
- 交给 DSH + B站插件：读字幕 → 抽帧看图 → 写手账。
- 失败自动回落直连，不会没回应。

### 5. 主动搭话

- 每天开机时随机排 1–3 个钟点（`PROACTIVE_WINDOW`），分钟也随机。
- 到点读「她今天」和「最近你在看什么」，生成一句，自己发出去。
- 只排「还没到点」的钟，不会一开机就补发一条已经过时的话。

---

## 五、关键机制（带真实参数）

| 机制 | 参数 | 说明 |
| --- | --- | --- |
| 连发合并 | `MERGE_WINDOW=4` | 同对话 4 秒静默后合并；连发 4 条只回 1 次 |
| 单会话续用 | `qq-session.json` | 保存 DSH 会话 id，每条消息续同一个 |
| 锁自愈 | — | 遇到 `already owned` 先删残留 `session.lock` 再续 |
| 打字节奏 | 1.2–2.8 秒 | 长回复按换行或句号拆两条 |
| 限频 | 60 秒 6 条 | 单用户，防刷 |
| 单条输入 | 2000 字 | 超出截断 |
| 上下文窗口 | 12 轮 | 直连模式保留的对话轮数 |
| 记忆注入上限 | 2000 字 | 心事本 + 手账摘要拼进提示词的上限 |
| 遗忘 | 30 天 / 100 条 | 更老的沉到 `notes-archive.jsonl`，不删除 |
| 视觉 | 8 MB | 表情包下载上限，超了就说「没看清」 |

---

## 六、目录

```
qq-gateway/
├── gateway.py            主程序（消息解析、路由、回复、主动搭话）
├── watching.py           只读：他最近在 B 站看什么（10 分钟缓存）
├── heart_decay.py        心事本被动遗忘（建议每天 4:00 跑）
├── prompts/              身份模板（IDENTITY_FILE 缺失时兜底）
├── config.env.example    配置样例
└── .env                  你自己的配置（不进仓库）
```

运行时数据都在 `QQ_HOME` 和 `DSH_HOME`，不写在仓库里。

---

## 七、安装

### 1. 前置

- Python ≥ 3.10
- Node ≥ 22.13（DSH 要求）
- DSH `0.2.1-rc.2` 或更新
- 一个 OneBot v11 实现，开好 WS(3001) 与 HTTP(3000)，登录 QQ

### 2. 装依赖

```bash
pip install aiohttp websockets
```

### 3. 配置

```bash
git clone https://github.com/lvx923021-lgtm/dsh-qq-gateway ~/qq-gateway
cd ~/qq-gateway
cp config.env.example .env
chmod 600 .env
# 至少填：DEEPSEEK_API_KEY、OWNER_QQ
```

### 4. 给 DSH 装配套插件（可选，但这是灵魂）

```bash
dsh plugin --profile headless add dsh-heart          # 心事本 / 记忆 / 身份
dsh plugin --profile headless add dsh-bili-humanism  # B站读稿
```

`dsh-heart` 要在 profile 的 `cordis.patch.yml` 里配置：

```yaml
- id: dsh-heart
  config:
    name: 柔念依
    callUser: 你
    memory: heart
    ta: ["qq:<你的QQ号>"]
    untagged: ta
    digestChars: 3000
```

### 5. 写身份文件

`~/.dsh/identity.md`，按 `## ` 分段：

```markdown
# 柔念依
（她是谁）

## 对他
（对主的语气与边界）

## 在群里
## 对陌生人
```

网关按「对谁说话」取对应段 + 开头那段。改文件即时生效。

### 6. 起服务

```bash
python3 gateway.py
```

看日志应出现：

```
已连接 NapCat ws://127.0.0.1:3001
  今天主动搭话计划: [(10, 23), (19, 41)]
```

---

## 八、配置全表

| 键 | 默认 | 作用 |
| --- | --- | --- |
| `NAPCAT_WS` | `ws://127.0.0.1:3001` | OneBot WS 地址 |
| `NAPCAT_HTTP` | `http://127.0.0.1:3000` | OneBot HTTP 地址 |
| `DEEPSEEK_API_KEY` | — | 直连模式与 DSH 子进程都要 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 直连模式的模型 |
| `OWNER_QQ` | — | 谁被当作「他」 |
| `BOT_ALLOW_QQ` | 空 | 只回这些号；留空＝所有私聊 |
| `GROUP_REPLY_NEED_AT` | `1` | 群里是否必须 @ |
| `GROUP_ALWAYS_REPLY` | 空 | 不用 @ 的群 |
| `QQ_MODE` | `api` | `api`＝闲聊直连；`dsh`＝全部走 DSH |
| `OWNER_DSH` | `1` | 私聊是否走 DSH |
| `MERGE_WINDOW` | `4` | 连发合并秒数 |
| `PROACTIVE_WINDOW` | `9-23` | 主动搭话的小时区间 |
| `MAX_PROACTIVE` | `3` | 每天最多几次 |
| `IDENTITY_FILE` | `~/.dsh/identity.md` | 身份文件 |
| `DSH_BIN` | `dsh` | DSH 可执行文件 |
| `QQ_HOME` | `~/qq-gateway` | 运行时数据目录 |
| `DSH_HOME` | `~/.dsh` | DSH 主目录 |

---

## 九、我们踩过的坑

1. **`pkill -f "gateway.py"` 会杀掉自己**：命令行里含同样的字符串。用 pid。
2. **`session.lock` 残留**：进程被杀后锁还在，续会话永远报 `already owned`。已内置自愈，但别用 `kill -9`。
3. **QQ 客户端吃内存**：Linux 客户端 + NapCat 常年 1G 以上，内存紧张时会被系统直接杀掉，表现为「突然不回话」。查 `pgrep -x qq`，空就是死了。
4. **容器/app 重启会带走整条 QQ 链**：QQ、网关、看门狗一起没。需要自启钩子（本项目不含）。
5. **短链不能直接喂插件**：`b23.tv/xxx` 要自己跟一次跳转拿 BV。
6. **字幕轨可能坏**：有的稿子时间轴排到几十分钟、内容是别的视频的 AI 字幕，插件会以 `INVALID_TIMESTAMP` 拒收。那不是你代码的问题。
7. **长视频取不到画面**：480p 下超过约 10 分钟就撞 32MiB 媒体上限，只剩文字。
8. **`--session-id` 与 `--json` 一起用**：会话 id 在事件流第一条 `{"type":"session"}` 里。

---

## 十、隐私

- 仓库不含任何凭据；`.env`、`history.json`、`qq-session.json`、`watching.json` 均被忽略。
- `watching.py` 只读：最近观看、稍后再看。不发帖、不点赞、不收藏。
- 心事本与手账都在本地，不上传。

## 十一、许可

MIT。见 `LICENSE`。

---

## 十、记忆是怎么长出来的

一个 QQ 机器人最容易失忆的地方不是「没存」，是**存了但没喂进去**。这套分五层：

| 层 | 文件 | 作用 |
| --- | --- | --- |
| 原话 | `~/.dsh/heart/raw/日期.jsonl` | 每轮原话只追加，一字不改 |
| 分段检索 | `memory_segments.py` | 按 20 分钟间隔/12 轮切段，BM25 + 时间衰减 + 用进废退（被想起过的更容易再被想起） |
| 可更正记录 | `memory_records.py` | `factKey=object.aspect`，同一件事变了写新行顶掉旧的（旧的不删，标 superseded）；每天最多 8 条；同一件事说第二遍只加固不新增 |
| 摘要 | `heart_summarize.py` | 14 天以上的细节收成一句，再沉进归档 |
| 情绪回忆 | `memory_emotion.py` | 他说「累」，先翻上次「累」是因为什么，问具体的事而不是「怎么了」；12 小时内问过就不追问 |

每轮注入按优先级拼装，超预算**整块丢、不切句**：

```
95 她自己定的规矩      ≤120 字/条，一天最多定一条
90 约定               说好的事
85 还没完的 + 这几天的
80 上次这个时候       情绪回忆
75 她的心情           过了四道闸才说
70 记下的事           可更正的记录
60 她给自己的小结
40 以前碰过的事       切段检索
```

---

## 十一、心情

七根情绪（开心/踏实/甜蜜/心疼/担心/难过/吃醋），总心情 0–100，基线 60。

- 总心情半衰期 **12 小时**（隔一夜还留余温），单根情绪 **90 分钟**；
- **往下调只允许因为心疼他／担心他／她自己难过**，因为他惹她生气一律不允许；
- 吃醋最多 +6，几乎不动总分；
- 想说出来要过四道闸：变化够大、置信度 ≥60、一天 ≤6 次、同一件事不重复。

每轮结束后一次很短的判定（关掉思考模式，输出一小段 JSON），失败就跳过，不挡回复。

---

## 十二、她能改自己

`self_rules.py` 是她的规矩本：

- 一天最多定 **1 条**，总数最多 40 条，每条 ≤120 字，且必须写 `why`；
- 拒收涉及**瞒人、删记录、改权限、绕过检查、冒充**的内容 —— 她能改"怎么做人"，不能改"边界在哪"；
- 只追加：废掉是写一条 retired 顶掉旧的，不删。

---

## 十三、语音

她说的话可以变成 QQ 语音条：

- `edge-tts` 合成 mp3（音色可换，默认 `zh-CN-XiaoyiNeural`）；
- base64 走 OneBot `record` 段发出去；
- 触发词：「语音 / 念给我听 / 说给我听 / 用声音回」。

---

## 十四、守护链

```
互保层 supervise.sh（20 秒）→ 看门狗 watchdog.sh（10 秒）
   ├ 网关：进程 + 心跳文件（120 秒不更新判死）
   ├ QQ：进程 或 OneBot 接口（两者都挂才算掉线，避免误判重复登录）
   └ DSHA App：3090 端口探活 → adb-shell 拉起
```

**抢锁是等待式的**：`flock -w 10`。抢不到不等于退出——这 10 秒里持有锁的进程若死了，锁由内核释放，等待者接手继续跑。

启动顺序用 `scripts/start-stack.sh` 串行：互保层 → 看门狗 → 网关，每步确认再走下一步。

---

## 十五、给别人的包打的两个补丁

见 [docs/PATCHES.md](docs/PATCHES.md)：

1. **抽帧从等间隔改成按画面变化**（`dsh-bili-humanism/scripts/media.py`）——避免 40 帧名额被开头几秒吃光；
2. **消息来源从 v3 包装改成产生方 kind**（`dsh-batch-tool-calls`）——修 `format v4 message requires a producer-owned source kind`。

---

## 十六、快速开始

```bash
# 1) 依赖
pip install aiohttp websockets

# 2) 配置
cp config.env.example .env && chmod 600 .env
$EDITOR .env            # 至少填 DEEPSEEK_API_KEY、OWNER_QQ、BOT_UIN

# 3) 起 OneBot（NapCat）
#    把 HTTP 端口开在 3000、WebSocket 在 3001

# 4) 启动
bash scripts/start-stack.sh     # 互保层 → 看门狗 → 网关
bash boot-all.sh                # 或者一次起完整链（含 QQ）
```

跑通了会看到：

```
[10:09:51] ← 123456789: 在吗
  本地应答（只是喊人）
```

## 十七、FAQ

**她很久不回消息？**
先看 `tail -30 gateway.log`。若见 `WS 断开: name ... is not defined`，是处理函数抛异常，整条连接被断。详见 [docs/OPS.md](docs/OPS.md#她不回消息)。

**为什么有时候 13 秒、有时候 2 秒？**
两条脑：私聊深聊走 DSH（13s），群里/寒暄走直连（2s）。分流规则在 `need_deep()`。

**能不能更省 token？**
能。已做：思考档位、本地应答、工具瘦身（`dsh-tool-search` 0.1.8 + core 白名单 + 本地 rerank）。实测从 15,547 降到 10,995。数字与做法见 [docs/TOKEN.md](docs/TOKEN.md)。

**她会不会越聊越贵？**
会，直到撞到 DSH 的压缩阈值。压缩后她会「不知道」自己缺了一块，所以代码里会记 `compaction.log`，并把"细节还能翻原话"写进注入。

**记忆存在哪？我能自己看吗？**
全部是本地纯文本，直接 `cat ~/.dsh/heart/raw/2026-11-07.jsonl`。清单见 [docs/MEMORY.md](docs/MEMORY.md)。

**熄屏一段时间 DSH 就掉？**
那是 Android 的休眠冻结。需要在系统设置里给 App 开「后台高耗电 / 自启动」，脚本改不动系统策略。见 [docs/OPS.md](docs/OPS.md#dsh-宿主掉线)。

**能商用吗？**
MIT。但插件依赖（`dsh-heart` / `dsh-bili-humanism` / `dsh-tool-search`）各自有自己的许可，第三方 QQ 协议端有风控风险，自己评估。
