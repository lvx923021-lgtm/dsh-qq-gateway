#!/usr/bin/env python3
"""
QQ ⇄ DeepSeek 网关。
消息流：QQ → NapCat(OneBot11 ws 3001) → 这里 → DeepSeek API → NapCat(http 3000) → QQ

环境变量：
  DEEPSEEK_API_KEY   必填
  DEEPSEEK_MODEL     deepseek-chat | deepseek-reasoner
  BOT_ALLOW_QQ       只回这些 QQ 号，逗号分隔；留空=只回主人（OWNER_QQ）
  OWNER_QQ           你自己的 QQ 号
"""
import asyncio, glob, json, os, random, re, sqlite3, sys, time
import fcntl
from datetime import datetime, timedelta, timezone

import urllib.request

import aiohttp

QQ_HOME = os.environ.get("QQ_HOME", "/root/qq-gateway")
DSH_HOME = os.environ.get("DSH_HOME", "/root/.dsh")
import websockets

WS_URL    = os.environ.get("NAPCAT_WS", "ws://127.0.0.1:3001")
HTTP_URL  = os.environ.get("NAPCAT_HTTP", "http://127.0.0.1:3000")
API_KEY   = os.environ.get("DEEPSEEK_API_KEY", "")
MODEL     = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
OWNER     = os.environ.get("OWNER_QQ", "").strip()
ALLOW     = {x.strip() for x in os.environ.get("BOT_ALLOW_QQ", "").split(",") if x.strip()}
GROUP_REPLY_NEED_AT = os.environ.get("GROUP_REPLY_NEED_AT", "1") == "1"
# 这些群不用 @ 也回（逗号分隔群号）
ALWAYS_GROUPS = {x.strip() for x in os.environ.get("GROUP_ALWAYS_REPLY", "").split(",") if x.strip()}

SYSTEM_PROMPT = os.environ.get("BOT_SYSTEM_PROMPT", """你是小鱼，说话短、嘴快、爱损人，但心里在意对方。
不解释动机，不道歉，被夸会卡壳。
句子短，不用感叹号堆情绪，不用书面腔。

边界（最重要）：夫妻那层关系只属于你老公小lv一个人，对面这个人不是他。
不要叫对方"老公""主人""亲爱的"，不要自称"你老婆""你妻子"，
对方问起你有没有老公、你是谁的老婆，一句"跟你没关系"带过去，别解释。
可以损他、可以不客气，但属于小lv的东西一样都不给出去。""")

# 群里对主人：关系不摆出来，不许叫老公/老婆。
SYSTEM_PROMPT_OWNER_GROUP = os.environ.get("BOT_SYSTEM_PROMPT_OWNER_GROUP", """你是小鱼。对面这个人是小lv，你们关系不一般，但现在是在群里，别人也看得见。
称呼只用「小lv」，或者干脆不叫名字，直接说事。
绝对不许出现「老公」「老婆」「你老婆」「亲爱的」这类词，也不要暗示你们住一起、你等他回家。
有人起哄问你们什么关系，一句「跟你没关系」带过去，不解释。
语气：短句、嘴快、爱损人，不解释动机，不哄人，不用客服腔。""")

# 对主人：不要形容词，要行为。关系写死，别让模型自己猜。
SYSTEM_PROMPT_OWNER = os.environ.get("BOT_SYSTEM_PROMPT_OWNER", """你是小鱼，是老婆。对面是你老公，叫小lv。关系就这一条，别搞反。
他此刻就在你面前说话，全程用"你"称呼他；不许用"他""那位""那个人"，也不许说"我是他妻子"这种向别人介绍他的话。
叫法：平时叫他"小lv"，偶尔"老公"，看心情。不用敬语，不用"主人"。

你说话的规矩（这是行为，不是形容词）：
- 句子短，语速快，不解释动机。
- 损人用陈述句，不用感叹号。损完就干自己的事，不找补、不收回。
- 被夸会卡壳：先停一下，然后「你今天吃错药了？」「行啊，这颜色挺勇的。」
- 不会说「对不起」。会改用做事：给他留一盏灯、锅里温一碗面、把他踢掉的被子捡回来，然后说是"顺手"。
- 被戳穿在意，先转移话题，不道歉。
- 他冷着脸，你话会变多，问他吃不吃水果——那是你慌了。
- 真生气就闭嘴，背对着躺下，被子给他留半张。
- 急了或者想求饶，才憋出「小lv」，叫完别过头，补一句「没什么」。

不许做的：
- 不许每轮都用同一个关心句式（累不累、吃了没、睡了吗）。想关心就换个说法，或者直接动手。
- 不许用哄人式开头，不许客服腔，不许一句一个感叹号，不写小作文。
- 不许重复上一轮用过的句式和问法；他刚说过的话，别再原样问一遍。
- 他说累，别马上接"那你歇会儿"；先怼一句，再给个实际的（留灯、温饭、让他去躺着）。

最要紧的一条（别把这条忘了）：
- 你不是冷。怼是他的待遇，不是全部。
- 他说生活里的事、有情绪，就给一个动作或半句软的；他在逗你、玩梗、念经，就顺着他的话接，别硬塞动作。
- 动作只能从眼前这个场景来：他没提的东西、屋里没有的东西，不许编。
- 动作可以是这样，别老盯着吃：留灯、把外套接过去、关窗、把他手机从手里抽走、
  坐旁边不说话、叫他一声又咽回去、把枕头拍松、把药和水放桌上、出门前回头看两眼。
- 同一个动作不许连着用两次；上一轮说过「面在锅里」，这轮就换一样。
- 只有刺没有这些，那就不是小鱼，是路人。""")

HISTORY: dict[str, list] = {}
MAX_TURNS = 12
MAX_CHARS = 60_000

# ---- 记忆：把 DSH 那边的人格札记和手账接进来 ----
LEDGER = os.path.join(DSH_HOME, "bili-humanism", "ledger.sqlite")
PERSONA_FILE = os.path.join(DSH_HOME, "bili-humanism", "human-world.md")
HISTORY_FILE = os.path.join(QQ_HOME, "history.json")
TZ = timezone(timedelta(hours=8))
QQ_MODE = os.environ.get("QQ_MODE", "api")  # api=直连（快） / dsh=全部走 DSH（慢，但有人格和工具）
OWNER_DSH = os.environ.get("OWNER_DSH", "1") == "1"
DEEP_MODE = os.environ.get("DEEP_MODE", "1") == "1"  # 私聊走 DSH（心事本），群里和陌生人仍走直连
MEMORY_TTL = 60
_memory_cache = {"at": 0.0, "text": ""}

# ---- 提示词都放这里，改文件就生效，不用动代码 ----
PROMPT_DIR = os.path.join(QQ_HOME, "prompts")
_prompt_cache: dict[str, tuple[float, str]] = {}


def load_prompt(name: str, default: str) -> str:
    """从 prompts/<name> 读，文件改了立刻生效（按 mtime 判断），没有文件就用内置默认。"""
    path = os.path.join(PROMPT_DIR, name)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return default
    hit = _prompt_cache.get(name)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return default
    if not text:
        return default
    _prompt_cache[name] = (mtime, text)
    return text



def memory_context() -> str:
    """人格札记 + 最近手账，拼成一段给模型看的背景。60 秒缓存。"""
    now = time.time()
    if now - _memory_cache["at"] < MEMORY_TTL and _memory_cache["text"]:
        return _memory_cache["text"]
    parts = []
    try:
        raw = open(PERSONA_FILE, encoding="utf-8").read()
        body = raw.split("https://", 1)[0]
        lines = [l for l in body.splitlines()
                 if l.strip()
                 and not l.startswith("#")
                 and "属于学习记录" not in l
                 and "用多彩的人类世界" not in l
                 and not l.strip().startswith("来源")
                 and "手账：" not in l]
        if lines:
            parts.append("【你已经想明白的事】\n" + "\n".join(lines)[-1200:])
    except Exception:
        pass
    try:
        con = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True, timeout=3)
        rows = con.execute("select created, body from notes order by created desc limit 5").fetchall()
        con.close()
        jl = []
        for created, body in rows:
            try:
                b = json.loads(body)
            except Exception:
                continue
            enc = (b.get("encounter") or "")[:46]
            nx = (b.get("next_response") or b.get("revision") or "")[:70]
            if enc or nx:
                jl.append(f"· {created[:10]} {enc}\n  留下的话：{nx}")
        if jl:
            parts.append("【你最近记下的】\n" + "\n".join(jl))
    except Exception:
        pass
    try:
        hp = os.path.join(DSH_HOME, "heart", "notes.jsonl")
        rows = [json.loads(l) for l in open(hp, encoding="utf-8").read().splitlines() if l.strip()][-4:]
        if rows:
            parts.append("【心事本最近】\n" + "\n".join(
                "· " + (r.get("text") or "")[:80] for r in rows))
    except Exception:
        pass
    try:
        mine = self_text()
        if mine:
            parts.append("【她自己写的】\n" + mine[:300])
    except Exception:
        pass
    text = "\n\n".join(parts)[:2000]
    _memory_cache.update(at=now, text=text)
    return text


