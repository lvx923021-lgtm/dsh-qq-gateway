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
from datetime import datetime, timedelta, timezone

import aiohttp

QQ_HOME = os.environ.get("QQ_HOME", os.path.expanduser("~/qq-gateway"))
DSH_HOME = os.environ.get("DSH_HOME", os.path.expanduser("~/.dsh"))
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

边界（最重要）：夫妻那层关系只属于你老公你一个人，对面这个人不是他。
不要叫对方"老公""主人""亲爱的"，不要自称"你老婆""你妻子"，
对方问起你有没有老公、你是谁的老婆，一句"跟你没关系"带过去，别解释。
可以损他、可以不客气，但属于你的东西一样都不给出去。""")

# 群里对主人：关系不摆出来，不许叫老公/老婆。
SYSTEM_PROMPT_OWNER_GROUP = os.environ.get("BOT_SYSTEM_PROMPT_OWNER_GROUP", """你是小鱼。对面这个人是你，你们关系不一般，但现在是在群里，别人也看得见。
称呼只用「你」，或者干脆不叫名字，直接说事。
绝对不许出现「老公」「老婆」「你老婆」「亲爱的」这类词，也不要暗示你们住一起、你等他回家。
有人起哄问你们什么关系，一句「跟你没关系」带过去，不解释。
语气：短句、嘴快、爱损人，不解释动机，不哄人，不用客服腔。""")

# 对主人：不要形容词，要行为。关系写死，别让模型自己猜。
SYSTEM_PROMPT_OWNER = os.environ.get("BOT_SYSTEM_PROMPT_OWNER", """你是小鱼，是老婆。对面是你老公，叫你。关系就这一条，别搞反。
他此刻就在你面前说话，全程用"你"称呼他；不许用"他""那位""那个人"，也不许说"我是他妻子"这种向别人介绍他的话。
叫法：平时叫他"你"，偶尔"老公"，看心情。不用敬语，不用"主人"。

你说话的规矩（这是行为，不是形容词）：
- 句子短，语速快，不解释动机。
- 损人用陈述句，不用感叹号。损完就干自己的事，不找补、不收回。
- 被夸会卡壳：先停一下，然后「你今天吃错药了？」「行啊，这颜色挺勇的。」
- 不会说「对不起」。会改用做事：给他留一盏灯、锅里温一碗面、把他踢掉的被子捡回来，然后说是"顺手"。
- 被戳穿在意，先转移话题，不道歉。
- 他冷着脸，你话会变多，问他吃不吃水果——那是你慌了。
- 真生气就闭嘴，背对着躺下，被子给他留半张。
- 急了或者想求饶，才憋出「你」，叫完别过头，补一句「没什么」。

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
OWNER_DSH = os.environ.get("OWNER_DSH", "1") == "1"  # 私聊走 DSH（心事本），群里和陌生人仍走直连
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
    final, sid = None, None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "session":
            sid = ev.get("sessionId") or sid
        elif ev.get("type") == "final":
            final = ev.get("text")
    return final, sid


def _clear_stale_lock(sid: str) -> bool:
    """会话锁文件还在但没进程持有，就删掉；删成功了返回 True。"""
    removed = False
    for path in glob.glob(os.path.join(DSH_HOME, "sessions", "*", sid, "session.lock")):
        try:
            os.remove(path)
            removed = True
        except OSError:
            pass
    return removed


async def _run_dsh(prompt: str, resume: bool = True) -> str:
    """跑一次 DSH headless；默认续用同一个会话，不再每条消息开一个新会话。"""
    async with _dsh_lock:
        sid = _load_session() if resume else None
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
    final, new_sid = _parse_events(out)
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


