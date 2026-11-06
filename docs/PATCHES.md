# 本地补丁记录

这两个补丁改在**别人的包或插件里**，升级就会丢。记在这里，丢了照着重打。

---

## 1. `dsh-bili-humanism` 抽帧：等间隔 → 按画面变化

**文件**：`node_modules/dsh-bili-humanism/scripts/media.py`（`frames` 分支）

**症状/动机**：等间隔抽帧（每 15 秒一张）在幻灯片、长镜头、一段没变化的画面上浪费额度，又会漏掉快速切换的片段。40 帧的名额可能在开头几秒就被塞满。

**改法**：
- 解码时把每帧缩成 96×54 灰度图，和**上一张保留的帧**算平均差；
- 差 ≥ 9 就留；另外加两条兜底：
  - `gap_max = interval`（再安静也不超过这么久不看一张）
  - `min_gap = interval / 6`（两张之间至少隔这么久，**防止连续运动把名额吃光**）
- manifest 里多记一个 `change` 字段（插件校验是按字段逐个查的，多一个不影响）。

**验证**：
```bash
# 30 秒、每 5 秒换一次色：旧逻辑 2 帧，新逻辑 6 帧
python scripts/media.py frames /tmp/test_change.mp4 /tmp/out --duration 30 --interval 15
```

> 只加变化判定不加 `min_gap` 会踩坑：实测一条 87 秒视频出现过 40 帧全挤在开头 0.27 秒。

---

## 2. `dsh-batch-tool-calls` 消息来源：v3 包装 → 产生方 kind

**文件**：`node_modules/dsh-batch-tool-calls/lib/index.js:60`

**症状**：
```
format v4 message requires a producer-owned source kind
```
上下文压缩后，这个插件要往会话里补一条"批量调用规则还在"的提醒，用的是旧格式：

```js
const PLUGIN_SOURCE = Object.freeze({ kind: 'plugin', plugin: name })   // v3 写法
```

DSH 的会话格式升到 v4 后，`source.kind` 必须是产生方自己的名字，`"plugin"` 这种包装被拒收。

**改法**：
```js
const PLUGIN_SOURCE = Object.freeze({ kind: name })   // → kind: "batch-tool-calls"
```

**验证**：
```bash
node --input-type=module -e "
const m = await import('./lib/index.js');
console.log(m.buildReassertMessage({enabled:true}).source);"
# { kind: 'batch-tool-calls', form: 'notice', summary: '...' }
```

---

## 打完补丁的通用检查

```bash
# 1) 语法
python3 -m py_compile scripts/media.py
node --check lib/index.js

# 2) 真跑一次，别只看"没报错"
# 3) 重启宿主，确认启动日志里没有新报错
```
