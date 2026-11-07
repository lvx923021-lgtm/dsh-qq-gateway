# 运维与排障

## 进程一览

| 进程 | 作用 | 谁看着它 | 判活方式 |
| --- | --- | --- | --- |
| `gateway.py` | 消息中转、记忆、心情 | 看门狗 | 进程 + 心跳文件（120s） |
| `watchdog.sh` | 看网关、QQ、DSHA | 互保层 | flock |
| `supervise.sh` | 看看门狗、DSH 宿主、rerank | 无（最外层） | flock |
| `dsh web` | GUI/会话宿主 | 互保层 | 进程 + 3080 端口 |
| `tool_rerank.py` | 工具搜索打分 | 互保层 | 3199 健康检查 |
| QQ / NapCat | 协议端 | 看门狗 | 进程 **或** OneBot 接口 |
| DSHA App | 设备桥（3090） | 看门狗 | 3090 探活 |

## 抢锁：等待式

```bash
exec 9>"$LOCK"; flock -w 10 9 || exit 0      # shell
```

```python
for _ in range(20):                           # python：等 10 秒
    try: fcntl.flock(lk, LOCK_EX | LOCK_NB); break
    except BlockingIOError: time.sleep(0.5)
```

语义：**抢不到不等于退出**。等待期间持有锁的进程若死了，锁由内核释放，等待者接手。锁随进程消亡自动释放，不会留下"没人敢启动"的僵尸状态。

## 常见故障

### 她不回消息

按这个顺序查：

```bash
cat /root/qq-gateway/gateway.pid            # 进程还在吗
tail -30 /root/qq-gateway/gateway.log       # 有没有 Traceback / WS 断开
curl -s -X POST http://127.0.0.1:3000/get_login_info -d '{}'   # OneBot 活着吗
```

最典型的三种：

| 日志里看到 | 原因 | 处理 |
| --- | --- | --- |
| `WS 断开: name '_xxx' is not defined` | 处理函数抛 NameError，整条连接断 | 修代码（用静态检查扫未定义全局） |
| `DSH 退出码 1: TRANSPORT ...` | 网络抖动 | 已内置重试一次，再失败降级直连 |
| 日志完全没新行 | 消息没进来（QQ 掉线 / 没连上 3090 的 OneBot） | 看 OneBot 接口 |

### DSH 宿主掉线

```bash
tail -20 /root/dsh-web.log                  # 看有没有 http://127.0.0.1:3080/?token=...
```

互保层会在 20 秒内拉起，并把新地址发到 QQ（token 可能变）。

**如果熄屏过久必掉**：那是 Android 的休眠冻结，不是程序崩溃。需要手动设置：

1. 设置 → 电池 → 后台耗电管理 → 给 DSHA「允许后台高耗电」
2. 设置 → 应用 → DSHA → 自启动、关联启动 全开
3. 设置 → 电池 → 睡眠模式 → 关闭或加例外

### DSHA 桥（3090）掉线

看门狗每 30 秒探一次，掉了用设备 shell 拉起：

```bash
adb-shell "am start -n dsh.android.client/com.deepseekharness.app.ui.MainActivity"
```

用设备 shell 而不是桥，因为桥本身已经不可用了。

### QQ 掉线

看门狗双层判活：先看进程，进程查不到再问接口，**两个都失败才算掉线**——避免 proot 里 `pgrep` 偶发读不到进程导致重复登录（对风控不友好）。

重新登录有 15 分钟冷却；检测到"等你扫码"就停下来等人，不无限重试。

## 日志在哪

| 文件 | 内容 |
| --- | --- |
| `gateway.log` | 消息收发、分流、失败原因 |
| `watchdog.log` | 拉起/判死记录 |
| `supervise.log` | 宿主与 rerank 的守护记录 |
| `boot-all.log` | 启动链 |
| `tokens.log` | 每次 API 调用的用量 |
| `compaction.log` | 上下文被压缩的记录 |
| `/root/dsh-web.log` | DSH 宿主启动与地址 |

## 手动操作

```bash
bash scripts/start-stack.sh        # 按优先级起：互保层 → 看门狗 → 网关
bash boot-all.sh                   # 完整启动链（含 QQ）
bash scripts/watchdog.sh           # 单独起看门狗
```

**不要用 `pkill -f xxx` 杀自己**：你的命令行里就包含这个字符串，会把自己杀掉。用 pid 文件：

```bash
kill "$(cat watchdog.pid)"
```

## 备份与恢复

```bash
tar czf memory-$(date +%F).tar.gz -C /root .dsh/heart .dsh/bili-humanism/human-world.md
tar xzf memory-YYYY-MM-DD.tar.gz -C /root      # 恢复
```

清空记忆（保留身份）：

```bash
rm -rf ~/.dsh/heart/* && mkdir -p ~/.dsh/heart/{raw,self}
rm -f qq-session.json history.json ctx.json
```