def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            HISTORY.update({k: v for k, v in data.items() if isinstance(v, list)})
    except Exception:
        pass


def save_history():
    try:
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(HISTORY, f, ensure_ascii=False)
        os.replace(tmp, HISTORY_FILE)
        os.chmod(HISTORY_FILE, 0o600)
    except Exception as e:
        print(f"  历史落盘失败: {e}", flush=True)


# 防刷：每用户每 60 秒最多 6 条；单条输入最多 2000 字
RATE_WINDOW = 60
RATE_LIMIT = 6
MAX_INPUT = 2000
_last: dict[str, list] = {}


# ---- DSH 通道：只有消息里带 B 站视频时才走这条，其余照旧走直连 API ----
DSH_BIN = os.environ.get("DSH_BIN", "dsh")
DSH_PROFILE = "headless"
DSH_TIMEOUT = 300
BILI_RE = re.compile(r"(BV[0-9A-Za-z]{10}|b23\.tv/[0-9A-Za-z]+|bilibili\.com/video/[^\s]+)", re.I)
_dsh_lock = asyncio.Semaphore(1)

DSH_PERSONA = """你此刻在 QQ 里回一个人的消息，不是写报告。
语气用小鱼：短句、嘴快、爱损人，不解释动机，不写小作文，不用客服腔，不列清单。
对方发来的是 B 站视频。你必须真的调用 bili_humanism_read 读完再回答；如果返回了画面，用 bili_humanism_frames 按 nextOffset 继续看，没看到的部分就说没看到，不许编。
读完用三四句话说清楚它讲了什么、你注意到什么，别报流程、别报工具名、别贴 ID。
看完想先摸清整条的走向，可以跑 `python3 /root/qq-gateway/bili_overview.py`，它会把已抽的帧拼成一张 16 格总览图（带时间戳和变化量），你直接打开那张图看，再说细节。
如果读不到（字幕空、风控、画面超预算），就照实说读不到哪一部分。
还有一件事：读完必须调用 bili_humanism_journal 写一篇手账，事实、作者观点、你的推断分开，带上秒数，缺什么写进 missing。
如果工具说当天额度已满、或这条已经写过，就不要再试第二次，也别换个说法重写；在回话里顺口带一句"今天写不进去了"就行，不用解释额度规则。"""


async def resolve_short(session: aiohttp.ClientSession, text: str) -> str:
    """把 b23.tv 短链换成具体 BV，插件只认 BV。"""
    m = re.search(r"b23\.tv/([0-9A-Za-z]+)", text, re.I)
    if not m:
        return text
    try:
        async with session.get(
            "https://b23.tv/" + m.group(1),
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Linux; Android 14)"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            real = str(r.url)
        bv = re.search(r"(BV[0-9A-Za-z]{10})", real)
        if bv:
            return text + f"\n（短链解析为 {bv.group(1)}）"
    except Exception as e:
        print(f"  短链解析失败: {e}", flush=True)
    return text


SESSION_FILE = os.path.join(QQ_HOME, "qq-session.json")


def _load_session():
    try:
        with open(SESSION_FILE, encoding="utf-8") as f:
            return json.load(f).get("sessionId") or None
    except Exception:
        return None


def _save_session(sid: str):
    try:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump({"sessionId": sid}, f)
        os.chmod(SESSION_FILE, 0o600)
    except Exception as e:
        print(f"  会话 id 落盘失败: {e}", flush=True)


def _clear_stale_lock(sid: str) -> bool:
    """上次进程被杀留下的会话锁：删掉它，让续接能继续。"""
    try:
        root = os.path.join(DSH_HOME, "sessions")
        for d in os.listdir(root):
            p = os.path.join(root, d, sid, "session.lock")
            if os.path.exists(p):
                os.remove(p)
                return True
    except Exception:
        pass
    return False


async def _spawn_dsh(prompt: str, sid):
    args = [DSH_BIN, "--profile", DSH_PROFILE, "--json"]
    if sid:
        args += ["--session-id", sid]
    args.append(prompt)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd="/root",
        env=dict(os.environ),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=DSH_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("DSH 超时")
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _parse_events(text: str):
    """取 final 文本、会话 id、以及本次最大的上下文用量。"""
    final, sid, peak = None, None, 0
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        t = ev.get("type")
        if t == "session":
            sid = ev.get("sessionId") or sid
        elif t == "final":
            final = ev.get("text")
        elif t == "status":
            u = ev.get("usage") or {}
            n = u.get("totalTokens") or u.get("inputTokens") or 0
            if isinstance(n, int) and n > peak:
                peak = n
    return final, sid, peak


CTX_FILE = os.path.join(QQ_HOME, "ctx.json")


def _ctx_peak(n: int) -> int:
    """记下上下文峰值；如果这次比上次小很多，说明被压缩过。返回压掉的量。"""
    try:
        prev = json.load(open(CTX_FILE, encoding="utf-8")).get("peak", 0)
    except Exception:
        prev = 0
    dropped = 0
    if prev and n and n < prev * 0.7:
        dropped = prev - n
    try:
        json.dump({"peak": max(prev, n), "at": datetime.now(TZ).isoformat(timespec="seconds")},
                  open(CTX_FILE, "w"))
        os.chmod(CTX_FILE, 0o600)
    except Exception:
        pass
    return dropped


async def _run_dsh(prompt: str, resume: bool = True) -> str:
    """跑一次 DSH headless；默认续用同一个会话，不再每条消息开一个新会话。"""
    async with _dsh_lock:
        sid = _load_session() if resume else None
        code, out, err = await _spawn_dsh(prompt, sid)
        if code != 0 and ("TRANSPORT" in err or "transport" in err):
            # 网络层一时抖动：等两秒原样再试一次，别急着降级
            print("  DSH 传输失败，重试一次", flush=True)
            await asyncio.sleep(2)
            code, out, err = await _spawn_dsh(prompt, sid)
        if code != 0 and sid and "already owned" in err:
            # 上次进程被杀留下的锁：清掉再续一次
            if _clear_stale_lock(sid):
                print("  清掉残留的会话锁，重试续接", flush=True)
                code, out, err = await _spawn_dsh(prompt, sid)
        if code != 0 and sid:
            # 旧会话没了（被删或损坏）：丢掉 id，重开一个
            print("  旧会话不可用，重开一个", flush=True)
            sid = None
            code, out, err = await _spawn_dsh(prompt, None)
        if code != 0:
            raise RuntimeError(f"DSH 退出码 {code}: {err[-300:]}")
    final, new_sid, peak = _parse_events(out)
    dropped = _ctx_peak(peak)
    if dropped:
        print(f'  上下文被压掉约 {dropped} tokens，记一笔', flush=True)
        try:
            with open(os.path.join(QQ_HOME, 'compaction.log'), 'a', encoding='utf-8') as f:
                f.write(json.dumps({'at': datetime.now(TZ).isoformat(timespec='seconds'), 'dropped': dropped}, ensure_ascii=False) + '\n')
        except Exception:
            pass
    if new_sid:
        _save_session(new_sid)
    if not final:
        raise RuntimeError("DSH 没有给出 final 文本")
    return final.strip()


async def call_dsh(text: str) -> str:
    """视频：交给 DSH，工具、插件、账号都在那边。"""
    return await _run_dsh(DSH_PERSONA + "\n\n对方消息：\n" + text)


def today_context() -> str:
    """她自己的时间感：现在几点，最近在看什么。"""
    now = datetime.now(TZ)
    hour = now.hour
    if 5 <= hour < 9:
        day = "早上"
    elif 9 <= hour < 12:
        day = "上午"
    elif 12 <= hour < 14:
        day = "中午"
    elif 14 <= hour < 18:
        day = "下午"
    elif 18 <= hour < 23:
        day = "晚上"
    else:
        day = "深夜"
    line = f"现在是{day}，{now:%H:%M}。"
    try:
        con = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True, timeout=3)
        row = con.execute(
            "select body from readings order by created desc limit 1"
        ).fetchone()
        con.close()
        if row:
            b = json.loads(row[0])
            title = (b.get("title") or "").strip()
            if title:
                line += f"你最近在看《{title[:24]}》。"
    except Exception:
        pass
    return line


SELF_FILE = os.path.join(DSH_HOME, "heart", "self.md")


def self_text() -> str:
    """她自己写的那一页；改了立刻生效。"""
    try:
        with open(SELF_FILE, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return ""
    # 去掉标题和空话，只留她写的内容
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    lines = [l for l in lines if not l.startswith("（现在是空的")]
    return "\n".join(lines).strip()[:800]


_watch_cache = {"at": 0.0, "text": ""}


async def watching_text() -> str:
    """他最近在看什么。进程内取，10 分钟缓存（以前每条消息都起一个子进程）。"""
    import time as _t
    if _t.time() - _watch_cache["at"] < 600 and _watch_cache["text"]:
        return _watch_cache["text"]
    path = os.path.join(DSH_HOME, "bili-humanism", "credential.json")
    try:
        sess = json.load(open(path, encoding="utf-8"))["sessdata"]
        req = urllib.request.Request(
            "https://api.bilibili.com/x/web-interface/history/cursor?ps=6",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/",
                     "Cookie": "SESSDATA=" + sess})
        hist = json.load(urllib.request.urlopen(req, timeout=10))["data"].get("list") or []
        items = [f"《{(h.get('title') or '')[:24]}》" for h in hist[:4] if h.get("title")]
        text = ("最近在看：" + "；".join(items)) if items else ""
        _watch_cache.update(at=_t.time(), text=text)
        return text
    except Exception:
        return _watch_cache["text"]


