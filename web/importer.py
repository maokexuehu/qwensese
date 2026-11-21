# -*- coding: utf-8 -*-
"""
把「现成的大段对话」变成一张角色卡。

流程：
  1) 本地解析：认说话人、统计篇幅、挑出最有代表性的原文台词、看角色怎么称呼对方
  2) 大文本切块 → 逐块让本地模型写摘要 → 再合成一张卡（map-reduce，避免爆上下文）
  3) 模型产出的字段和本地统计合并，缺的用原文补，产出可直接保存的草稿

不依赖任何第三方库。
"""

import json
import os
import re
import urllib.request

DEFAULT_MODEL = "qwensese-27b"
OLLAMA = "http://127.0.0.1:11434"

FIELDS = ["名字", "年龄", "身份", "与用户的关系", "前情提要", "外貌", "性格",
          "说话风格", "行为准则", "尺度", "禁忌", "开场白", "示例对话"]

# 单次直接分析的字数上限（超过就走切块摘要）
DIRECT_LIMIT = 12000
CHUNK_SIZE = 6000
MAX_CHUNKS = 10

SPEAKER_PATTERNS = [
    re.compile(r"^\s*\*\*([^*\n]{1,16})\*\*\s*[:：]\s*(.+)$"),      # **名字**：内容
    re.compile(r"^\s*【([^】\n]{1,16})】\s*[:：]?\s*(.+)$"),         # 【名字】内容
    re.compile(r"^\s*\[([^\]\n]{1,16})\]\s*[:：]?\s*(.+)$"),         # [名字] 内容
    re.compile(r"^\s*([^\s:：\[\]【】()（）]{1,12})\s*[:：]\s*(.+)$"),  # 名字：内容
]

# 常见"不是我"的说话人标记，用于自动判断谁是角色
NARRATOR_HINTS = {"旁白", "narrator", "system", "系统", "动作", "场景", "旁白君"}

QUOTE_MARK = re.compile(r"[“\"「『]([^”\"」』]{2,80})[”\"」』]")
ACTION_MARK = re.compile(r"[*＊]|（[^）]{2,40}）|\([^)]{2,40}\)")


# ------------------------------------------------------------------ 模型调用