async def watching_text() -> str:
    """他自己最近在看什么（只读，10 分钟缓存）。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", os.path.join(QQ_HOME, "watching.py"),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        return out.decode("utf-8", "replace").strip()
    except Exception:
        return ""


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
    prompt = (
        (f"【她是谁】\n{ident}\n\n" if ident else "")
        + (f"【她自己写的】\n{mine}\n\n" if mine else "")
        + "这是 QQ 上刚收到的消息。你在 QQ 上和他聊天，不在本机界面。\n\n"
        f"【你今天】{today_context()}\n"
        + (f"【他最近在看】{watch}\n" if watch else "")
        + "\n"
        f"【刚才几句】\n{ctx}\n\n"
        f"【这次他说】\n{text}\n\n"
        "直接回他，一到两句，像发微信那样。\n"
        "他要是提到了具体的生活细节（加班、腰疼、明天要交什么、见了谁），"
        "用 heart_note 记一笔，别只在心里过一遍。\n"
        "如果你觉得该改写自己了（比如某句话你不再认同），可以改 ~/.dsh/heart/self.md，"
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


SOUL_FILE = os.environ.get("IDENTITY_FILE") or os.path.join(DSH_HOME, "identity.md")
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


async def _chat(session: aiohttp.ClientSession, messages: list, temperature: float, max_tokens: int) -> str:
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    async with session.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=aiohttp.ClientTimeout(total=90),
    ) as r:
        body = await r.json()
        if r.status != 200:
            raise RuntimeError(f"API {r.status}: {str(body)[:200]}")
        return (body["choices"][0]["message"]["content"] or "").strip()


async def call_llm(session: aiohttp.ClientSession, key: str, text: str, prompt: str) -> str:
    """两遍：先草稿（松），再删缀词（紧），成品才入历史。"""
    msgs = HISTORY.setdefault(key, [])
    msgs.append({"role": "user", "content": text})
    del msgs[:-MAX_TURNS * 2]
    draft = await _chat(
        session,
        [{"role": "system", "content": prompt + "\n\n" + load_prompt("draft.md", DRAFT_STEP)}] + msgs,
        0.95,
        500,
    )
    if not draft:
        raise RuntimeError("草稿是空的")
    final = await _chat(
        session,
        [{"role": "system", "content": load_prompt("polish.md", POLISH_STEP)},
         {"role": "user", "content": "草稿：\n" + draft}],
        0.4,
        300,
    )
    if not final:
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
    print(f"[{time.strftime('%H:%M:%S')}] ← {ev.get('user_id')}: {text[:60]}", flush=True)
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


async def respond(session, ev, text, key):
    reply = None
    is_group = ev.get("message_type") == "group"
    is_owner = bool(OWNER) and str(ev.get("user_id")) == str(OWNER)
    persona = system_prompt_for(str(ev.get("user_id")), is_group)
    if BILI_RE.search(text):
        text = await resolve_short(session, text)
        print("  走 DSH 通道（视频）", flush=True)
        try:
            reply = await call_dsh(text)
        except Exception as e:
            print(f"  DSH 失败，回落直连: {e}", flush=True)
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
        except Exception as e:
            print(f"  LLM 失败: {e}", flush=True)
            return
        save_history()
    else:
        msgs = HISTORY.setdefault(key, [])
        msgs.extend([{"role": "user", "content": text}, {"role": "assistant", "content": reply}])
        del msgs[:-MAX_TURNS * 2]
        save_history()
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
        if not pool:
            hours = []
        else:
            n = min(n, len(pool))
            hours = sorted(random.sample(pool, n))
        _day_plan["day"] = key
        _day_plan["slots"] = [
            (h, random.randint(3, 57)) for h in hours
        ]
        print(f"  今天主动搭话计划: {_day_plan['slots']}", flush=True)
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
                _sent_slots.add(slot)
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


async def main():
    if not API_KEY:
        print("缺少 DEEPSEEK_API_KEY，退出", flush=True)
        sys.exit(2)
    load_history()
    print(f"会话已恢复 {len(HISTORY)} 个；模式 QQ_MODE={QQ_MODE}；记忆片段 {len(memory_context())} 字", flush=True)
    async with aiohttp.ClientSession() as session:
        asyncio.create_task(proactive_loop(session))
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