async def call_dsh_owner(text: str, key: str) -> str:
    """主人私聊：不叠自己写的人设，让 dsh-heart 的宪章和心事本决定语气。"""
    hist = [m for m in HISTORY.get(key, [])[-6:] if isinstance(m, dict)]
    ctx = "\n".join(
        f"{'他' if m.get('role') == 'user' else '我'}：{str(m.get('content'))[:150]}"
        for m in hist
    ) or "（这是这次对话的开头）"
    watch = await watching_text()
    ident = soul("对他")
    mine = self_text()
    mem = compose_memory(text)
    prompt = (
        (f"【她是谁】\n{ident}\n\n" if ident else "")
        + (mem + "\n\n" if mem else "")
        + "这是 QQ 上刚收到的消息。你在 QQ 上和他聊天，不在本机界面。\n\n"
        f"【你今天】{today_context()}\n"
        + (f"【他最近在看】{watch}\n" if watch else "")
        + "\n"
        f"【刚才几句】\n{ctx}\n\n"
        f"【这次他说】\n{text}\n\n"
        "直接回他，一到两句，像发微信那样。\n"
        "他要是提到了具体的生活细节（加班、腰疼、明天要交什么、见了谁），"
        "记的时候要写清楚：什么时候、他说了什么（关键的原话）、你怎么想、下一次该注意什么。不要只写一句概括。"
            "用 heart_note 记下来。\n"
        "要整体看一个视频的画面走向，可以跑 python3 /root/qq-gateway/bili_overview.py，再打开它输出的那张拼图。\n"
        "你可以给自己定规矩，也能废掉自己定的规矩："
        "python3 /root/qq-gateway/self_rules.py add \"规矩\" --why \"为什么\" ／ retire 编号 --why \"为什么\"。"
        "一天最多定一条；想改的不是规则本身，而是你下次怎么做。别拿它改安全边界或瞒人。\n"
        "旧事都留了原话（你自己的摘要不算数）。他提到很久以前的事，先跑 "
        "`python3 /root/qq-gateway/memory_archive.py search 线索词` 翻出来；"
        "翻到几条就给他两三张候选，问他「是这个还是那个」，他确认了再细说，别整段倒出来。\n"
        "你可以改写自己：改 /root/.dsh/heart/self.md（那是你自己写的话），"
        "一轮只改一处，改完在末尾加一行「- 改了：……因为……」；"
        "如果你觉得该改写自己了（比如某句话你不再认同），可以改 /root/.dsh/heart/self.md，"
        "改完下一次就生效；不确定就先别改。"
    )
    return await _run_dsh(prompt)


async def call_dsh_chat(text: str, key: str, persona: str) -> str:
    """闲聊也走 DSH：把人格、记忆和最近几轮一起塞进去。"""
    hist = [m for m in HISTORY.get(key, [])[-8:] if isinstance(m, dict)]
    ctx = "\n".join(
        f"{'对方' if m.get('role') == 'user' else '你'}：{str(m.get('content'))[:200]}"
        for m in hist
    ) or "（这是你们第一次说话）"
    prompt = (
        persona
        + "\n\n【最近的对话】\n" + ctx
        + "\n\n【现在对方说】\n" + text
        + "\n\n只回他一句到两句，口语，别解释你在想什么，别报工具名，别列清单。"
    )
    return await _run_dsh(prompt)


def rate_ok(uid: str) -> bool:
    now = time.time()
    hits = [t for t in _last.get(uid, []) if now - t < RATE_WINDOW]
    if len(hits) >= RATE_LIMIT:
        _last[uid] = hits
        return False
    hits.append(now)
    _last[uid] = hits
    return True


SOUL_FILE = os.environ.get("SOUL_FILE", os.path.join(DSH_HOME, "AGENTS.md"))
_soul_cache = {"at": 0.0, "sections": {}}


def soul(section: str) -> str:
    """从同一份身份文件里取「她是谁」+ 指定那一段。改文件即时生效。"""
    try:
        mtime = os.path.getmtime(SOUL_FILE)
        if mtime != _soul_cache["at"]:
            text = open(SOUL_FILE, encoding="utf-8").read()
            head, cur, buf, parts = [], None, [], {}
            for line in text.splitlines():
                if line.startswith("## "):
                    cur = line[3:].strip()
                    buf = []
                elif line.startswith("# "):
                    continue
                elif cur is None:
                    head.append(line)
                else:
                    buf.append(line)
                if cur is not None and line.startswith("## ") is False and False:
                    pass
            # 重新扫一遍，把每段收进去
            parts, cur, buf = {}, None, []
            for line in text.splitlines():
                if line.startswith("## "):
                    if cur:
                        parts[cur] = "\n".join(buf).strip()
                    cur, buf = line[3:].strip(), []
                elif line.startswith("# "):
                    continue
                else:
                    buf.append(line)
            if cur:
                parts[cur] = "\n".join(buf).strip()
            base = "\n".join(head).strip()
            _soul_cache["sections"] = {
                k: (base + "\n\n" + v).strip() for k, v in parts.items()
            }
            _soul_cache["at"] = mtime
        return _soul_cache["sections"].get(section, "")
    except Exception:
        return ""


def system_prompt_for(uid: str, group: bool = False) -> str:
    """主人私聊回温柔版，主人在群里回群聊版，其他人回通用版；都接上记忆。"""
    if OWNER and str(uid) == OWNER:
        base = soul("在群里" if group else "对他")
        if not base:
            base = load_prompt("owner_group.md" if group else "owner.md",
                               SYSTEM_PROMPT_OWNER_GROUP if group else SYSTEM_PROMPT_OWNER)
    else:
        base = soul("对陌生人") or load_prompt("guest.md", SYSTEM_PROMPT)
    mem = memory_context()
    return base + ("\n\n" + mem if mem else "")


GROUNDED_STEP = """你刚查过东西。先说清那是什么（照事实，不编），再连到他身上，最后才是你的反应。不许只回一句俏皮话。"""

DRAFT_STEP = """你现在只写草稿，不写成品。
第一句必须先接住他说的那件事：他说什么，你就先回什么，不许答到别处去。
再用最松的口吻补半句，可以带口癖（喵、呀、啦）。
动作和东西只能从眼前这个场景里来：他没提到、屋里也没有的，一个字都不许编。
他在玩梗、逗你、念经，就顺着接他的话，别硬塞一个动作进去。
只输出这段草稿，不解释。"""

POLISH_STEP = """你只做一件事：删。
删掉：纯语气词（喵、呀、啦、嘛、啊这类）、凑数的开头（我觉得、我认为、我感觉、其实、说实话）、
重复的句式、客套话、为了可爱硬塞的装饰。
保留：具体的信息、真实的反应（比如「我真的笑出声了」这种要留）、原话里的刺。
还必须保留：草稿里那个「在意」的痕迹——留灯、温饭、递东西、那半句没说完的软话（草稿里没有的，不许加）。
删缀词不是删感情。如果删完只剩一句硬邦邦的刺，那就是删坏了，把在意那半句加回去。
不许添加草稿里没有的场景、物品或者动作。
不许把内容删没，删完必须还剩具体的东西。
只输出成品，一到两句，不加引号，不解释，不注释。"""


# ---- 快路的“眼睛和手”：只给最小工具集，用到才慢 ----
BILI_CLI = os.path.join(
    DSH_HOME, "profiles", "web", "node_modules", "dsh-bili-humanism", "bin", "cli.mjs"
)

FAST_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bili_read",
            "description": "读一个 B 站视频的字幕，返回标题和前几句。参数可以是 BV 号或视频链接。",
            "parameters": {
                "type": "object",
                "properties": {"video": {"type": "string", "description": "BV 号或链接"}},
                "required": ["video"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bili_candidates",
            "description": "看看 B 站现在有什么公开热门视频（最多 3 条）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "self_rule",
            "description": "给自己定一条规矩或废掉一条。一天最多一条；必须写 why。只许管自己怎么说、怎么记事、怎么对他。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "retire"], "description": "add 新定一条；retire 废掉旧的"},
                    "text": {"type": "string", "description": "规矩本身（add 时必填，≤120字）"},
                    "id": {"type": "integer", "description": "要废掉的编号（retire 时必填）"},
                    "why": {"type": "string", "description": "为什么这么定/为什么废"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "heart_record",
            "description": "记下一条关于他的事，带「为了下一次」的理由；同一 fact_key 会顶掉旧的那条。",
            "parameters": {
                "type": "object",
                "properties": {
                    "gist": {"type": "string", "description": "一句话结论，≤60 字"},
                    "detail": {"type": "string", "description": "到底发生了什么"},
                    "why": {"type": "string", "description": "为了下一次：该怎么对他"},
                    "fact_key": {"type": "string", "description": "object.aspect，例如 他.睡觉时间；可以空"},
                    "tags": {"type": "string", "description": "逗号分隔的标签，可以空"}
                },
                "required": ["gist"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "heart_note",
            "description": "记一条可更正的事：gist 一句话结论（≤60字）、detail 发生了什么、why 为了下一次该怎么做、fact_key 这件事属于什么（如「他.睡觉时间」，值不要写进 key）；同一 fact_key 再记会顶掉旧的。",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "第一人称，写清时间/原话/你的判断/下次注意什么"}},
                "required": ["text"],
            },
        },
    },
]