def ollama_chat(messages, model=None, options=None, think=False, timeout=900):
    body = {
        "model": model or DEFAULT_MODEL,
        "messages": messages,
        "stream": False,
        "think": think,
        "keep_alive": "30m",
        "options": dict({
            "num_ctx": 16384,
            "num_predict": 1200,
            "temperature": 0.4,
            "top_p": 0.9,
            "repeat_penalty": 1.05,
        }, **(options or {})),
    }
    req = urllib.request.Request(
        OLLAMA + "/api/chat",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    r = json.loads(raw)
    return (r.get("message", {}).get("content") or "").strip(), r


def parse_json_block(text):
    """模型有时会包 ```json 或者多写几句，这里硬抠第一个平衡的 {…}"""
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc_next = False
    for i in range(start, len(t)):
        c = t[i]
        if in_str:
            if esc_next:
                esc_next = False
            elif c == "\\":
                esc_next = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except Exception:
                    return None
    return None


# ------------------------------------------------------------------ 本地解析

def split_speaker(line):
    for pat in SPEAKER_PATTERNS:
        m = pat.match(line)
        if m:
            name = m.group(1).strip()
            text = m.group(2).strip()
            # 排除 "时间：2024年" 这种明显不是说话人的
            if len(name) <= 12 and text and not re.match(r"^\d{2,4}[-/年]", text):
                return name, text
    return None, line.strip()


def parse_log(text, narrator=None):
    """返回 {'lines':[{speaker,text}], 'speakers':{name:统计}, 'order':[...]}"""
    lines = []
    last = None
    for raw in text.split("\n"):
        s = raw.strip()
        if not s:
            continue
        name, body = split_speaker(s)
        if not body:
            continue
        if name is None and last is not None:
            # 续行：一条消息里的第 2、3 段通常没有"名字："开头，算回上一个人
            last["text"] += "\n" + body
            continue
        ln = {"speaker": name, "text": body}
        lines.append(ln)
        last = ln

    stats = {}
    first_order = []
    for ln in lines:
        sp = ln["speaker"]
        if sp is None:
            continue
        if sp not in stats:
            first_order.append(sp)
        d = stats.setdefault(sp, {"name": sp, "count": 0, "chars": 0, "samples": []})
        d["count"] += 1
        d["chars"] += len(ln["text"])
        d["samples"].append(ln["text"])

    order = sorted(stats.values(), key=lambda d: (-d["count"], -d["chars"]))

    # 选"角色"和"用户"
    role, user = None, None
    candidates = [d for d in order if d["name"].lower() not in NARRATOR_HINTS]
    by_first = [stats[n] for n in first_order if n.lower() not in NARRATOR_HINTS]
    if narrator == "left" and by_first:
        user = by_first[0]
        role = by_first[1] if len(by_first) > 1 else None
    elif narrator == "right" and len(by_first) > 1:
        role = by_first[0]
        user = by_first[1]
    else:
        # auto：AI 扮演的那一方通常写字多得多，所以按"字数"选角色，而不是按条数。
        # （之前按条数选，遇到"你一句我一大段"的对话会把用户当成角色。）
        if candidates:
            by_chars = sorted(candidates, key=lambda d: -d["chars"])
            role = by_chars[0]
            user = by_chars[1] if len(by_chars) > 1 else None

    return {"lines": lines, "speakers": stats, "order": order, "role": role, "user": user}


def quote_samples(speaker_stat, n=3):
    """从角色台词里挑几句短的原文，比整条消息更适合当示例对话"""
    if not speaker_stat:
        return []
    out = []
    for txt in speaker_stat["samples"]:
        for q in QUOTE_MARK.findall(txt):
            q = q.strip()
            if 6 <= len(q) <= 90:
                out.append(q)
                if len(out) >= n:
                    return out
    return out


def pick_samples(speaker_stat, n=4):
    """挑最有代表性的台词：长句优先，但保留开头的一条"""
    if not speaker_stat:
        return []
    texts = speaker_stat["samples"]
    head = texts[0]
    longest = sorted(texts, key=lambda t: -len(t))[:n]
    out = [head]
    for t in longest:
        if t != head and t not in out:
            out.append(t)
        if len(out) >= n:
            break
    return out


def how_called(lines, role_name, user_name):
    """看角色怎么称呼用户（取引号里出现最多的称呼）"""
    # 用户方名字本身就是代词（"你"/"我"…）时，这套启发式没有意义，
    # 否则会把"你别上去"这种整句台词当成称呼。
    if not role_name or not user_name or user_name in ("你", "您", "我", "他", "她", "对方", "U", "u"):
        return ""
    cnt = {}
    for ln in lines:
        if ln["speaker"] != role_name:
            continue
        for q in QUOTE_MARK.findall(ln["text"]):
            if user_name in q and len(q) <= 8 and not re.search(r"[。，！？、…]", q):
                cnt[q] = cnt.get(q, 0) + 1
    if not cnt:
        return ""
    return sorted(cnt.items(), key=lambda kv: -kv[1])[0][0]


# 用词推测尺度用的关键词（宁可保守：只在出现得比较多时才往上抬）
EXPLICIT_WORDS = ["裸", "呻吟", "喘息", "插入", "抽动", "舔", "吻住", "下体", "湿润",
                  "性器", "射", "高潮", "乳", "大腿根", "脱下", "内衣", "体液"]


def estimate_level(lines):
    hits = 0
    for ln in lines:
        hits += sum(1 for w in EXPLICIT_WORDS if w in ln["text"])
    if hits >= 12:
        return "3"
    if hits >= 4:
        return "2"
    return "2"


def chunk_lines(lines, size=CHUNK_SIZE):
    chunks, cur, cur_len = [], [], 0
    for ln in lines:
        piece = (ln["speaker"] + "：" if ln["speaker"] else "") + ln["text"]
        if cur_len + len(piece) > size and cur:
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(piece)
        cur_len += len(piece) + 1
    if cur:
        chunks.append(cur)
    return chunks


def transcript(lines, limit=None):
    out = []
    total = 0
    for ln in lines:
        piece = (ln["speaker"] + "：" if ln["speaker"] else "") + ln["text"]
        out.append(piece)
        total += len(piece) + 1
        if limit and total > limit:
            break
    return "\n".join(out)


# ------------------------------------------------------------------ 提示词

SYS_ANALYZER = (
    "你是一个角色卡整理器。你的任务是从给定的对话文本里，提取出其中一个角色的人设，"
    "输出为严格的 JSON。你只做提取和归纳，不评价、不续写、不添加原文没有的信息。"
    "\n\n铁律（优先于下面所有写作要求）："
    "\n1. 只写这一个角色（或这一套系统）本身的静态设定：它是谁、长什么样、怎么说话、会怎么反应。"
    "\n2. 不写未来剧情：不写后续走向、不写「接下来会发生什么」、不写伏笔与计划。"
    "\n3. 不列 NPC 名单：不写配角表、势力成员表、出场角色表。原文里的其他角色，"
    "最多在「与用户的关系」里一句话带过，不要给他们建条目。"
    "\n4. 不写扩展模块：阶段表、场景库、世界观势力清单、任务列表这类东西一律不要。"
)

RAW_PROMPT = """下面是一段从网页或聊天软件里复制出来的文本。它**没有标注谁在说话**，或者标注很乱，
还可能夹着一些网页杂质（导航、按钮、点赞数、时间戳等，忽略它们即可）。

先判断，再整理：
- 哪一方是「角色」——要被扮演的那个人，通常名字出现得多、动作描写多、说话有特色
- 哪一方是「用户」——读者/代入方，常被对方称为"你"

=== 文本 ===
{text}

请输出这样的 JSON（字段名一字不差，值都是字符串）：
{{
  "名字": "", "年龄": "", "身份": "", "与用户的关系": "", "前情提要": "",
  "外貌": "", "性格": "", "说话风格": "", "行为准则": "", "禁忌": "",
  "开场白": "", "示例对话": ""
}}

写作要求：
- 名字：用文本里能确认的角色名；第一人称叙事就用角色自称的名字；实在没有填 "未命名角色"。
- 年龄：没写就填 "未提及"，不要猜。
- 身份、外貌、性格、行为准则：只写文本里能看出来的，看不出来填 "未提及"。
- 与用户的关系：陌生人/熟人/暧昧/情侣/上下级等，说明进展到哪一步。
- 前情提要：300 字以内，按时间顺序写已经发生过的事，用「你」指代用户方、用角色名指代角色。
  要具体到地点、时间、正在发生的事 —— 这是让模型能接着演下去的上下文。
- 说话风格：必须具体。句子长度、语气词、口头禅、称呼对方的固定说法、是否爱用 *动作描写*、标点习惯。可引用原文片段。
- 禁忌：角色明确排斥或回避的事；没有就填 "未提及"。
- 开场白：150 字以内，*动作* + "台词" 格式，能直接接着前情提要往下演。
- 示例对话：摘 2～3 句角色的原话，每句前加「角色名：」，保留原话不要改写。
- 所有字段都要出现。只输出 JSON，不要解释、不要代码块围栏。
- 只写这个角色本身的人设：**不要**写后续剧情走向，**不要**列 NPC/配角/势力名单，
  **不要**写阶段表、场景库这类扩展模块。原文里出现的其他角色，只在「与用户的关系」里一句话带过。
  任何字段的正文里都不要出现 `【世界观】` `【NPC 池】` `【场景库】` 这种小标题。"""

CHUNK_PROMPT = """这是一段角色扮演对话的第 {i}/{n} 段（共 {total} 字）。

请用 200 字以内提炼这几点，只输出总结本身，不要任何前缀：
1) 角色「{role}」在这段里的性格表现与情绪变化
2) 他/她的说话方式（句子长短、口癖、是否爱用动作描写、称呼对方的方式）
3) 与「{user}」的关系进展
4) 发生了什么关键情节

=== 文本 ===
{text}"""

FINAL_PROMPT = """下面是从一段长对话里提炼出来的材料。请据此整理出一张角色卡，输出 JSON。

对方角色的名字：{role}
与角色对话的人：{user}
角色说话 {role_count} 条 / {role_chars} 字；对方 {user_count} 条 / {user_chars} 字
{call_note}

材料：
{digest}

请输出这样的 JSON（字段名一字不差，值都是字符串）：
{{
  "名字": "", "年龄": "", "身份": "", "与用户的关系": "", "前情提要": "",
  "外貌": "", "性格": "", "说话风格": "", "行为准则": "", "禁忌": "",
  "开场白": "", "示例对话": ""
}}

写作要求：
- 名字：就用「{role}」。除非材料里明确写了别的名字。
- 年龄：材料没写就填 "未提及"，不要猜。
- 身份、外貌、性格、行为准则：从材料里能看出来的才写，看不出来填 "未提及"。
- 与用户的关系：写清是陌生人/熟人/暧昧/情侣/上下级等，并说明进展到哪一步。
- 前情提要：300 字以内，按时间顺序写已经发生过的事，用「你」指代对话另一方，用角色名指代角色。
  这是让模型能接着演的上下文，要具体到地点、时间、正在进行的事。
- 说话风格：必须具体。写清句子长度、常用语气词、口头禅、称呼对方的固定说法、
  是否习惯用 *动作描写*、标点习惯（比如爱用省略号）。可以直接引用原文片段说明。
- 禁忌：写角色明确排斥或回避的事；材料里没有就填 "未提及"。
- 开场白：写一段 150 字以内的开场，用 *动作* + "台词" 的格式，能直接接着前情提要往下演。
- 示例对话：直接摘 2～3 句原文台词，每句前加「{role}：」，保留原话不要改写。
- 所有字段都必须出现。只输出 JSON，不要解释，不要代码块围栏。
- 只写「{role}」本身的人设：**不要**写后续剧情走向，**不要**列 NPC/配角/势力名单，
  **不要**写阶段表、场景库这类扩展模块。材料里其他角色只在「与用户的关系」里一句话带过。
  任何字段的正文里都不要出现 `【世界观】` `【NPC 池】` `【场景库】` 这种小标题。"""


# ------------------------------------------------------------------ 卡片清洗
# 模型（或手工粘贴）经常把「NPC 池 / 场景库 / 阶段表 / 未来剧情」塞进字段里。
# 角色卡应该只有一个角色（或一套系统）的静态设定，所以这里整段剪掉。

BAD_SECTION = re.compile(
    r"(?m)^\s*#{0,2}\s*[【\[]?\s*(NPC|配角|出场角色|人物表|角色表|势力|阵营|场景库|阶段表|"
    r"世界观|剧情走向|后续剧情|未来剧情|任务)\s*[】\]]?\s*[:：]?")

NPC_LINE = re.compile(
    r"(?m)^\s*[-*•·]?\s*(?:NPC|NPC\s*池|配角|其他|其他角色|出场角色|人物|角色|路人|旁人)\s*[】\]]?\s*[:：]")


def strip_plot_and_npc(text):
    """返回 (清洗后的文本, 删掉的行数)"""
    if not text:
        return text, 0
    kept, dropped, cut = [], 0, False
    for line in text.split("\n"):
        if BAD_SECTION.match(line):
            cut = True
            dropped += 1
            continue
        if cut:
            # 一段模块到下一个标题或空行为止（模型输出里模块之间都有空行）
            if not line.strip() or line.startswith("#"):
                cut = False
            else:
                dropped += 1
                continue
        if NPC_LINE.match(line):
            dropped += 1
            continue
        kept.append(line)
    out = "\n".join(kept).strip()
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out, dropped


# ------------------------------------------------------------------ 主流程

def _heuristic_draft(parsed, note=""):
    role, user = parsed["role"], parsed["user"]
    rname = role["name"] if role else "未命名角色"
    samples = pick_samples(role, 4) if role else []
    call = how_called(parsed["lines"], rname, user["name"] if user else "")
    avg = int(role["chars"] / role["count"]) if role and role["count"] else 0
    actions = sum(1 for ln in parsed["lines"] if ln["speaker"] == rname and ACTION_MARK.search(ln["text"]))
    style_bits = ["平均每句约 %d 字" % avg]
    if role and role["count"]:
        style_bits.append("动作/神态描写占比约 %d%%" % int(actions * 100 / role["count"]))
    if call:
        style_bits.append("称呼对方为「%s」" % call)

    tail = transcript(parsed["lines"][-14:], 1200)
    return {
        "名字": rname,
        "年龄": "未提及",
        "身份": "未提及",
        "与用户的关系": ("与「%s」的对话关系" % user["name"]) if user else "未提及",
        "前情提要": ("（从导入对话的结尾自动摘录，建议手工精简）\n" + tail) if tail else "未提及",
        "外貌": "未提及",
        "性格": "未提及",
        "说话风格": "；".join(style_bits) + ("。" + note if note else ""),
        "行为准则": "未提及",
        "尺度": "2",
        "禁忌": "未提及",
        "开场白": "",
        "示例对话": "\n".join("%s：%s" % (rname, s) for s in samples),
    }


def _normalize(draft, parsed, warnings=None):
    role, user = parsed["role"], parsed["user"]
    rname = role["name"] if role else "未命名角色"
    out = {}
    stripped = 0
    for f in FIELDS:
        v = draft.get(f)
        v = (v if isinstance(v, str) else ("" if v is None else str(v))).strip()
        v, n = strip_plot_and_npc(v)
        stripped += n
        out[f] = v
    if stripped and warnings is not None:
        warnings.append("已剪掉字段里的 NPC/剧情/阶段表片段 %d 行（角色卡只保留人设本身）。" % stripped)
    if not out["名字"] or out["名字"] in ("未提及", "角色"):
        out["名字"] = rname
    if not re.fullmatch(r"[0-3]", out.get("尺度", "")):
        out["尺度"] = "2"
    # 示例对话一律用原文台词：分段摘要路径下模型看不到全文，它"引用"的其实是自己编的，
    # 那样的示例会把语气带偏。
    real = quote_samples(role, 3) or (pick_samples(role, 2) if role else [])
    if real:
        out["示例对话"] = "\n".join("%s：%s" % (rname, s) for s in real)
    if not out["说话风格"] or out["说话风格"] == "未提及":
        out["说话风格"] = _heuristic_draft(parsed)["说话风格"]
    return out


def _sample_text(text, head=6000, tail=3000):
    """长文本取头 + 尾：开头交代设定，结尾是当前剧情，比平均值有用得多"""
    text = text.strip()
    if len(text) <= head + tail:
        return text
    return text[:head] + "\n……（中间省略）……\n" + text[-tail:]


def _normalize_raw(draft, text, warnings=None):
    """整段文本（没有发言人标注）生成的草稿"""
    out = {}
    stripped = 0
    for f in FIELDS:
        v = draft.get(f)
        v = (v if isinstance(v, str) else ("" if v is None else str(v))).strip()
        v, n = strip_plot_and_npc(v)
        stripped += n
        out[f] = v
    if stripped and warnings is not None:
        warnings.append("已剪掉字段里的 NPC/剧情/阶段表片段 %d 行（角色卡只保留人设本身）。" % stripped)
    if not out.get("名字") or out["名字"] in ("未提及", "角色", "无", "未知"):
        out["名字"] = "未命名角色"
    if not re.fullmatch(r"[0-3]", out.get("尺度", "")):
        out["尺度"] = "2"
    if not out.get("示例对话"):
        qs = [q for q in QUOTE_MARK.findall(text) if 6 <= len(q) <= 80]
        if qs:
            out["示例对话"] = "\n".join("%s：%s" % (out["名字"], q) for q in qs[:3])
    return out


def analyze(text, narrator=None, model=None, ollama=OLLAMA, progress=None, use_model=True):
    """返回 {'draft': {字段}, 'stats': {...}, 'warnings': [...]}"""
    global OLLAMA
    OLLAMA = ollama
    say = progress or (lambda *a, **k: None)

    parsed = parse_log(text, narrator)
    role, user = parsed["role"], parsed["user"]
    say("parse", speakers=[d["name"] for d in parsed["order"]],
        role=role["name"] if role else None,
        user=user["name"] if user else None,
        lines=len(parsed["lines"]))

    warnings = []
    if not role:
        # 没有「名字：内容」这种标注（从网页整段复制过来的最常见），
        # 就把整段文本当材料丢给模型，让它自己判断谁是被扮演的角色。
        warnings.append("没认出「名字：内容」的格式，已按整段文本处理：让模型自己判断角色是谁。")
        if not use_model:
            draft = _heuristic_draft(parsed, "（未调用模型）")
            return {"draft": draft, "stats": _stats(parsed), "warnings": warnings}
        say("raw", chars=len(text))
        try:
            content, _meta = ollama_chat(
                [{"role": "system", "content": SYS_ANALYZER},
                 {"role": "user", "content": RAW_PROMPT.format(text=_sample_text(text))}],
                model=model, options={"num_predict": 1600, "temperature": 0.35})
            draft = parse_json_block(content)
            if draft:
                draft = _normalize_raw(draft, text, warnings)
            else:
                warnings.append("模型输出不是合法 JSON，已退回本地统计结果。")
                draft = _heuristic_draft(parsed, "（模型输出解析失败）")
        except Exception as e:
            warnings.append("调用本地模型失败：%s —— 已退回本地统计结果。" % e)
            draft = _heuristic_draft(parsed)
        return {"draft": draft, "stats": _stats(parsed), "warnings": warnings}
    if not user:
        warnings.append("只认出一个说话人，用户侧留空了。")
    if len(parsed["lines"]) < 4:
        warnings.append("对话很短（少于 4 条），提取质量有限。")

    if not use_model:
        draft = _heuristic_draft(parsed, "（未调用模型）")
        return {"draft": draft, "stats": _stats(parsed), "warnings": warnings}

    rname = role["name"]
    uname = user["name"] if user else "你"
    call = how_called(parsed["lines"], rname, uname)
    call_note = ("角色固定称呼对方为「%s」。" % call) if call else ""

    try:
        total_chars = sum(len(ln["text"]) for ln in parsed["lines"])
        if total_chars <= DIRECT_LIMIT:
            say("direct", chars=total_chars)
            digest = transcript(parsed["lines"], DIRECT_LIMIT)
        else:
            chunks = chunk_lines(parsed["lines"])
            if len(chunks) > MAX_CHUNKS:
                step = len(chunks) / float(MAX_CHUNKS)
                chunks = [chunks[int(i * step)] for i in range(MAX_CHUNKS)]
                warnings.append("对话太长，只抽取了 %d 段做摘要。" % MAX_CHUNKS)
            digests = []
            for i, ch in enumerate(chunks):
                say("chunk", i=i + 1, n=len(chunks))
                prompt = CHUNK_PROMPT.format(i=i + 1, n=len(chunks), total=total_chars,
                                            role=rname, user=uname, text="\n".join(ch))
                dg, _ = ollama_chat(
                    [{"role": "system", "content": "你是一个精准的文本摘要器，只输出摘要本身。"},
                     {"role": "user", "content": prompt}],
                    model=model, options={"num_predict": 400, "temperature": 0.3})
                if dg:
                    digests.append(dg)
            say("reduce", chunks=len(digests))
            digest = "\n\n".join("- " + d for d in digests)

        prompt = FINAL_PROMPT.format(
            role=rname, user=uname,
            role_count=role["count"], role_chars=role["chars"],
            user_count=user["count"] if user else 0,
            user_chars=user["chars"] if user else 0,
            call_note=call_note, digest=digest)

        say("synth")
        content, meta = ollama_chat(
            [{"role": "system", "content": SYS_ANALYZER},
             {"role": "user", "content": prompt}],
            model=model, options={"num_predict": 1600, "temperature": 0.35})

        draft = parse_json_block(content)
        if not draft:
            warnings.append("模型输出不是合法 JSON，已退回本地统计结果，建议手工补全。")
            draft = _heuristic_draft(parsed, "（模型输出解析失败）")
        else:
            draft = _normalize(draft, parsed, warnings)
    except Exception as e:
        warnings.append("调用本地模型失败：%s —— 已退回本地统计结果。" % e)
        draft = _heuristic_draft(parsed, "（模型不可用）")

    # 尺度按原文用词粗估（只往上抬到 3，其余保持 2）
    lv = estimate_level(parsed["lines"])
    draft["尺度"] = lv
    if lv == "3":
        warnings.append("原文露骨用词较多，尺度预设在 3 —— 想收着点就在编辑框里改成 2 或 1。")

    return {"draft": draft, "stats": _stats(parsed), "warnings": warnings}


def _stats(parsed):
    return {
        "lines": len(parsed["lines"]),
        "speakers": [{"name": d["name"], "count": d["count"], "chars": d["chars"]}
                     for d in parsed["order"][:6]],
        "role": parsed["role"]["name"] if parsed["role"] else None,
        "user": parsed["user"]["name"] if parsed["user"] else None,
    }
