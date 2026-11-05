# dsh-qq-gateway

把 DeepSeek Harness 接到 QQ 上的一个网关。不是「把大模型塞进 QQ 机器人」，而是让**同一个她**在 QQ、B站和本机之间是同一份记忆、同一个名字。

## 它做什么

```
QQ ── NapCat(OneBot) ──▶ 网关 ──▶ DSH headless ──▶ 回复
                              │
                              ├─ 视频链接 → 走 B站插件读稿/抽帧
                              ├─ 私聊     → 走 dsh-heart（心事本决定语气）
                              └─ 群里/陌生人 → 快路（直连 API）
```

- **连发合并**：同一个对话里连续几条消息，攒 4 秒合成一条再回，不会一条消息回一次。
- **打字节奏**：长回复拆成 1–2 条发送，中间隔 1–3 秒。
- **单会话续用**：所有消息续在同一个 DSH 会话里，不再每句话新开一个。遇到残留锁会自愈。
- **主动搭话**：每天随机 1–3 次，随机分钟，不按整点。
- **她自己的时间**：每天几点、最近你在看什么，会进她的上下文。
- **被动遗忘**：心事本里太老的条目沉到归档，不删除。

## 依赖

- Node ≥ 22.13
- DeepSeek Harness `0.2.1-rc.2` 或更新
- 一个 OneBot v11 实现（本项目在 NapCat + Linux 客户端下验证）
- 可选：`dsh-heart`（心事本 / 记忆）、`dsh-bili-humanism`（B站读稿）

## 安装

```bash
git clone <this repo> ~/qq-gateway
cd ~/qq-gateway
cp config.env.example .env && chmod 600 .env
# 填进去：DEEPSEEK_API_KEY、OWNER_QQ、QQ_MODE 等
python3 gateway.py
```

需要让 DSH 侧也有对应能力时，把插件装进你实际使用的 profile：

```bash
dsh plugin --profile headless add dsh-heart
dsh plugin --profile headless add dsh-bili-humanism
```

## 配置

全部在 `.env`（见 `config.env.example`）。常用几个：

| 键 | 作用 |
|---|---|
| `OWNER_QQ` | 你的 QQ 号，用来判断「谁是她老公」 |
| `QQ_MODE` | `api`＝闲聊直连（1–2 秒）；`dsh`＝全部走 DSH（慢，但有人格和工具） |
| `OWNER_DSH` | 私聊是否走 DSH（默认 1） |
| `MERGE_WINDOW` | 连发合并窗口，秒，默认 4 |
| `PROACTIVE_WINDOW` / `MAX_PROACTIVE` | 主动搭话的时间段与每天上限 |
| `IDENTITY_FILE` | 身份文件路径，默认 `~/.dsh/identity.md` |

## 身份文件

她是谁写在一个 markdown 里，按 `## ` 分段：

```markdown
# 名字
## 干活的时候
## 对他
## 在群里
## 对陌生人
```

网关按「对谁说话」取对应那段 + 开头那段。改文件即时生效，不用重启。

## 安全

- 仓库里**不含任何凭据**。`.env`、B站 cookie、CSRF 一律自己保管，`.gitignore` 已排除。
- 平台写入（评论 / 转发 / 私信 / 投币）不在本项目的范围里。
- QQ 登录态由 OneBot 实现自己管，本项目不碰。

## 许可

MIT