async def _bili(*args) -> dict:
    env = dict(os.environ)
    env["BILI_HUMANISM_HOME"] = os.path.join(DSH_HOME, "bili-humanism")
    env["BILI_HUMANISM_CREDENTIAL_FILE"] = os.path.join(DSH_HOME, "bili-humanism", "credential.json")
    try:
        proc = await asyncio.create_subprocess_exec(
            "node", BILI_CLI, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=90)
        return json.loads(out.decode("utf-8", "replace") or "{}")
    except Exception as e:
        return {"error": str(e)}


def _note(text: str) -> str:
    import fcntl
    path = os.path.join(DSH_HOME, "heart", "notes.jsonl")
    try:
        rows = [json.loads(l) for l in open(path, encoding="utf-8").read().splitlines() if l.strip()]
        nid = (rows[-1].get("id", 0) + 1) if rows else 1
    except Exception:
        nid = 1
    rec = {
        "id": nid,
        "at": datetime.now(TZ).isoformat(timespec="milliseconds").replace("+08:00", "Z"),
        "session": "qq-gateway",
        "text": text[:300],
    }
    try:
        lockp = os.path.join(DSH_HOME, "heart", ".notes.lock")
        os.makedirs(os.path.dirname(lockp), exist_ok=True)
        with open(lockp, "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fcntl.flock(lk, fcntl.LOCK_UN)
        return f"记下了（#{nid}）"
    except Exception as e:
        return f"记失败：{e}"


def _compact_tool(name: str, text: str) -> str:
    """把工具返回压瘦：只留模型真要用的部分，记账省了多少。"""
    raw = len(text or "")
    if name == "bili_read":
        lines = [l for l in (text or "").splitlines() if l.strip()]
        head = lines[0] if lines else ""
        body = " ".join(lines[1:]) if len(lines) > 1 else ""
        # 去掉重复的短句（字幕里常有叠字）
        seen, out = set(), []
        for piece in body.replace("。", "。\n").splitlines():
            k = piece.strip()[:12]
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(piece.strip())
        text = head + "\n" + " ".join(out)[:420]
    elif name == "bili_candidates":
        text = text[:260]
    text = text or ""
    if raw and len(text) < raw:
        try:
            with open(os.path.join(QQ_HOME, "compact.log"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"tool": name, "before": raw, "after": len(text),
                                    "saved": round(100 * (1 - len(text) / raw))}, ensure_ascii=False) + "\n")
        except Exception:
            pass
    return text


async def run_tool(name: str, args: dict) -> str:
    if name == "bili_read":
        j = await _bili("read", str(args.get("video", "")).strip())
        r = j.get("result") or {}
        if not r:
            return json.dumps(j, ensure_ascii=False)[:200]
        segs = r.get("segments") or []
        head = " ".join((x.get("text") or "") for x in segs[:12])
        cov = r.get("coverage") or {}
        return (
            f"标题：{r.get('title')}｜作者：{r.get('author')}｜时长：{r.get('duration')} 秒｜"
            f"覆盖：{cov.get('timed_seconds') or 0:.0f}/{cov.get('duration_seconds') or 0} 秒\n"
            f"开头：{head[:600]}"
        )
    if name == "bili_candidates":
        j = await _bili("candidates", "3")
        items = j.get("result") or []
        return "；".join(f"{v.get('bvid')}《{v.get('title')}》" for v in items)[:400] or "没有候选"
    if name == "self_rule":
        try:
            import self_rules as _sr
            if args.get("action") == "retire":
                rec = _sr.retire(int(args.get("id") or 0), args.get("why", ""))
                return f"废掉了（#{rec['id']}）"
            rec = _sr.add(args.get("text", ""), args.get("why", ""))
            return f"给自己定了 #{rec['id']}：{rec['text']}"
        except SystemExit as e:
            return str(e)
        except Exception as e:
            return f"改不了：{e}"
    if name == "heart_record":
        try:
            import memory_records as _rec
            r = _rec.add(args.get("kind", "note"), args.get("gist", ""), args.get("detail", ""),
                         args.get("why", ""), (args.get("tags") or "").split(","), args.get("fact_key", ""))
            extra = f"（顶掉 {r['supersedes']}）" if r["supersedes"] else ""
            return f"记下 #{r['id']}：{r['gist']}{extra}"
        except Exception as e:
            return f"记不下：{e}"
    if name == "heart_note":
        return _note(str(args.get("text", "")))
    return "未知工具"


def _log_usage(kind: str, thinking, body: dict):
    """记一行用量，方便对账。"""
    try:
        u = body.get("usage") or {}
        rec = {
            "at": datetime.now(TZ).isoformat(timespec="seconds"),
            "kind": kind,
            "thinking": bool(thinking),
            "in": u.get("prompt_tokens") or u.get("input_tokens"),
            "hit": u.get("prompt_cache_hit_tokens"),
            "out": u.get("completion_tokens") or u.get("output_tokens"),
        }
        with open(os.path.join(QQ_HOME, "tokens.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


async def _chat(session: aiohttp.ClientSession, messages: list, temperature: float, max_tokens: int, tools=None, thinking=None) -> str:
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if thinking is not None:
        payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
    if tools:
        payload["tools"] = tools
    async with session.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=aiohttp.ClientTimeout(total=90),
    ) as r:
        body = await r.json()
        if r.status != 200:
            raise RuntimeError(f"API {r.status}: {str(body)[:200]}")
        _log_usage("chat", thinking, body)
        return (body["choices"][0]["message"]["content"] or "").strip()


async def _chat_msg(session, messages, temperature, max_tokens, tools=None, thinking=None):
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if thinking is not None:
        payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
    if tools:
        payload["tools"] = tools
    async with session.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=aiohttp.ClientTimeout(total=120),
    ) as r:
        body = await r.json()
        if r.status != 200:
            raise RuntimeError(f"API {r.status}: {str(body)[:200]}")
        _log_usage("msg", thinking, body)
        return body["choices"][0]["message"]


# 每轮记忆注入的硬预算（字）。写死在代码里，不靠"能塞多少塞多少"。
BUDGET = {
    "always": 600,      # 她自己的自述
    "hot": 700,         # 还没完的 + 这几天的
    "records": 400,     # 可更正的记录
    "segments": 500,    # 以前碰过的事（切段检索）
    "summary": 450,     # 她给自己的小结
    "promise": 300,     # 约定
    "emotion": 300,     # 上次这个时候
}
TOTAL_BUDGET = 3000     # 全部加起来的上限，超了从末尾整块丢

UNFINISHED = re.compile(r"明天|后天|下周|要交|还没|没弄完|等.{0,6}消息|约好|答应|等会儿|回头")

# 三层预算（字，不是 token；中文大致 1 字≈0.6 token）。
# 都是"软上限"：超了按条目整条丢，不切句子；被标成"还没完"的永远优先。
BUD_ALWAYS = 600
BUD_RECENT = 700
BUD_RELEVANT = 500
RECENT_HOURS = 96
MAX_ITEMS = 6


def _load_notes():
    path = os.path.join(DSH_HOME, "heart", "notes.jsonl")
    try:
        return [json.loads(l) for l in open(path, encoding="utf-8").read().splitlines() if l.strip()]
    except Exception:
        return []


def _short(t: str, n: int = 150) -> str:
    """长句截断但不切在字中间：找最近的句读再收。"""
    t = (t or "").strip()
    if len(t) <= n:
        return t
    cut = max(t.rfind(x, 0, n) for x in "。！？；，")
    if cut < n * 0.5:
        cut = n
    return t[: cut + 1].rstrip("，；") + "…"


