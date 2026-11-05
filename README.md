# dsh-qq-gateway

让 **DeepSeek Harness** 和 QQ 说话：不是「接个大模型当客服」，而是让**同一个她**在 QQ、B站、本机之间共用一份记忆、一个名字、一套作息。

单文件网关（`gateway.py`，约 900 行），Python 3.10+，只依赖 `aiohttp` 和 `websockets`。

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
    name: 小鱼
    callUser: 你
    memory: heart
    ta: ["qq:<你的QQ号>"]
    untagged: ta
    digestChars: 3000
```

### 5. 写身份文件

`~/.dsh/identity.md`，按 `## ` 分段：

```markdown
# 小鱼
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