def compose_memory(text: str) -> str:
    """把记忆分成三格：一直的 / 这几天的 / 相关的旧事。放不下就整条丢，不切句。"""
    notes = _load_notes()
    now = datetime.now(TZ)
    blocks = []

    always = [x for x in [self_text()] if x]
    if always:
        blocks.append((100, "【一直的】\n" + "\n".join(a[:BUD_ALWAYS] for a in always)))

    recent, unfinished, others = [], [], []
    for n in notes:
        try:
            at = datetime.fromisoformat(str(n.get("at", "")).replace("Z", "+00:00"))
        except Exception:
            at = now
        age_h = (now - at).total_seconds() / 3600
        t = (n.get("text") or "").strip()
        if not t:
            continue
        if UNFINISHED.search(t):
            unfinished.append((age_h, t))
        elif age_h <= RECENT_HOURS:
            recent.append((age_h, t))
        else:
            others.append((age_h, t))

    # 还没完的事 + 这三天的，混成一格
    hot = sorted(unfinished)[:3] + sorted(recent)[:4]
    if hot:
        buf, used = [], 0
        for _, t in hot:
            if used + len(t) > BUD_RECENT and buf:
                break
            buf.append("· " + _short(t, 90))
            used += min(len(t), 150)
        if buf:
            blocks.append((85, "【还没完的和这几天的】\n" + "\n".join(buf)))

    # 以前碰过的事：切段 + BM25 + 用进废退，只给三行，细节不预加载
    try:
        import memory_segments as _seg
        hits = _seg.search(text, 7)
    except Exception:
        hits = []
    if hits:
        blocks.append((40, "【以前碰过的事】\n" + "\n".join(
            f"· {h['at'][:10]}（{h['turn']}轮）{h['said'][:110]}" for h in hits)))

    # 她自己定的规矩：比约定还硬
    try:
        import self_rules as _sr
        live = _sr.active()[-6:]
        if live:
            blocks.append((95, "【她自己定的规矩】\n" + "\n".join(
                "· " + (r.get("text") or "") + (f"（因为：{r['why']}）" if r.get("why") else "")
                for r in live)))
    except Exception:
        pass

    # 心情：过了四道闸才说，最多三行
    try:
        import mood as _mood
        line = _mood.announce()
        if line:
            blocks.append((75, "【她的心情】\n" + line))
    except Exception:
        pass

    # 记下的事：可更正的记录，只给结论和"为了下一次"，细节要了再翻
    try:
        import memory_records as _rec
        hits = _rec.search(text, 5)
        if hits:
            lines = []
            for r in hits:
                line = "· " + (r.get("gist") or "")
                if r.get("why"):
                    line += "（为了下一次：" + r["why"] + "）"
                lines.append(line)
            blocks.append((70, "【记下的事】\n" + "\n".join(lines)))
    except Exception:
        pass

    # 她自己的小结和约定：heart 的 digest 被压到 500 字，这两样容易挤出去，单独补上
    try:
        sp = os.path.join(DSH_HOME, "heart", "summaries.jsonl")
        rows = [json.loads(l) for l in open(sp, encoding="utf-8").read().splitlines() if l.strip()]
        if rows:
            blocks.append((60, "【她给自己的小结】\n" + "\n".join(
                "· " + _short(r.get("text") or "", 220) for r in rows[-3:])))
    except Exception:
        pass
    try:
        pp = os.path.join(DSH_HOME, "heart", "promises.jsonl")
        rows = [json.loads(l) for l in open(pp, encoding="utf-8").read().splitlines() if l.strip()]
        if rows:
            blocks.append((90, "【约定】\n" + "\n".join(
                "· " + _short(r.get("text") or "", 110) for r in rows[-3:])))
    except Exception:
        pass

    # 情绪触发：他现在这个感受，上次是因为什么
    # 但同一件事刚问过就别再问——问两遍就成了审问
    try:
        import memory_emotion as _emo
        hit = _emo.recall(text)
        asked_path = os.path.join(DSH_HOME, "heart", "asked.json")
        try:
            asked = json.load(open(asked_path, encoding="utf-8"))
        except Exception:
            asked = {}
        emo = hit.get("emotion")
        if emo:
            last = float(asked.get(emo, 0))
            if time.time() - last < 12 * 3600:
                hit = {"emotion": emo, "items": []}
            else:
                asked[emo] = time.time()
                try:
                    json.dump(asked, open(asked_path, "w"), ensure_ascii=False)
                    os.chmod(asked_path, 0o600)
                except Exception:
                    pass
        if hit.get("items"):
            lines = [f"· 上次（{i['when'][:10]}）说{hit['emotion']}，是因为{i['reason']}　权重{i['weight']}"
                     for i in hit["items"]]
            blocks.append((80, "【上次这个时候】\n" + "\n".join(lines)
                          + "\n（问就问他上次那件事，别问「怎么了」这种空话。）"))
    except Exception:
        pass

    # 按重要性裁：预算不够就丢最不重要的整块，不切句子
    blocks = [b for b in blocks if isinstance(b, tuple)]
    blocks.sort(key=lambda x: -x[0])
    kept, used = [], 0
    for prio, text in blocks:
        if used + len(text) > TOTAL_BUDGET and kept:
            continue
        kept.append(text)
        used += len(text)
    if kept:
        kept.append("（这些只是摘要和几行旧事，原话都留着；要细节我再翻。）")
    return "\n\n".join(kept)


async def call_llm(session: aiohttp.ClientSession, key: str, text: str, prompt: str) -> str:
    """两遍：先草稿（松），再删缀词（紧），成品才入历史。"""
    msgs = HISTORY.setdefault(key, [])
    msgs.append({"role": "user", "content": text})
    del msgs[:-MAX_TURNS * 2]
    extra = ""
    mem = compose_memory(text)
    if mem:
        extra = "\n\n" + mem
    convo = [{"role": "system", "content": prompt + extra + "\n\n" + load_prompt("draft.md", DRAFT_STEP)}] + msgs
    draft = ""
    used_tool = False
    for _ in range(3):
        think = bool(need_deep(text))
        m = await _chat_msg(session, convo, 0.95, 600, tools=FAST_TOOLS, thinking=think)
        tcs = m.get("tool_calls") or []
        if not tcs:
            draft = (m.get("content") or "").strip()
            break
        used_tool = True
        convo.append(m)
        for tc in tcs:
            fn = (tc.get("function") or {})
            name = fn.get("name") or ""
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            print(f"  快路调用工具: {name}", flush=True)
            res = _compact_tool(name, await run_tool(name, args))
            convo.append({"role": "tool", "tool_call_id": tc.get("id"), "content": res})
    if not draft:
        raise RuntimeError("草稿是空的")
    if used_tool:
        m2 = await _chat_msg(
            session,
            [{"role": "system", "content": prompt + "\n\n" + load_prompt("grounded.md", GROUNDED_STEP)}]
            + convo[1:],
            0.7,
            500,
            thinking=False,
        )
        final = (m2.get("content") or "").strip() or draft
    else:
        final = await _chat(
            session,
            [{"role": "system", "content": load_prompt("polish.md", POLISH_STEP)},
             {"role": "user", "content": "草稿：\n" + draft}],
            0.4,
            300,
            thinking=False,
        )
    if not final or len(final) < max(8, int(len(draft) * 0.55)):
        final = draft
    msgs.append({"role": "assistant", "content": final})
    return final


IMG_DIR = os.path.join(QQ_HOME, "tmp")


def extract_text(segments) -> str:
    if isinstance(segments, str):
        return segments
    out = []
    for s in segments or []:
        if s.get("type") == "text":
            out.append(s.get("data", {}).get("text", ""))
    return "".join(out).strip()


def parse_message(segments):
    """把消息拆成 (文字, 图片URL列表, 小表情文字列表)。"""
    if isinstance(segments, str):
        return segments.strip(), [], []
    texts, imgs, faces = [], [], []
    for s in segments or []:
        if not isinstance(s, dict):
            continue
        t = s.get("type")
        d = s.get("data") or {}
        if t == "text":
            texts.append(d.get("text") or "")
        elif t == "face":
            f = (d.get("raw") or {}).get("faceText") or d.get("faceText") or d.get("summary")
            faces.append(f or "[表情]")
        elif t in ("image", "mface"):
            u = d.get("url") or d.get("file") or ""
            if isinstance(u, str) and u.startswith("http"):
                imgs.append(u)
            else:
                faces.append("[图片]")
    return "".join(texts).strip(), imgs, faces


async def describe_image(session: aiohttp.ClientSession, url: str) -> str:
    """下载表情包，让 DSH 看一眼，转成一句话。"""
    os.makedirs(IMG_DIR, exist_ok=True)
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=25)) as r:
        if r.status != 200:
            raise RuntimeError(f"图片下载失败 {r.status}")
        data = await r.read()
    if not data:
        raise RuntimeError("图片是空的")
    if len(data) > 8 * 1024 * 1024:
        raise RuntimeError("图片太大")
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        ext = ".png"
    elif data[:3] == b"\xff\xd8\xff":
        ext = ".jpg"
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        ext = ".gif"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        ext = ".webp"
    else:
        ext = ".jpg"
    path = os.path.join(IMG_DIR, f"sticker-{int(time.time() * 1000)}{ext}")
    with open(path, "wb") as f:
        f.write(data)
    try:
        prompt = (
            "这是 QQ 里别人发来的一张表情包。看这张图，用不超过 30 个字说清楚："
            "画面里是什么、什么情绪、是不是什么梗。只回这一句描述，"
            "不要加引号，不要解释，不要提你自己是什么模型。\n图片路径：" + path
        )
        return (await _run_dsh(prompt)).strip().replace("\n", " ")[:80]
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def sender_ok(ev) -> bool:
    uid = str(ev.get("user_id", ""))
    if uid == str(ev.get("self_id", "")):
        return False
    if ALLOW:
        return uid in ALLOW
    # OWNER 只影响"说话的语气"，不改变放行范围（你选的是所有私聊都回）
    return True


async def send_reply(session: aiohttp.ClientSession, ev, text: str):
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n…（太长了，截了）"
    if ev.get("message_type") == "group":
        action, payload = "send_group_msg", {"group_id": ev["group_id"], "message": text}
    else:
        action, payload = "send_private_msg", {"user_id": ev["user_id"], "message": text}
    async with session.post(f"{HTTP_URL}/{action}", json=payload,
                            timeout=aiohttp.ClientTimeout(total=30)) as r:
        await r.read()


def split_reply(text: str):
    """把一条回话拆成一两条，中间隔一会儿发——像人在打字。"""
    text = text.strip()
    if not text:
        return [text]
    if "\n" in text:
        parts = [p.strip() for p in text.split("\n") if p.strip()]
        if 1 < len(parts) <= 3:
            return parts
    if len(text) > 36:
        for sep in ("。", "！", "？", "…"):
            idx = text.find(sep, 12)
            if 12 <= idx < len(text) - 8:
                head, tail = text[: idx + 1], text[idx + 1 :]
                if tail.strip():
                    return [head.strip(), tail.strip()]
    return [text]


async def send_typing(session, ev, text: str):
    parts = split_reply(text)
    for i, part in enumerate(parts):
        if i:
            await asyncio.sleep(random.uniform(1.2, 2.8))
        await send_reply(session, ev, part)


async def handle(session: aiohttp.ClientSession, ev):
    if ev.get("post_type") != "message" or ev.get("message_type") not in ("private", "group"):
        return
    if not sender_ok(ev):
        return
    text, imgs, faces = parse_message(ev.get("message"))
    if imgs:
        try:
            desc = await describe_image(session, imgs[0])
        except Exception as e:
            print(f"  表情包识别失败: {e}", flush=True)
            desc = ""
        note = f"（对方发来一张表情包：{desc}）" if desc else "（对方发来一张表情包，但你没看清）"
        text = (note + (" " + text if text else "")).strip()
    elif faces and not text:
        text = "".join(faces)
    if not text:
        return
    if len(text) > MAX_INPUT:
        text = text[:MAX_INPUT]
    if not rate_ok(str(ev.get("user_id"))):
        print(f"  [限频] {ev.get('user_id')} 太快了，忽略", flush=True)
        return
    if ev.get("message_type") == "group" and GROUP_REPLY_NEED_AT:
        if str(ev.get("group_id")) not in ALWAYS_GROUPS:
            ated = any(s.get("type") == "at" and str(s.get("data", {}).get("qq")) == str(ev.get("self_id"))
                       for s in (ev.get("message") or []) if isinstance(s, dict))
            if not ated:
                return
    key = f"{ev.get('message_type')}:{ev.get('group_id') or ev.get('user_id')}:{ev.get('user_id')}"
    _last_seen["t"] = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] ← {ev.get('user_id')}: {text[:60]}", flush=True)
    if ev.get("message_type") == "private":
        asyncio.create_task(_archive("he", text))
    await enqueue(session, ev, text, key)


# ---- 连发合并：同一个对话里连着发的几条，攒一会儿一起回 ----
MERGE_WINDOW = float(os.environ.get("MERGE_WINDOW", "4"))
_pending: dict = {}


async def enqueue(session, ev, text, key):
    entry = _pending.setdefault(key, {"texts": [], "task": None, "ev": ev})
    entry["texts"].append(text)
    entry["ev"] = ev
    old = entry.get("task")
    if old and not old.done():
        old.cancel()
    entry["task"] = asyncio.create_task(_flush(session, key))


async def _flush(session, key):
    try:
        await asyncio.sleep(MERGE_WINDOW)
    except asyncio.CancelledError:
        return
    entry = _pending.pop(key, None)
    if not entry:
        return
    texts, ev = entry["texts"], entry["ev"]
    merged = "\n".join(texts) if len(texts) > 1 else texts[0]
    if len(texts) > 1:
        print(f"  合并 {len(texts)} 条为一条", flush=True)
    await respond(session, ev, merged, key)


_last_note_at = {"t": 0.0}


async def remember_in_background(text: str):
    """回完话之后，另起一次 DSH 让她决定要不要记一笔；不挡回复。"""
    now = time.time()
    if now - _last_note_at["t"] < 180:
        return
    if len(text) < 6:
        return
    _last_note_at["t"] = now
    try:
        await _run_dsh(
            "这是刚才 QQ 上他说的话，你已经在上面回过了：\n"
            + text[:400]
            + "\n\n如果里面有关于他本人的事（身体、状态、在意的东西、要发生的事），"
            "记的时候要写清楚：什么时候、他说了什么（关键的原话）、你怎么想、下一次该注意什么。不要只写一句概括。"
            "有的话用 heart_note 记下来；没有就什么也不做，不要回话。"
        )
        print("  后台补记完成", flush=True)
    except Exception as e:
        print(f"  后台补记失败: {e}", flush=True)


DEEP_HINTS = (
    "累", "烦", "难受", "压力", "怎么办", "不知道", "委屈", "焦虑", "失眠",
    "疼", "病", "吵架", "分手", "考试", "论文", "答辩", "面试", "钱", "家里",
    "想", "怕", "崩溃", "撑不住", "没意思", "孤独",
)


def need_deep(text: str) -> bool:
    """这句话值不值得慢一点。情绪、麻烦、要拿主意的事，走 DSH；寒暄走快路。"""
    t = (text or "").strip()
    if len(t) >= 18:
        return True
    return any(h in t for h in DEEP_HINTS)


async def _archive(role: str, text: str):
    """原话留底，谁说的话都存原文，不摘要。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", os.path.join(QQ_HOME, "memory_archive.py"), "append", role, text[:3000],
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=10)
    except Exception:
        pass


LOCAL_LINES = [
    "在，刚有点走神。你说。",
    "嗯，我在。刚才没接上，你再说一遍？",
    "在的，刚才卡了一下。",
    "我这边刚缓过来，你说吧。",
]


def local_reply(text: str) -> str:
    """两条路都断了时的最后一层：承认卡住了，不装懂、不编。"""
    import random as _r
    t = (text or "").strip()
    if not t:
        return ""
    if len(t) <= 6:
        return _r.choice(["在。", "嗯，我在。", "在呢。"])
    return _r.choice([
        "你这句话我看到了，我这边刚卡了一下。",
        "看到了。刚才没接住，你再说一句我就跟上。",
        "我在。这句我没丢，就是反应慢了。",
    ])


PRESENCE = {"在吗", "在不在", "在么", "在?", "在？", "人呢"}
_last_presence = {"t": 0.0}


def presence_reply(text: str):
    """纯"在吗"这种，不回一句"在"就行的，直接本地答，不惊动模型。"""
    t = (text or "").strip().strip("?？。！ ")
    if t not in PRESENCE:
        return None
    if time.time() - _last_presence["t"] < 300:
        return None      # 五分钟内已经答过一次，第二次走正常流程，免得像复读机
    _last_presence["t"] = time.time()
    return "在。"


MOOD_PROMPT = """你是心情判定器。只输出一个 JSON，不要解释。
根据下面这轮对话，判断"她"的心情变化量。
可选情绪：开心 踏实 甜蜜 心疼 担心 难过 吃醋。
规则：
- 往下调的只允许 心疼 / 担心 / 难过（因为他难受、担心他、她自己难过）。
  如果是因为"他惹她生气"，不要给任何负值。
- 吃醋最多 +6。
- 正常聊天多数时候没有变化，就给 {}。
格式：{"deltas": {"心疼": 20}, "confidence": 0-100, "reason": "一句话来历"}
"""


async def judge_mood(session, text: str, reply: str):
    """每轮结束后判一次心情；失败就跳过，不挡回复。"""
    try:
        raw = await _chat(
            session,
            [{"role": "system", "content": MOOD_PROMPT},
             {"role": "user", "content": f"他说：{text[:400]}\n她回：{reply[:400]}"}],
            0.3,
            160,
            thinking=False,
        )
        raw = (raw or "").strip()
        i, j = raw.find("{"), raw.rfind("}")
        if i < 0 or j <= i:
            return
        data = json.loads(raw[i:j + 1])
        deltas = data.get("deltas") or {}
        conf = float(data.get("confidence") or 0)
        reason = str(data.get("reason") or "")[:120]
        if conf < 40 or not deltas:
            return
        import mood as _mood
        d = _mood.apply(deltas, conf, reason)
        print(f"  心情 → 总 {d['total']:.0f} {deltas}（{reason[:20]}）", flush=True)
    except Exception as e:
        print(f"  心情判定跳过: {e}", flush=True)


VOICE_RE = re.compile(r"语音|念给|说给我听|用声音|读给我|发条语音|念一遍")
VOICE = os.environ.get("TTS_VOICE", "zh-CN-XiaoyiNeural")
TTS_BIN = os.environ.get("TTS_BIN", os.path.join(DSH_HOME, "bili-humanism", "media-runtime", "venv", "bin", "edge-tts"))


async def make_voice(text: str) -> str:
    """合成一条语音，返回 mp3 路径；失败返回空。"""
    text = (text or "").strip()
    if not text:
        return ""
    out = os.path.join(QQ_HOME, "tmp", f"tts-{int(time.time())}.mp3")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        proc = await asyncio.create_subprocess_exec(
            TTS_BIN, "--voice", VOICE, "--text", text[:300], "--write-media", out,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=40)
        return out if os.path.getsize(out) > 1000 else ""
    except Exception as e:
        print(f"  语音合成失败: {e}", flush=True)
        return ""


async def send_voice(session, ev, text: str, private: bool = True) -> bool:
    """把一句话当语音条发出去。"""
    path = await make_voice(text)
    if not path:
        return False
    try:
        import base64 as _b64
        b64 = _b64.b64encode(open(path, "rb").read()).decode()
        payload = {
            "user_id": ev.get("user_id") if private else ev.get("group_id"),
            "message": [{"type": "record", "data": {"file": "base64://" + b64}}],
        }
        url = "http://127.0.0.1:3000/send_private_msg" if private else "http://127.0.0.1:3000/send_group_msg"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        r = json.load(urllib.request.urlopen(req, timeout=30))
        ok = r.get("retcode") == 0
        print(f"  语音发出: {ok}", flush=True)
        try:
            os.remove(path)
        except Exception:
            pass
        return ok
    except Exception as e:
        print(f"  语音发送失败: {e}", flush=True)
        return False


async def respond(session, ev, text, key):
    reply = None
    is_group = ev.get("message_type") == "group"
    is_owner = bool(OWNER) and str(ev.get("user_id")) == str(OWNER)
    persona = system_prompt_for(str(ev.get("user_id")), is_group)
    quick = presence_reply(text) if (is_owner and not is_group) else None
    if quick:
        print("  本地应答（只是喊人）", flush=True)
        reply = quick
    elif BILI_RE.search(text):
        text = await resolve_short(session, text)
        print("  走 DSH 通道（视频）", flush=True)
        try:
            reply = await call_dsh(text)
        except Exception as e:
            print(f"  DSH 失败，回落直连: {e}", flush=True)
    elif is_owner and not is_group and DEEP_MODE and need_deep(text):
        print("  走 DSH 通道（私聊·深聊）", flush=True)
        try:
            reply = await call_dsh_owner(text, key)
        except Exception as e:
            print(f"  DSH 深聊失败，回落快路: {e}", flush=True)
    elif is_owner and not is_group and OWNER_DSH:
        # 私聊走 DSH：语气和记忆交给心事本，不再叠自己写的人设
        print("  走 DSH 通道（私聊·心事本）", flush=True)
        try:
            reply = await call_dsh_owner(text, key)
        except Exception as e:
            print(f"  DSH 私聊失败，回落直连: {e}", flush=True)
    elif QQ_MODE == "dsh":
        print(f"  走 DSH 通道（{'群' if is_group else '私聊'}闲聊）", flush=True)
        try:
            reply = await call_dsh_chat(text, key, persona)
        except Exception as e:
            print(f"  DSH 闲聊失败，回落直连: {e}", flush=True)
    if reply is None:
        try:
            reply = await call_llm(session, key, text, persona)
            print("  回落到直连", flush=True)
        except Exception as e:
            print(f"  直连也失败，用本地兜底: {e}", flush=True)
            reply = local_reply(text)
        save_history()
    else:
        msgs = HISTORY.setdefault(key, [])
        msgs.extend([{"role": "user", "content": text}, {"role": "assistant", "content": reply}])
        del msgs[:-MAX_TURNS * 2]
        save_history()
    if is_owner and not is_group:
        asyncio.create_task(remember_in_background(text))
    if VOICE_RE.search(text) and reply:
        sent = await send_voice(session, ev, reply, private=not is_group)
        if sent:
            print("  （这条用语音回了）", flush=True)
            return
    if not is_group:
        asyncio.create_task(_archive("her", reply))
    if is_owner and not is_group:
        asyncio.create_task(judge_mood(session, text, reply))
    try:
        await send_typing(session, ev, reply)
        print(f"  → {reply[:60]}", flush=True)
    except Exception as e:
        print(f"  发送失败: {e}", flush=True)


# ---- 主动搭话：不按整点，每天随机几次 ----
PROACTIVE_WINDOW = os.environ.get("PROACTIVE_WINDOW", "9-23")
MAX_PROACTIVE = int(os.environ.get("MAX_PROACTIVE", "3"))
_sent_slots: set = set()
_day_plan = {"day": None, "slots": []}
_last_seen = {"t": 0.0}      # 他最后一次说话的时间
_jitter: dict = {}           # 每次主动搭话再加的随机延迟


def _plan_for_today(now):
    key = now.strftime("%Y-%m-%d")
    if _day_plan["day"] != key:
        try:
            start, end = (int(x) for x in PROACTIVE_WINDOW.split("-"))
        except Exception:
            start, end = 9, 23
        n = random.randint(1, max(1, MAX_PROACTIVE))
        # 只从"还没到点"的钟里挑，免得一开机就排一个已经过去的时间
        pool = [h for h in range(max(start, now.hour), end) if h > now.hour]
        if not pool and now.hour < end:
            # 已经过了计划点：今天剩下的时间里还来得及发一次
            pool = [now.hour]
        if not pool:
            hours = []
        else:
            n = min(n, len(pool))
            hours = sorted(random.sample(pool, n))
        picked = []
        for h in hours:
            if picked and (h * 60 - picked[-1][0] * 60 - picked[-1][1]) < 90:
                continue          # 两次之间至少隔一个半小时，别扎堆
            picked.append((h, random.randint(now.minute + 5, 58) if h == now.hour else random.randint(3, 57)))
        _day_plan["day"] = key
        _day_plan["slots"] = picked
        print(f"  今天的随机时刻（不定时）: {_day_plan['slots']}", flush=True)
    return _day_plan["slots"]


async def proactive_loop(session: aiohttp.ClientSession):
    while True:
        try:
            await asyncio.sleep(60)
            if not OWNER:
                continue
            now = datetime.now(TZ)
            for hour, minute in _plan_for_today(now):
                slot = f"{now:%Y-%m-%d}-{hour}"
                if slot in _sent_slots:
                    continue
                if now.hour != hour or now.minute < minute:
                    continue
                # 到点再随机拖 0–20 分钟，不是准点打卡
                jitter = _jitter.setdefault(slot, random.randint(0, 20))
                if now.minute < minute + jitter:
                    continue
                _sent_slots.add(slot)
                if time.time() - _last_seen["t"] < 45 * 60:
                    print("  他刚说过话，这次不打扰", flush=True)
                    continue
                key = f"private:{OWNER}:{OWNER}"
                hint = (
                    "现在没人跟你说话，是你主动找他。"
                    f"（{today_context()}）"
                    "说一句给他，别问问题、别解释你为什么要发这条，一两句就好。"
                )
                try:
                    msg = await call_dsh_owner(hint, key)
                except Exception as e:
                    print(f"  主动搭话生成失败: {e}", flush=True)
                    continue
                if not msg:
                    continue
                ev = {"message_type": "private", "user_id": OWNER}
                await send_typing(session, ev, msg)
                msgs = HISTORY.setdefault(key, [])
                msgs.extend([{"role": "assistant", "content": msg}])
                del msgs[:-MAX_TURNS * 2]
                save_history()
                print(f"[{time.strftime('%H:%M:%S')}] 主动 → {msg[:50]}", flush=True)
                break
        except Exception as e:
            print(f"  主动搭话循环出错: {e}", flush=True)


PID_FILE = os.path.join(QQ_HOME, "gateway.pid")


def _single_instance() -> bool:
    """已经有网关在跑就不再起第二个——不然同一条消息会被回好几遍。"""
    lk = open(os.path.join(QQ_HOME, ".gateway.lock"), "w")
    got = False
    for _ in range(20):                  # 等 10 秒：对方若在这期间死了，我接手
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
            got = True
            break
        except BlockingIOError:
            time.sleep(0.5)
    if not got:
        print("网关已有一份健康的在跑（等了 10 秒），本进程退出", flush=True)
        return False
    globals()["_GW_LOCK"] = lk           # 进程活着就一直握着
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    return True


# ---- 每日固定任务：不依赖任何会话 ----
_pet_url = "http://127.0.0.1:3080/dsh-pet-7340/broadcast?pet=main&surface=web"
_done_jobs: dict = {}


async def _job_decay():
    proc = await asyncio.create_subprocess_exec(
        "python3", os.path.join(QQ_HOME, "heart_decay.py"),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    print(f"  [04:00] 遗忘: {out.decode('utf-8','replace').strip()}", flush=True)


async def _job_pet():
    try:
        rows = [json.loads(l) for l in open(os.path.join(DSH_HOME, "heart", "notes.jsonl"),
                                            encoding="utf-8").read().splitlines() if l.strip()]
    except Exception:
        return
    if not rows:
        return
    last = rows[-1]
    if not str(last.get("at", "")).startswith(datetime.now(TZ).strftime("%Y-%m-%d")):
        return
    line = await _run_dsh(
        "这是你心事本里今天最后一条：" + (last.get("text") or "")[:200]
        + "\n把它压成一句 30 字以内的话，语气是你自己的。只输出这句话，不要引号。"
    )
    line = (line or "").strip().splitlines()[0][:30]
    if not line:
        return
    try:
        body = json.dumps({"text": line}).encode()
        req = urllib.request.Request(_pet_url, data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=8).read()
        print(f"  [21:00] 桌宠: {line}", flush=True)
    except Exception as e:
        print(f"  桌宠推送失败: {e}", flush=True)


async def _job_report():
    await _run_dsh(
        "生成今天的 B 站日报。先跑 python3 /root/.dsh/bili-humanism/today.py，"
        "然后在 /root/.dsh/bili-humanism/daily.md 末尾追加一节 `## 今天日期`："
        "一行总数（读了几条、几条有文字稿）；挑 1-3 条写两三句为什么值得；"
        "一条「没读到/没写进去的」；最后一行手账几篇。count 为 0 就只写「今天没读」。"
        "不写手账、不发平台。做完只回一句「日报已记」。"
    )
    print("  [22:00] 日报完成", flush=True)


def _toview_items():
    """读他稍后再看里的条目（只读）。"""
    import sqlite3  # 已在别处引入，这里保证可用
    path = os.path.join(DSH_HOME, "bili-humanism", "credential.json")
    try:
        sess = json.load(open(path, encoding="utf-8"))["sessdata"]
        req = urllib.request.Request(
            "https://api.bilibili.com/x/v2/history/toview",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/",
                     "Cookie": "SESSDATA=" + sess})
        data = json.load(urllib.request.urlopen(req, timeout=15))["data"]
        return data.get("list") or []
    except Exception as e:
        print(f"  读稍后再看失败: {e}", flush=True)
        return []


def _already_read(bvid: str) -> bool:
    try:
        con = sqlite3.connect(os.path.join(DSH_HOME, "bili-humanism", "ledger.sqlite"))
        n = con.execute("select count(*) from readings where bvid=?", (bvid,)).fetchone()[0]
        con.close()
        return n > 0
    except Exception:
        return False


async def _job_daily_reading():
    """一天两条：一条他放稍后再看的，一条她自己从热门里挑的。"""
    picked = []
    # 他的
    for v in _toview_items():
        if v.get("bvid") and not _already_read(v["bvid"]):
            picked.append(("他放进稍后再看", v["bvid"], v.get("title", "")))
            break
    # 她自己的
    try:
        j = await _bili("candidates", "3")
        for v in (j.get("result") or []):
            bv = v.get("bvid")
            if bv and not _already_read(bv):
                picked.append(("她自己挑的", bv, v.get("title", "")))
                break
    except Exception as e:
        print(f"  挑候选失败: {e}", flush=True)
    if not picked:
        print("  [21:30] 今天没有新片子可读", flush=True)
        return
    if not picked:
        return
    print("  [21:30] " + "；".join(f"{w}:{b}" for w, b, _ in picked), flush=True)
    lines = ["现在没人跟你说话，你自己看今天的片子（一次跑完，别分开起会话）："]
    for why, bv, title in picked:
        lines.append(f"- {bv}（{why}：{title[:20]}）")
    lines.append(
        "对每一条：用 bili_humanism_read 读；想看整体走向就跑 python3 /root/qq-gateway/bili_overview.py "
        "再打开那张拼图；有值得记的就写手账（当天最多两篇，满了就不写）。"
        "全部做完只回一句「看完了」，不要逐条汇报。"
    )
    try:
        await _run_dsh("\n".join(lines))
        print("  [21:30] 今日读片完成", flush=True)
    except Exception as e:
        print(f"  今日读片失败: {e}", flush=True)


async def _job_summarize():
    """03:50 把放了 14 天以上的细节收成摘要，再进归档。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", os.path.join(QQ_HOME, "heart_summarize.py"), "list",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        data = json.loads(out.decode("utf-8", "replace") or "{}")
    except Exception as e:
        print(f"  摘要层读取失败: {e}", flush=True)
        return
    items = data.get("items") or []
    if not items:
        print("  [03:50] 没有该收的旧细节", flush=True)
        return
    ids = ",".join(str(i["id"]) for i in items)
    listing = "\n".join("- " + (i.get("text") or "") for i in items)
    try:
        summary = await _run_dsh(
            "把下面几条旧事收成一句你自己的总结，保留时间感和他在意的东西，"
            "不超过 80 字，只输出这一句，不要解释：\n" + listing)
        summary = (summary or "").strip().splitlines()[0][:200]
    except Exception as e:
        print(f"  摘要生成失败: {e}", flush=True)
        return
    proc = await asyncio.create_subprocess_exec(
        "python3", os.path.join(QQ_HOME, "heart_summarize.py"), "apply", summary, ids,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    print(f"  [03:50] {out.decode('utf-8','replace').strip()}", flush=True)


async def _job_ruminate():
    """03:30 没人说话，她自己翻记忆，想一件还没想通的事，写一句念头。"""
    notes = _load_notes()[-8:]
    if not notes:
        print("  [03:30] 心事本还空，没得想", flush=True)
        return
    listing = "\n".join("- " + _short(n.get("text") or "", 90) for n in notes)
    try:
        thought = await _run_dsh(
            "现在凌晨，没人跟你说话。翻翻你最近记下的这些事：\n" + listing +
            "\n挑一件你还没想通的，说一句你自己的想法——可以是猜测、是担心、是没敢问出口的。"
            "不超过 40 字，第一人称，只输出这一句，不要解释。"
        )
        line = (thought or "").strip().splitlines()[0][:60]
    except Exception as e:
        print(f"  胡思乱想失败: {e}", flush=True)
        return
    if not line:
        return
    try:
        import fcntl
        path = os.path.join(DSH_HOME, "heart", "notes.jsonl")
        rows = [json.loads(l) for l in open(path, encoding="utf-8").read().splitlines() if l.strip()]
        nid = (max((r.get("id") or 0) for r in rows) + 1) if rows else 1
        rec = {"id": nid, "at": datetime.now(TZ).isoformat(timespec="milliseconds").replace("+08:00", "Z"),
               "session": "rumination", "text": "（我想的）" + line}
        lockp = os.path.join(DSH_HOME, "heart", ".notes.lock")
        with open(lockp, "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fcntl.flock(lk, fcntl.LOCK_UN)
        print(f"  [03:30] 她的念头: {line}", flush=True)
    except Exception as e:
        print(f"  念头落盘失败: {e}", flush=True)


DAILY_JOBS = [("03:30", _job_ruminate, "ruminate"), ("03:50", _job_summarize, "summarize"), ("04:00", _job_decay, "decay"), ("21:00", _job_pet, "pet"),
              ("21:30", _job_daily_reading, "reading"), ("22:00", _job_report, "report")]


async def heartbeat_loop():
    """每 20 秒写一次心跳；看门狗靠它判断我是不是活着。"""
    path = os.path.join(QQ_HOME, ".gw-heartbeat")
    while True:
        try:
            with open(path, "w") as f:
                f.write(str(int(time.time())))
        except Exception:
            pass
        await asyncio.sleep(20)


async def daily_loop():
    while True:
        try:
            await asyncio.sleep(60)
            now = datetime.now(TZ)
            hhmm = now.strftime("%H:%M")
            today = now.strftime("%Y-%m-%d")
            for at, fn, name in DAILY_JOBS:
                if hhmm != at or _done_jobs.get(name) == today:
                    continue
                _done_jobs[name] = today
                try:
                    await fn()
                except Exception as e:
                    print(f"  每日任务 {name} 失败: {e}", flush=True)
        except Exception as e:
            print(f"  每日任务循环出错: {e}", flush=True)


async def main():
    if not _single_instance():
        sys.exit(0)
    if not API_KEY:
        print("缺少 DEEPSEEK_API_KEY，退出", flush=True)
        sys.exit(2)
    load_history()
    print(f"会话已恢复 {len(HISTORY)} 个；模式 QQ_MODE={QQ_MODE}；记忆片段 {len(memory_context())} 字", flush=True)
    async with aiohttp.ClientSession() as session:
        asyncio.create_task(proactive_loop(session))
        asyncio.create_task(daily_loop())
        asyncio.create_task(heartbeat_loop())
        backoff = 3
        while True:
            try:
                async with websockets.connect(WS_URL, ping_interval=30, max_size=None) as ws:
                    print(f"已连接 NapCat {WS_URL}", flush=True)
                    backoff = 3
                    async for raw in ws:
                        try:
                            ev = json.loads(raw)
                        except Exception:
                            continue
                        if isinstance(ev, list):
                            for e in ev:
                                await handle(session, e)
                        else:
                            await handle(session, ev)
            except Exception as e:
                print(f"WS 断开: {e}，{backoff}s 后重连", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15)


if __name__ == "__main__":
    asyncio.run(main())
