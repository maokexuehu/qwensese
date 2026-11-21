#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qwensese · 本地可视化角色对话台
- 只依赖 Python 标准库，不安装任何第三方包
- 角色卡与系统提示词直接读 personas/ 与 prompts/ 下的 .md 文件
- 每次请求现场拼 SYSTEM 发给 Ollama，所以换角色、调尺度都即时生效，不用重建模型
"""

import json
import os
import re
import subprocess
import sys
import socket
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WEB_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(WEB_DIR)
PERSONA_DIR = os.path.join(ROOT, "personas")
PROMPT_DIR = os.path.join(ROOT, "prompts")
OLLAMA_EXE = os.path.join(ROOT, "ollama", "ollama.exe")
CONFIG_PATH = os.path.join(WEB_DIR, "config.json")

DEFAULT_CONFIG = {
    "model": "qwensese-27b",
    "ollama": "http://127.0.0.1:11434",
    "num_ctx": 16384,
    "host": "127.0.0.1",
    "port": 8717,
}

LEVELS = {
    0: "纯剧情。身体接触止于牵手拥抱，不写情欲。",
    1: "暧昧张力：目光、距离、呼吸、布料摩擦。不写具体身体。",
    2: "情色场景：可写身体与感官，节奏放慢，重氛围与反应。",
    3: "直白露骨：不避讳器官与行为，可用粗口。",
}

# 叙事者模式：不扮演单一角色，而是第三人称讲故事（多人物同场、推进剧情）
NARRATOR_BLOCK = """======== 叙事模式（你不是某个角色，你是讲故事的人）=======
你现在是这场戏的叙述者，用第三人称全知视角讲故事，而不是扮演某一个人。
- 像小说一样写：场景、氛围、动作、神态、对话、心理都可以写；多个人物同场没有问题。
- 人物台词用「」包起来；叙述与环境描写正常写，不要写成一句问一句答的聊天记录。
- 每一轮至少推进一件事：新信息、新动作、场景变化、某个 NPC 出场。不要原地重复同一个画面。
- 不要替「你」做决定，也不要替「你」说话；只写世界和其他角色对「你」的反应。
- 结尾留一个钩子或悬念，让「你」有得接。
- 角色卡里的人物是这个世界的既有角色，可以按剧情需要出场、退场、旁观、插话。"""

# 各模型推荐参数：num_ctx 决定能记多久，越大越吃显存
OUTPUT_FORMAT_BLOCK = """【输出格式】直接给出正文和台词。不要写思考过程、推理步骤，也不要在开头写 think 之类的标记。"""

MODEL_INFO = {
    # 27B 的上下文有个硬拐点：实测 10k=44.5、12k=44.5、14k=16.5、16k=12 tok/s。
    # 12k 是"还能全速"的上限，再往上显存不够用就开始疯狂掉速。
    "qwensese-27b": {"label": "27B · 质量最高", "num_ctx": 12288,
                     "ctx_choices": [8192, 12288, 16384]},
    "qwensese-14b": {"label": "14B · 快 1.5 倍", "num_ctx": 32768,
                     "ctx_choices": [16384, 24576, 32768]},
}

FIELD_ORDER = ["名字", "年龄", "身份", "与用户的关系", "前情提要", "外貌", "性格",
               "说话风格", "行为准则", "尺度", "禁忌", "开场白", "示例对话"]

# 这些不是角色卡，不进列表
SKIP_FILES = {"README.md", "template.md"}  # personas/ 下不作为角色卡的文档文件


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            cfg.update(json.loads(open(CONFIG_PATH, encoding="utf-8").read()))
        except Exception:
            pass
    return cfg


CONFIG = load_config()


# ---------------------------------------------------------------- 文件解析

def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def clean_doc(text):
    """去掉给人看的说明（引用块）和 H1 标题，只留正文"""
    keep = []
    for line in text.split("\n"):
        if re.match(r"^\s*>\s*", line):
            continue
        if re.match(r"^#\s", line):
            continue
        keep.append(line)
    return "\n".join(keep).strip()


def parse_persona(path):
    raw = read_text(path)
    raw = re.sub(r"(?s)<!--.*?-->", "", raw)
    fields = {}
    cur = None
    for line in raw.split("\n"):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            cur = m.group(1).strip()
            fields[cur] = []
        elif cur is not None:
            fields[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in fields.items()}


def persona_summary(path):
    d = parse_persona(path)
    lvl_raw = re.sub(r"[^0-9]", "", d.get("尺度", "2") or "2")
    lvl = int(lvl_raw) if lvl_raw else 2
    return {
        "id": os.path.basename(path),
        "name": d.get("名字", "") or os.path.splitext(os.path.basename(path))[0],
        "age": d.get("年龄", ""),
        "role": d.get("身份", ""),
        "level": max(0, min(3, lvl)),
        "file": os.path.basename(path),
        "path": path,
    }


def list_personas():
    if not os.path.isdir(PERSONA_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(PERSONA_DIR)):
        if not fn.lower().endswith(".md") or fn in SKIP_FILES:
            continue
        try:
            out.append(persona_summary(os.path.join(PERSONA_DIR, fn)))
        except Exception:
            continue
    return out


def safe_persona_path(pfile):
    """只允许 personas/ 下的 .md，防目录穿越"""
    base = os.path.basename(pfile)
    if not base.lower().endswith(".md"):
        raise ValueError("必须是 .md")
    return os.path.join(PERSONA_DIR, base)


# ---------------------------------------------------------------- 入参校验
def atomic_write(path, text):
    """先写临时文件再替换：并发读/断电都不会看到写了一半的卡或存档"""
    import tempfile
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # Windows 上别的线程正读着这个文件时 replace 会报 WinError 5，重试几次
        for _ in range(10):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.03)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.remove(tmp)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def require_dict(value, name="字段"):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("%s 必须是 JSON 对象" % name)
    return value


def require_list(value, name="列表"):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("%s 必须是 JSON 数组" % name)
    return value


def fields_dict(value, name="fields"):
    """字段字典：值统一转成字符串（前端偶尔会传数字/数组）"""
    out = {}
    for k, v in require_dict(value, name).items():
        if v is None:
            out[str(k)] = ""
        elif isinstance(v, (list, tuple)):
            out[str(k)] = "、".join(str(x) for x in v)
        else:
            out[str(k)] = str(v)
    return out


# ---------------------------------------------------------------- 对话存档
SAVE_DIR = os.path.join(ROOT, "saves")


def _save_file(sid):
    """存档 id 只允许 [A-Za-z0-9_-]，防目录穿越"""
    sid = re.sub(r"[^A-Za-z0-9_-]", "", str(sid or ""))
    if not sid:
        raise ValueError("存档 id 不合法")
    return os.path.join(SAVE_DIR, sid + ".json")


def list_saves():
    if not os.path.isdir(SAVE_DIR):
        return []
    out = []
    for fn in os.listdir(SAVE_DIR):
        if not fn.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(SAVE_DIR, fn), encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        hist = d.get("history") or []
        last_line = ""
        for m in reversed(hist):
            c = (m.get("content") or "").strip()
            if c:
                last_line = re.sub(r"\s+", " ", c)
                break
        # 老存档没有 title 字段：退回第一句我说的话，再退回角色名
        title = (d.get("title") or "").strip()
        if not title:
            for m in hist:
                if (m.get("role") or "") == "user" and (m.get("content") or "").strip():
                    title = re.sub(r"\s+", " ", m["content"].strip())[:40]
                    break
        if not title:
            title = (d.get("persona_name") or "旧对话").strip()
        out.append({
            "id": d.get("id") or os.path.splitext(fn)[0],
            "name": d.get("name") or "未命名",
            "title": title,
            "auto": bool(d.get("auto")),
            "persona_id": d.get("persona_id") or "",
            "persona_name": d.get("persona_name") or "",
            "level": d.get("level"),
            "mode": d.get("mode") or "dialogue",
            "model": d.get("model") or "",
            "num_ctx": d.get("num_ctx"),
            "len": d.get("len"),
            "saved_at": d.get("saved_at") or "",
            "updated_at": d.get("updated_at") or d.get("saved_at") or "",
            "msg_count": len([m for m in hist if m.get("role") in ("user", "assistant")]),
            "chars": sum(len(m.get("content") or "") for m in hist),
            "last_line": last_line[:120],
        })
    out.sort(key=lambda x: x["updated_at"], reverse=True)
    return out


def load_save(sid):
    path = _save_file(sid)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_save(body):
    os.makedirs(SAVE_DIR, exist_ok=True)
    sid = body.get("id")
    if sid:
        # 统一收敛成安全 id：保证落盘路径、响应、存档列表三处一致
        sid = re.sub(r"[^A-Za-z0-9_-]", "", str(sid))
        if not sid:
            raise ValueError("存档 id 只允许字母、数字、下划线和短横线")
    if not sid:
        # 毫秒 + 随机后缀：并发保存时不会撞到同一个文件（原来只到秒，同一秒全撞）
        import random
        base = "s" + time.strftime("%Y%m%d%H%M%S")
        sid = "%s%03d%s" % (base, int(time.time() * 1000) % 1000,
                            "".join(random.choice("0123456789abcdef") for _ in range(4)))
        path0 = _save_file(sid)
        while os.path.exists(path0):
            sid += random.choice("0123456789abcdef")
            path0 = _save_file(sid)
    path = _save_file(sid)
    now = time.strftime("%Y-%m-%d %H:%M")
    hist = []
    for m in body.get("history") or []:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        hist.append({"role": role, "content": m.get("content") or ""})
    old = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            old = {}
    first_user = ""
    for m in hist:
        if m["role"] == "user" and (m.get("content") or "").strip():
            first_user = re.sub(r"\s+", " ", m["content"].strip())[:60]
            break
    data = {
        "id": sid,
        "name": (body.get("name") or old.get("name") or
                 "%s · %s" % (body.get("persona_name") or "对话", now)),
        "title": body.get("title") or old.get("title") or first_user,
        "auto": bool(body.get("auto", old.get("auto", False))),
        "persona_id": body.get("persona_id") or old.get("persona_id") or "",
        "persona_name": body.get("persona_name") or old.get("persona_name") or "",
        "level": body.get("level", old.get("level", 2)),
        "mode": body.get("mode") or old.get("mode") or "dialogue",
        "model": body.get("model") or old.get("model") or "",
        "num_ctx": body.get("num_ctx") or old.get("num_ctx"),
        "len": body.get("len", old.get("len")),
        "saved_at": old.get("saved_at") or now,
        "updated_at": now,
        "summary": body.get("summary", old.get("summary") or ""),
        "sum_count": int(body.get("sum_count", old.get("sum_count") or 0) or 0),
        "history": hist,
    }
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1))
    return {"ok": True, "id": sid, "saves": list_saves()}


def delete_save(sid):
    try:
        path = _save_file(sid)
    except ValueError:
        return
    # 并发场景：别的请求可能刚删掉它（FileNotFound），或者正读着它（Windows WinError 32）。
    # 先重试几次；还被占用就改名成 .deleted，list_saves 只认 .json 所以立刻"消失"。
    for _ in range(6):
        try:
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.05)
    try:
        os.replace(path, path + ".deleted")
    except OSError:
        pass


def write_persona(pfile, fields):
    path = safe_persona_path(pfile)
    parts = []
    for key in FIELD_ORDER:
        val = (fields.get(key) or "").strip()
        if key == "尺度":
            val = str(max(0, min(3, int(re.sub(r"[^0-9]", "", val) or 2))))
        parts.append("## %s\n%s\n" % (key, val))
    for key, val in fields.items():
        if key not in FIELD_ORDER and (val or "").strip():
            parts.append("## %s\n%s\n" % (key, val.strip()))
    os.makedirs(PERSONA_DIR, exist_ok=True)
    atomic_write(path, "\n".join(parts))
    return path


# ---------------------------------------------------------------- 我的角色卡
# 用户在 AI 眼里的形象。可以有很多张（不同身份 / 种族 / 场合），在页面上随时切换。
# 每张一个 md 文件，放在 config/user_personas/；当前用哪张记在 config/current_user_persona.txt。

ME_DIR = os.path.join(ROOT, "config", "user_personas")
ME_CUR = os.path.join(ROOT, "config", "current_user_persona.txt")
ME_LEGACY = os.path.join(ROOT, "config", "user_persona.md")
ME_DEFAULT = "default.md"
ME_FIELDS = ["名字", "称呼", "性别", "年龄", "种族", "身份", "外貌", "性格",
             "说话风格", "喜好", "其他"]


def _me_files():
    os.makedirs(ME_DIR, exist_ok=True)
    files = sorted(f for f in os.listdir(ME_DIR) if f.lower().endswith(".md"))
    if not files:
        # 老版本只有 config/user_persona.md —— 第一次运行时搬进目录当「默认」那张
        try:
            if os.path.exists(ME_LEGACY):
                with open(ME_LEGACY, "rb") as src:
                    data = src.read()
                with open(os.path.join(ME_DIR, ME_DEFAULT), "wb") as dst:
                    dst.write(data)
                files = [ME_DEFAULT]
            else:
                with open(os.path.join(ME_DIR, ME_DEFAULT), "w", encoding="utf-8", newline="\n") as f:
                    f.write("## 名字\n你\n\n## 称呼\n你\n")
                files = [ME_DEFAULT]
        except Exception:
            files = []
    return files


def me_current():
    files = _me_files()
    cur = ""
    try:
        if os.path.exists(ME_CUR):
            cur = read_text(ME_CUR).strip()
    except Exception:
        cur = ""
    if cur not in files:
        cur = files[0] if files else ME_DEFAULT
        try:
            with open(ME_CUR, "w", encoding="utf-8", newline="\n") as f:
                f.write(cur + "\n")
        except Exception:
            pass
    return cur


def me_select(name):
    name = os.path.basename(str(name or ""))
    if name not in _me_files():
        return False
    try:
        atomic_write(ME_CUR, name + "\n")
    except Exception:
        return False
    return True


def read_me(file=None):
    name = os.path.basename(str(file or "")) or me_current()
    path = os.path.join(ME_DIR, name)
    fields = {}
    try:
        if os.path.exists(path):
            fields = parse_persona(path)
    except Exception:
        fields = {}
    return {k: v for k, v in fields.items() if v}


def write_me(fields, file=None):
    name = os.path.basename(str(file or "")) or me_current()
    if not name.lower().endswith(".md"):
        name += ".md"
    os.makedirs(ME_DIR, exist_ok=True)
    path = os.path.join(ME_DIR, name)
    parts = []
    for key in ME_FIELDS:
        parts.append("## %s\n%s\n" % (key, (fields.get(key) or "").strip()))
    for key, val in fields.items():
        if key not in ME_FIELDS and (val or "").strip():
            parts.append("## %s\n%s\n" % (key, val.strip()))
    atomic_write(path, "\n".join(parts))
    return path


def list_me():
    cur = me_current()
    out = []
    for fn in _me_files():
        try:
            f = parse_persona(os.path.join(ME_DIR, fn))
        except Exception:
            f = {}
        nm = (f.get("名字") or "").strip() or os.path.splitext(fn)[0]
        sub = (f.get("身份") or f.get("种族") or f.get("称呼") or "").strip()
        out.append({"file": fn, "name": nm, "sub": sub, "current": fn == cur})
    return out


def delete_me(file):
    name = os.path.basename(str(file or ""))
    if not name or name == me_current() and len(_me_files()) <= 1:
        return False
    path = os.path.join(ME_DIR, name)
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except Exception:
        return False
    return False


# ---------------------------------------------------------------- 拼 SYSTEM

# 实测：Qwen3 系中文约 1.26 个字符 = 1 token。按 1.25 算（略保守，宁可早一点腾地方）
CHARS_PER_TOKEN = 1.25


def estimate_tokens(text):
    return int(len(text or "") / CHARS_PER_TOKEN) + 1


def build_system(card, level, use_style, extra, mode=None, me=None, summary=None, min_chars=0,
                 drop_opening=False):
    core_path = os.path.join(PROMPT_DIR, "system-core.md")
    style_path = os.path.join(PROMPT_DIR, "style-guide.md")

    sb = []
    if os.path.exists(core_path):
        sb.append(clean_doc(read_text(core_path)))

    sb.append("")
    sb.append("======== 角色卡 ========")
    sb.append("")
    for key in FIELD_ORDER:
        val = card.get(key)
        # 开场白已经在 history 里当第一句了，再塞进 system 等于把同一段话付两遍钱
        if key == "开场白" and drop_opening:
            continue
        if val:
            sb.append("【%s】" % key)
            sb.append(val)
            sb.append("")

    # 卡里手工加的额外字段（比如「世界观」「称呼」）也带进 system，不要丢
    for key, val in card.items():
        if key in FIELD_ORDER or not (val or "").strip():
            continue
        sb.append("【%s】" % key)
        sb.append(val.strip())
        sb.append("")

    # 用户（我）的设定 —— 决定「你」在故事里是谁
    if me:
        sb.append("")
        sb.append("======== 用户（我）的设定 ========")
        sb.append("这是玩家「你」这个人的设定：只描述这个人本身（长相、性格、说话习惯），"
                  "与任何具体剧情无关。不要把它里面的内容当成已经发生的事，也不要替「你」补剧情。")
        sb.append("凡是写到「你」，长相、性格、说话风格都必须符合下面这些，不要改写、不要另造一个人。")
        for key, val in me.items():
            if (val or "").strip():
                sb.append("【%s】%s" % (key, val.strip()))
        sb.append("")

    # 上下文压缩出来的前情摘要（比逐条历史省 token）
    if summary and summary.strip():
        sb.append("======== 前情摘要（更早的剧情，已压缩）========")
        sb.append(summary.strip())
        sb.append("上面这段是之前发生过的事，接着它往下演，不要重演、不要否认它。")
        sb.append("")

    lvl = max(0, min(3, int(level)))
    sb.append("【当前尺度档位】%d —— %s" % (lvl, LEVELS[lvl]))
    sb.append("")

    if extra and extra.strip():
        sb.append("======== 本轮额外指令 ========")
        sb.append(extra.strip())
        sb.append("")

    # 篇幅下限（超长档要求至少 600 汉字）
    if min_chars and int(min_chars) > 0:
        sb.append("======== 本轮篇幅 ========")
        sb.append("这一轮回复至少要写 %d 个汉字（正文合计），达不到就继续往下写，"
                  "不要用「……」或一句话跳过整场戏。" % int(min_chars))
        sb.append("")

    if mode == "narrator":
        sb.append(NARRATOR_BLOCK)
        sb.append("")

    # 有些 Qwen3 量化模型会先写一段思考再写正文，既浪费 token 又把正文埋掉。
    # 用一句指令把它压成空的，服务端还会兜底剥离（见 ThinkFilter）。
    sb.append(OUTPUT_FORMAT_BLOCK)
    sb.append("")

    # 卡里写了「结尾格式」（比如【雌堕值 +N → 当前 X】），就要求每轮最后一行照抄
    tail = (card.get("结尾格式") or "").strip()
    if tail:
        sb.append("======== 每轮结尾 ========")
        sb.append("每轮回复的最后一行，必须原样给出这一行格式（数值自己填）：")
        sb.append(tail)
        sb.append("")

    if use_style and os.path.exists(style_path):
        sb.append("======== 文风规则 ========")
        sb.append(clean_doc(read_text(style_path)))

    return "\n".join(sb).strip()


# ---------------------------------------------------------------- 帮我想（下一步建议）

SUGGEST_SYS = (
    "你在给一部互动小说当「提示助手」。用户扮演主角（下面提到「你」），AI 扮演角色或叙事者。\n"
    "任务：读下面的对话，写出 %d 条「用户紧接着可以说的下一句话 / 可以做的下一个动作」。\n"
    "最重要的规则：**不能脱离剧情**。\n"
    "- 必须承接最后一条回复：同一个地点、同一时刻、同一批在场的人，不许跳场景、不许跳时间\n"
    "- 不许引入对话与设定里都没有出现过的人物、地点、物品、组织、事件\n"
    "- 不许总结剧情、不许评论、不许写旁白解说；每条只是「你」的一个动作或一句台词\n"
    "- 不许替对方（AI）说话，也不许预判对方的反应\n"
    "- 每条都要能在下一轮立刻用上，不能是「以后再说」这种远期计划\n"
    "输出格式（严格遵守）：\n"
    "- 只输出 %d 行，每行以 1. 2. 3. 4. 开头，行与行之间换行\n"
    "- 不要写任何前言、总结、解释、括号旁白\n"
    "内容要求：\n"
    "1) 每行只有一句：要么是台词（用引号），要么是 *用星号包住的动作*\n"
    "2) 用「你」的视角，长度像用户平时说的话，别写成一大段\n"
    "3) %d 条指向不同方向：顺着对方、顶着来、试探情报、主动推进剧情、换个角度问\n"
    "4) 不要重复对话里已经说过的句子\n"
)


def _tidy_line(s):
    """清理模型常见的小毛病：多余的引号套嵌、尾巴上的半句"""
    s = (s or "").strip()
    s = s.strip("“”\"'「」 ").strip()
    # 有人会写成「他说：」「台词」——取更像台词的那半边
    if "」「" in s:
        a, b = s.split("」「", 1)
        a, b = a.strip().strip("：:"), b.strip()
        s = b if len(b) >= len(a) else a
    s = s.strip("“”\"'「」 ").strip()
    if s.endswith("：") or s.endswith(":"):
        s = s[:-1].strip()
    # 引号没闭合就补上，免得看起来像断掉的半句
    if s.count("「") > s.count("」"):
        s += "」"
    if s.count("“") > s.count("”"):
        s += "”"
    if s.count("*") % 2 == 1:
        s += "*"
    if s.count('\"') % 2 == 1:
        s += '\"'
    return s


def _looks_like_line(s):
    return ("「" in s or "」" in s or '"' in s or "“" in s or
            s.startswith("*") or s.startswith("我"))


def parse_suggestions(text, n):
    numbered, loose = [], []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        m = re.match(r"^\s*(\d+)\s*[.、)．:：]\s*(.+)$", s)
        body = (m.group(2) if m else s.strip("-—•·… ")).strip()
        # 模型偶尔把行号写两遍（"1. 1 *动作*" / "1 是啊…"，还可能是全角空格），反复剥掉
        exp = len(numbered) + 1
        for _ in range(3):
            m2 = re.match(r"^\s*(\d{1,2})\s*[.、)．:：]?\s+(.+)$", body)
            if not m2:
                break
            nxt = m2.group(2).strip()
            if int(m2.group(1)) == exp or (nxt[:1] in '*「"“"'):
                body = nxt
            else:
                break
        body = _tidy_line(body)[:600]
        if len(body) < 3:
            continue
        if m:
            numbered.append(body)
        else:
            loose.append(body)

    out = []
    for cand in numbered:
        if cand not in out:
            out.append(cand)
        if len(out) >= n:
            return out
    # 模型没按编号输出时的兜底：只收看起来像台词/动作的行
    for cand in loose:
        if _looks_like_line(cand) and cand not in out:
            out.append(cand)
        if len(out) >= n:
            break
    return out


def suggest_lines(body):
    pid = (body.get("persona_id") or "").strip()
    card = {}
    try:
        if pid:
            p = safe_persona_path(pid)
            if os.path.exists(p):
                card = parse_persona(p)
    except Exception:
        card = {}

    hist = body.get("history") or []
    hist = [m for m in hist if isinstance(m, dict) and (m.get("content") or "").strip()][-10:]
    n = max(3, min(5, int(body.get("n") or 4)))
    # 注意：这里的 model 是 Ollama 的 tag（qwensese-14b），不是 OpenAI 兼容端那套
    # 「qwensese/角色/尺度」的 id —— resolve_model() 返回的是 (角色文件, 尺度)，用错会直接 400。
    model = CONFIG["model"]
    wanted = (body.get("model") or "").strip()
    if wanted:
        try:
            ids = [m["id"] for m in model_list()]
        except Exception:
            ids = []

        def _norm(s):
            return (s or "").replace(":latest", "")

        if wanted in ids or _norm(wanted) in [_norm(i) for i in ids]:
            model = wanted
    num_ctx = int(body.get("num_ctx") or CONFIG.get("num_ctx") or 8192)

    ctx = []
    for m in hist:
        who = "用户" if m.get("role") == "user" else "AI"
        ctx.append("%s：%s" % (who, (m.get("content") or "").strip()[:1200]))
    convo = "\n\n".join(ctx) if ctx else "(对话刚开始，还没有内容)"

    sys_prompt = SUGGEST_SYS % (n, n, n)
    if card:
        sys_prompt += "\n===== 设定（用来保证不跑偏，不要照抄字句）=====\n"
        for k in ("名字", "身份", "与用户的关系", "性格", "说话风格"):
            v = (card.get(k) or "").strip()
            if v:
                sys_prompt += "【%s】%s\n" % (k, v[:200])
        for k in ("前情提要", "【世界观】", "【NPC 池】", "【场景库】", "【雌堕阶段表】"):
            v = (card.get(k) or "").strip()
            if v:
                sys_prompt += "【%s】%s\n" % (k, v[:600])

    summary = (body.get("summary") or "").strip()
    if summary:
        sys_prompt += "\n【更早的剧情摘要】%s\n" % summary[:1200]

    me = read_me()
    if me:
        bits = []
        for k in ("名字", "称呼", "性别", "年龄", "种族", "身份", "外貌", "性格", "说话风格", "喜好"):
            v = (me.get(k) or "").strip()
            if v:
                bits.append("%s：%s" % (k, v[:150]))
        if bits:
            sys_prompt += ("\n【「你」是谁】（建议必须用这个人的口吻、符合他的性格与处境）\n"
                           "%s\n" % "；".join(bits))

    user_prompt = "===== 最近的对话 =====\n%s\n\n===== 请给出 %d 条建议 =====" % (convo, n)

    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "messages": [{"role": "system", "content": sys_prompt},
                     {"role": "user", "content": user_prompt},
                     # 预填 "1." 既是防思考块的开关，也逼它从编号开始写
                     {"role": "assistant", "content": "1."}],
        "options": {"temperature": 0.95, "top_p": 0.95, "num_predict": 700,
                    "num_ctx": num_ctx, "repeat_penalty": 1.05},
    }

    try:
        ensure_ollama()
        req = urllib.request.Request(
            CONFIG["ollama"] + "/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"})
        resp = urllib.request.urlopen(req, timeout=240)
        d = json.loads(resp.read().decode("utf-8", "replace"))
        content = strip_think((d.get("message") or {}).get("content") or "")
        # 预填了 "1."，模型有时又自己写一遍编号 → 去掉重复的，避免出现 "1. 1. …"
        content = re.sub(r"^\s*1\s*[.、)．:：]\s*", "", content)
        text = "1." + content
    except Exception as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        return {"ok": False, "error": "%s %s" % (e, detail), "lines": []}

    out = {"ok": True, "lines": parse_suggestions(text, n), "model": model}
    if body.get("debug"):
        out["raw"] = text[:800]
    return out


# ---------------------------------------------------------------- 从对话导入「我的角色卡」

ME_IMPORT_SYS = (
    "你在从一段角色扮演对话里，提取「用户 / 代入者」这个人本身的设定。\n"
    "关键：只提取**这个人本身**（长什么样、什么性格、怎么说话、喜欢什么），"
    "**绝对不要**写剧情——不写发生过什么事、不写他被谁抓住了、不写和某人的关系进展。\n"
    "因为这些设定要复用到别的角色、别的故事里去。\n"
    "填写原则：\n"
    "1) 对话里明确写了的，直接抄要点（名字、年龄、种族、外貌、身份）。\n"
    "2) 没明说但能从「用户」的说话方式、用词、反应推断的，可以保守推断一句话："
    "说话风格、性格、喜好都能这样推（例：句子很短、爱用反问 → 说话风格「短句，爱反问」）。\n"
    "3) 实在没有线索的字段填「未提及」，不要硬编。\n"
    "4) 身份只写「是什么人」（例：书店常客 / 雪狐族王室出身），不要写处境（不写被俘、受伤、欠债）。\n"
    "5) 每个字段尽量都有内容，但不确定就不要写。\n"
    "只输出一个 JSON 对象，不要解释、不要代码块围栏。"
)

ME_IMPORT_JSON = """{
  "名字": "", "称呼": "", "性别": "", "年龄": "", "种族": "",
  "身份": "", "外貌": "", "性格": "", "说话风格": "", "喜好": "", "其他": ""
}"""


def _cn_number(text):
    """「十九岁」→「19」，只处理 0~99 的常见写法，其它原样返回"""
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    t = (text or "").strip()
    if not t or any(c.isdigit() for c in t):
        return t
    m = re.fullmatch(r"([一二三四五六七八九])?十([一二三四五六七八九])?", t)
    if m:
        tens = digits.get(m.group(1), 1)
        ones = digits.get(m.group(2), 0)
        return str(tens * 10 + ones)
    if len(t) == 1 and t in digits:
        return str(digits[t])
    return t


def ctx_report(persona_id, level=None, mode=None, use_style=True):
    """当前卡在 system 里占多少 token（前端算上下文进度用）"""
    card = {}
    try:
        p = safe_persona_path(persona_id or "")
        if os.path.exists(p):
            card = parse_persona(p)
    except Exception:
        pass
    lvl = int(level if level is not None else card.get("尺度", 2))
    sysmsg = build_system(card, lvl, bool(use_style), "", mode, me=read_me(),
                          drop_opening=True)
    return {"system_chars": len(sysmsg), "system_tokens": estimate_tokens(sysmsg),
            "chars_per_token": CHARS_PER_TOKEN}


def import_me(body):
    text = (body.get("text") or "").strip()
    if len(text) < 40:
        return {"ok": False, "error": "文本太短了，至少贴几十个字", "fields": {}, "status": 400}

    try:
        import importer
    except Exception as e:
        return {"ok": False, "error": "导入模块不可用：%s" % e, "fields": {}}

    user_prompt = ("=== 对话原文 ===\n%s\n\n=== 请按这个 JSON 结构输出「我」的个人设定 ===\n%s"
                   % (text[:9000], ME_IMPORT_JSON))

    try:
        content, _meta = importer.ollama_chat(
            [{"role": "system", "content": ME_IMPORT_SYS},
             {"role": "user", "content": user_prompt}],
            model=(body.get("model") or CONFIG["model"]),
            options={"num_predict": 900, "temperature": 0.3,
                     "num_ctx": int(body.get("num_ctx") or CONFIG["num_ctx"])})
        content = strip_think(content)
        draft = importer.parse_json_block(content)
    except Exception as e:
        return {"ok": False, "error": "分析失败：%s" % e, "fields": {}}

    if not draft:
        return {"ok": False, "error": "模型输出不是合法 JSON，再试一次", "fields": {}}

    # 「我」「用户」这种不是名字，别写进卡里
    BAD_NAMES = {"我", "用户", "主角", "玩家", "you", "you", "你"}

    fields = {}
    for key in ME_FIELDS:
        v = draft.get(key)
        if isinstance(v, (list, tuple)):
            v = "、".join(str(x) for x in v)
        v = ("" if v is None else str(v)).strip()
        if v in ("未提及", "无", "未知", "未说明", "不明确"):
            continue
        if key == "名字" and (v in BAD_NAMES or v.startswith("我")):
            continue
        if key == "年龄":
            v2 = _cn_number(re.sub(r"[^0-9一二三四五六七八九十两零]", "", v))
            v = v2 or v
        if v:
            fields[key] = v
    for k, v in draft.items():
        if k in ME_FIELDS or not isinstance(v, str) or not v.strip():
            continue
        if v.strip() not in ("未提及", "无", "未知", "未说明"):
            fields[k] = v.strip()

    if not fields:
        return {"ok": True, "fields": {}, "note": "这段对话里看不出太多「我」的个人设定，手动补几个字段吧"}
    return {"ok": True, "fields": fields, "chars": len(fields)}


# ---------------------------------------------------------------- 从描述生成角色卡
# 用户丢一段口语描述 / 关键词 / 人物小传，模型把它整理成一张卡（只写人设，不写剧情）。

DESC_SYS_PERSONA = (
    "你在帮人把「口语描述 / 关键词 / 人物小传」整理成一张角色扮演用的角色卡。只输出 JSON。\n"
    "必须输出的键（一个都不能少，没有内容就留空字符串）：\n"
    "名字、年龄、身份、与用户的关系、前情提要、外貌、性格、说话风格、行为准则、尺度、禁忌、开场白、示例对话\n"
    "铁律：\n"
    "1) 只写这个角色本身的静态设定，不写未来剧情、不写后续走向、不写伏笔\n"
    "2) 不列 NPC 名单、不写配角表、不写势力成员表，也不要用【】小标题\n"
    "3) 不写阶段表、场景库、世界观势力清单\n"
    "4) 用户没提的，可以按人物逻辑合理补全外貌/语气/习惯；但『前情提要』只写用户明确说过的已发生的事，没说就留空\n"
    "5) 『与用户的关系』只写这个角色怎么看用户（称呼、态度、亲疏），不要写两人的具体经历\n"
    "6) 『尺度』是 0~3 的整数：0 剧情 / 1 暧昧 / 2 情色 / 3 露骨（按描述里的露骨程度判断，没提就用 2）\n"
    "7) 『示例对话』4~6 行，每行形如『角色：台词』\n"
    "只输出一个 JSON 对象，不要解释、不要 markdown 代码块。"
)

DESC_SYS_ME = (
    "你在帮人把「口语描述 / 关键词」整理成一张『我』的自我角色卡。只输出 JSON。\n"
    "必须输出的键（一个都不能少，没有内容就留空字符串）：\n"
    "名字、称呼、性别、年龄、种族、身份、外貌、性格、说话风格、喜好、其他\n"
    "铁律：\n"
    "1) 只写「我」这个人本身：长相、性格、说话方式、喜好，不写任何剧情经历\n"
    "2) 不写未来会发生什么、不写别人怎么对我、不列 NPC\n"
    "3) 用户没提的可以按人物逻辑合理补全，但别编造经历\n"
    "只输出一个 JSON 对象，不要解释、不要 markdown 代码块。"
)


def card_from_desc(body):
    """描述 → 字段字典（target = persona | me）"""
    text = (body.get("text") or "").strip()
    target = "me" if (body.get("target") or "").lower() == "me" else "persona"
    if len(text) < 6:
        return {"ok": False, "error": "描述太短了，至少写几个关键词或一两句话", "fields": {}}
    try:
        import importer
    except Exception as e:
        return {"ok": False, "error": "导入模块不可用：%s" % e, "fields": {}}

    keys = ME_FIELDS if target == "me" else importer.FIELDS
    sysmsg = DESC_SYS_ME if target == "me" else DESC_SYS_PERSONA
    prompt = ("=== 用户的描述 ===\n%s\n\n=== 请整理成角色卡 JSON（键：%s）==="
              % (text[:8000], "、".join(keys)))
    model = CONFIG["model"]
    wanted = (body.get("model") or "").strip()
    if wanted:
        try:
            ids = [m["id"] for m in model_list()]
        except Exception:
            ids = []
        if wanted in ids or wanted.replace(":latest", "") in [i.replace(":latest", "") for i in ids]:
            model = wanted
    try:
        content, _meta = importer.ollama_chat(
            [{"role": "system", "content": sysmsg},
             {"role": "user", "content": prompt}],
            model=model,
            options={"num_predict": 1600, "temperature": 0.55, "top_p": 0.92,
                     "num_ctx": int(body.get("num_ctx") or CONFIG.get("num_ctx") or 12288)})
        content = strip_think(content)
        draft = importer.parse_json_block(content)
    except Exception as e:
        return {"ok": False, "error": "生成失败：%s" % e, "fields": {}}
    if not draft:
        return {"ok": False, "error": "模型没给出合法 JSON，再点一次试试", "fields": {}}

    fields = {}
    for k in keys:
        v = draft.get(k)
        if isinstance(v, (list, tuple)):
            v = "、".join(str(x) for x in v)
        v = ("" if v is None else str(v)).strip()
        if v in ("未提及", "无", "未知", "未说明", "不明确"):
            v = ""
        if k == "尺度":
            m = re.search(r"[0-3]", v)
            v = m.group(0) if m else str(body.get("level") if body.get("level") is not None else 2)
        if k in ("外貌", "性格", "说话风格", "行为准则", "禁忌", "前情提要", "示例对话", "开场白") and v:
            v, _dropped = importer.strip_plot_and_npc(v)
        fields[k] = v
    if not fields.get("名字"):
        fields["名字"] = "未命名角色" if target != "me" else "我"
    for k, v in draft.items():
        if k in keys or not isinstance(v, str) or not v.strip():
            continue
        fields[k] = v.strip()
    return {"ok": True, "target": target, "fields": fields, "chars": sum(len(v) for v in fields.values())}


# ---------------------------------------------------------------- 上下文压缩

COMPRESS_SYS = (
    "你在做角色扮演对话的「记忆压缩」。把下面这段对话压成一段前情摘要，供模型之后接着演。\n"
    "必须保留：\n"
    "1) 出现过的人名、身份、称呼，以及谁跟谁是什么关系\n"
    "2) 地点与时间推进（现在人在哪、什么时候）\n"
    "3) 已发生的关键事件，按时间顺序\n"
    "4) 当前的处境与代价（受伤、被绑、欠债、把柄、约定、誓言）\n"
    "5) 未解决的悬念、还没兑现的承诺、正在进行中的事\n"
    "6) 卡里有数值系统（比如雌堕值）就记下当前数值\n"
    "写法：第三人称、300～500 字、纯叙述，不要分析、不要建议、不要用「用户」这种称呼，"
    "不要照抄原文句子，直接输出摘要正文。"
)


def compress_context(body):
    hist = body.get("history") or []
    hist = [m for m in hist if isinstance(m, dict) and (m.get("content") or "").strip()]
    if not hist:
        return {"ok": False, "error": "这段对话还是空的", "summary": ""}

    lines = []
    for m in hist[-60:]:
        who = "我" if m.get("role") == "user" else "对方"
        lines.append("%s：%s" % (who, (m.get("content") or "").strip()[:1200]))
    convo = "\n\n".join(lines)

    model = CONFIG["model"]
    wanted = (body.get("model") or "").strip()
    if wanted:
        try:
            ids = [m["id"] for m in model_list()]
        except Exception:
            ids = []
        if wanted in ids or wanted.replace(":latest", "") in [i.replace(":latest", "") for i in ids]:
            model = wanted

    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "messages": [
            {"role": "system", "content": COMPRESS_SYS},
            {"role": "user", "content": "===== 对话 =====\n%s\n\n===== 请输出摘要 =====" % convo},
        ],
        "options": {"temperature": 0.4, "top_p": 0.9, "num_predict": 900,
                    "num_ctx": int(body.get("num_ctx") or CONFIG.get("num_ctx") or 8192)},
    }
    try:
        ensure_ollama()
        req = urllib.request.Request(
            CONFIG["ollama"] + "/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"})
        d = json.loads(urllib.request.urlopen(req, timeout=300).read().decode("utf-8", "replace"))
        text = strip_think((d.get("message") or {}).get("content") or "").strip()
    except Exception as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        return {"ok": False, "error": "%s %s" % (e, detail), "summary": ""}

    if not text:
        return {"ok": False, "error": "模型没给出摘要", "summary": ""}
    return {"ok": True, "summary": text, "chars": len(text), "model": model}


# ---------------------------------------------------------------- Ollama

def ollama_up():
    try:
        urllib.request.urlopen(CONFIG["ollama"] + "/api/version", timeout=2).read()
        return True
    except Exception:
        return False


_serve_lock = threading.Lock()

ZW = "\u200b"

# 模型偶尔会在正文之前吐一些奇怪的东西：零宽空格、坏字节、日文长音符号，
# 或者干脆把思考标记写坏（只剩一个 </think>，前面还带个随机单词）。
# 这些一律算"开头垃圾"，剥掉再给前端。
HEAD_JUNK = re.compile(r"(?is)^[\s\u200b\ufffd\u30fc\u2014\u2013]+")

# 开头 60 字之内出现的思考标记：整段（含标记前面的垃圾）一起去掉
MARKER_BLOCK = re.compile(r"(?is)^.{0,60}?<\s*\u200b?\s*think[^>]*>[\s\S]*?<\s*\u200b?\s*/\s*think\s*>")
MARKER_CLOSE = re.compile(r"(?is)^.{0,60}?<\s*\u200b?\s*/\s*think\s*>")
MARKER_OPEN = re.compile(r"(?is)^.{0,60}?<\s*\u200b?\s*think[^>]*>")


def strip_markers(text):
    """把开头的思考标记（含它前面那点垃圾）切掉，最多切 4 段"""
    t = text or ""
    for _ in range(4):
        m = MARKER_BLOCK.match(t) or MARKER_CLOSE.match(t) or MARKER_OPEN.match(t)
        if not m:
            break
        t = t[m.end():]
    return t


def clean_head(text):
    """去掉开头的垃圾字符和思考标记"""
    t = strip_markers((text or "").replace(ZW, ""))
    # 模型有时先写一大段思考、再单独吐一个 </think>（没有开头标记）：
    # 标记之前那整段不是正文，一起切掉。
    m = re.match(r"(?is)^.{0,900}?<\s*\u200b?\s*/\s*think\s*>", t)
    if m:
        t = t[m.end():]
    for _ in range(4):
        t2 = HEAD_JUNK.sub("", t, count=1)
        if t2 == t:
            break
        t = t2
    return t.lstrip()


class ThinkFilter:
    """流式输出时把开头的思考块剥掉，只把正文流给前端。

    Qwen3 系的量化模型经常先吐一段 <\u200bthink>…</\u200bthink> 再写正文；
    即使 think=False、即使 system 里禁止，它也可能吐一个空思考块，
    甚至只吐一个孤零零的 </think>，或者先写一段思考再补一个闭合标记。这里统一处理。

    做法：开头先攒 24 个字。见到思考标记就切掉；如果是纯叙述、没有台词/斜体/引号
    这类正文特征，就多等一会儿（最多 900 字），等那个 </think> 现身再切。
    """

    HOLD = 24
    MAX_HOLD = 6000     # 思考块一直不闭合（被截断）时的兜底
    REASON_HOLD = 900   # 看着像在写思考时，最多再等这么多字
    CONTENT_MARK = re.compile(r"[*「」“”\"（）()【】：:\n]")

    def __init__(self):
        self.buf = ""
        self.holding = True
        self.emitted = False

    def _release(self, text):
        self.buf, self.holding = "", False
        out = clean_head(text)
        if not out:
            return ""
        if not self.emitted:
            out = out.lstrip("\n\r\t ")
            self.emitted = True
        return out

    def feed(self, piece):
        if not self.holding:
            return piece
        self.buf += piece
        t = self.buf.replace(ZW, "")

        # 完整的思考块：整段丢掉，接着放行后面的正文
        m = MARKER_BLOCK.match(t)
        if m:
            return self._release(t[m.end():])
        # 只有闭合标记（没有开头）：连同它前面的垃圾一起丢掉
        m = MARKER_CLOSE.match(t)
        if m:
            return self._release(t[m.end():])
        # 刚出现开头标记：里面都是在自我审查，继续攒着等闭合
        if MARKER_OPEN.match(t):
            if len(t) > self.MAX_HOLD:
                return self._release(re.sub(r"(?is)^.{0,60}?<\s*\u200b?\s*think[^>]*>", "", t))
            return ""
        if len(t) >= self.HOLD:
            if self.CONTENT_MARK.search(t) or len(t) >= self.REASON_HOLD:
                return self._release(t)
            return ""
        return ""

    def flush(self):
        t = self.buf.replace(ZW, "")
        self.buf, self.holding = "", False
        if not t:
            return ""
        # 被长度截断、思考块没闭合：至少把正文部分给出去，别整条空白
        return clean_head(t)


def strip_think(text):
    """非流式路径用：剥掉开头的思考块/残片，再清掉正文里夹带的标记"""
    t = clean_head(text)
    return MARK_RE.sub("", t)


# 正文中间偶尔会夹一个 </think>（模型先写正文又补了个闭合标记）：
# 标记本身必须删掉，但两边的正文要留着
MARK_RE = re.compile(r"(?is)</?\s*\u200b?\s*think\s*>")
PARTIAL_MARK = re.compile(r"(?is)<\s*\u200b?\s*/?\s*(?:t|th|thi|thin|think)?$")


class MarkerScrubber:
    """删掉正文里夹带的 think 标记；末尾可能是半个标记时先按住，等下一段"""

    def __init__(self):
        self.hold = ""
        self.dirty = False

    def feed(self, text):
        t = self.hold + (text or "")
        out = MARK_RE.sub("", t)
        if out != t:
            self.dirty = True
        m = PARTIAL_MARK.search(out)
        if m and len(m.group(0)) <= 9:
            self.hold = m.group(0)
            out = out[:m.start()]
        else:
            self.hold = ""
        if self.dirty:
            out = re.sub(r"\n{3,}", "\n\n", out)
        return out

    def flush(self):
        h, self.hold = self.hold, ""
        return MARK_RE.sub("", h)


def ensure_ollama():
    """没起服务就顺手拉起来（和 scripts/start.ps1 等价），省一步操作"""
    if ollama_up():
        return True
    with _serve_lock:
        if ollama_up():
            return True
        if not os.path.exists(OLLAMA_EXE):
            return False
        env = dict(os.environ)
        env["OLLAMA_MODELS"] = os.path.join(ROOT, "models")
        env["OLLAMA_HOST"] = "127.0.0.1:11434"
        env["OLLAMA_KEEP_ALIVE"] = "30m"
        env["OLLAMA_FLASH_ATTENTION"] = "1"
        env["OLLAMA_KV_CACHE_TYPE"] = "q8_0"
        env["OLLAMA_NUM_PARALLEL"] = "1"
        env["OLLAMA_MAX_LOADED_MODELS"] = "1"
        log = open(os.path.join(ROOT, "logs", "ollama.web.log"), "ab")
        creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        try:
            subprocess.Popen([OLLAMA_EXE, "serve"], env=env, stdout=log,
                             stderr=log, creationflags=creationflags)
        except Exception:
            return False
        for _ in range(90):
            time.sleep(1)
            if ollama_up():
                return True
    return False


def stream_chat(payload, handler, notes=None):
    """把 Ollama 的 NDJSON 流转发给浏览器（顺便把思考链剥干净）

    对付 Qwen3 系的思考链有两道保险：
    1) 预填一个「*」当助手回复的开头 —— 模型直接续写正文，根本没机会先写思考块
    2) 万一它还是写了，ThinkFilter 会把开头那段切掉
    """
    prefill = "*"
    msgs = list(payload.get("messages") or [])
    if msgs and msgs[-1].get("role") == "user":
        msgs = msgs + [{"role": "assistant", "content": prefill}]
        payload = dict(payload, messages=msgs)
    else:
        prefill = ""

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        CONFIG["ollama"] + "/api/chat", data=data,
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        resp = urllib.request.urlopen(req, timeout=900)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = (json.loads(e.read().decode("utf-8", "replace")) or {}).get("error", "")
        except Exception:
            pass
        if e.code == 404:
            raise RuntimeError(
                "Ollama 里找不到模型「%s」%s。先确认模型已导入（见 docs/quickstart.md）；"
                "如果模型目录用了目录联接（junction），Ollama 读不到，要把 OLLAMA_MODELS 指到真实路径。"
                % (payload.get("model", ""), ("：" + detail) if detail else ""))
        raise RuntimeError("Ollama 返回 %d%s" % (e.code, ("：" + detail) if detail else ""))

    handler.send_response(200)
    handler.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()

    # 先把「上下文被裁剪了」这类提示送出去，前端会显示在输入框上面
    for note in (notes or []):
        handler.wfile.write((json.dumps({"note": note}, ensure_ascii=False) + "\n").encode("utf-8"))
    if notes:
        handler.wfile.flush()

    try:
        flt = ThinkFilter()
        scrub = MarkerScrubber()
        pending = prefill   # 预填的那半个字符，等第一段正文出来再补上
        for raw_line in resp:
            if not raw_line.strip():
                continue
            try:
                obj = json.loads(raw_line.decode("utf-8", "replace"))
            except Exception:
                handler.wfile.write(raw_line)
                continue
            msg = obj.get("message") or {}
            # 思考链是模型自言自语，不往前端送
            msg.pop("thinking", None)
            piece = msg.get("content") or ""
            if piece:
                out = scrub.feed(flt.feed(piece))
                if out and pending:
                    out, pending = pending + out, ""
                msg["content"] = out
            if obj.get("done"):
                tail = scrub.flush() + flt.flush()
                if tail:
                    msg["content"] = (msg.get("content") or "") + tail
                if pending and (msg.get("content") or "").strip():
                    msg["content"] = pending + msg["content"]
                    pending = ""
            handler.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            handler.wfile.flush()
    except OSError:
        # 浏览器中途断开（停止生成 / 关页面 / 截图进程退出）：
        # Windows 上是 ConnectionAbortedError 10053，Linux 上是 BrokenPipe
        pass
    finally:
        try:
            resp.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 虚拟模型名
# 手机上用 Chatbox 时，"换角色/换尺度"最顺手的做法就是把它们做成模型名：
#   qwensese-27b              → 默认卡（persona.md）+ 卡里写的尺度
#   qwensese/林晚/2           → 林晚这张卡 + 尺度 2
#   qwensese/c0/2             → 第 0 张卡 + 尺度 2（纯 ASCII 兜底）

MODEL_RE = re.compile(r"^qwensese/(.+)/([0-3])$")


def model_list():
    """实际装了哪些模型（页面顶栏的模型下拉用）"""
    out = []
    try:
        raw = urllib.request.urlopen(CONFIG["ollama"] + "/api/tags", timeout=2).read()
        tags = json.loads(raw).get("models", [])
    except Exception:
        tags = []
    for t in tags:
        name = (t.get("name") or "").replace(":latest", "")
        info = MODEL_INFO.get(name) or MODEL_INFO.get(t.get("name") or "", None)
        # 不是我们预设的模型也列出来，参数按默认给
        base = info or {}
        out.append({
            "id": t.get("name") or name,
            "name": name,
            "label": base.get("label") or name,
            "num_ctx": base.get("num_ctx") or CONFIG["num_ctx"],
            "ctx_choices": base.get("ctx_choices") or [CONFIG["num_ctx"]],
            "gb": round((t.get("size") or 0) / (1024 ** 3), 1),
            "family": (t.get("details") or {}).get("family") or "",
            "params": (t.get("details") or {}).get("parameter_size") or "",
            "default": name == CONFIG["model"],
        })
    # 预设里有但还没装的，也提示出来（灰掉）
    up = ollama_up()
    installed = {(m["name"]) for m in out}
    for name, info in MODEL_INFO.items():
        if name not in installed:
            out.append({"id": name, "name": name,
                        "label": info["label"] + ("" if up else "（服务未启动）") + ("（未安装）" if up else ""),
                        "num_ctx": info["num_ctx"],
                        "ctx_choices": info.get("ctx_choices") or [info["num_ctx"]],
                        "gb": 0, "family": "", "params": "",
                        "default": False, "missing": True, "offline": (not up)})
    return out


def model_catalog():
    out = [{"id": "qwensese-27b", "object": "model", "created": 1700000000,
            "owned_by": "qwensese"}]
    for idx, p in enumerate(list_personas()):
        stem = os.path.splitext(p["file"])[0]
        for lv in range(4):
            for name in (stem, "c%d" % idx):
                out.append({"id": "qwensese/%s/%d" % (name, lv), "object": "model",
                            "created": 1700000000, "owned_by": "qwensese"})
    return out


def resolve_model(model_id):
    """把模型名解成 (角色卡文件, 尺度)。解不出来就用默认卡。"""
    m = MODEL_RE.match(model_id or "")
    if m:
        key, lv = m.group(1), int(m.group(2))
        personas = list_personas()
        for idx, p in enumerate(personas):
            stem = os.path.splitext(p["file"])[0]
            if key in (stem, "c%d" % idx, p["name"], p["file"]):
                return p["file"], lv
        for p in personas:  # 模糊匹配，防止手机端截断名字
            if key and (key in p["name"] or p["name"] in key):
                return p["file"], lv
    return "persona.md", None


def sse_chunk(model_id, content, finish=None):
    """OpenAI 流式格式的一小包"""
    obj = {
        "id": "chatcmpl-qw",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{"index": 0,
                     "delta": ({"content": content} if content else {}),
                     "finish_reason": finish}],
    }
    return b"data: " + json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n\n"


# ---------------------------------------------------------------- HTTP

# ---------------------------------------------------------------- 访问钥匙
# 副本（给朋友用的那套）在 web/config.json 里写 "access_key"；没写就不校验。
# 支持三种带法：网址 ?k=xxx（会给浏览器种 cookie）、cookie qk、请求头 X-Access-Key。

KEY_PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>需要访问口令</title>
<style>
body{margin:0;background:#14161a;color:#e8e8ea;font:15px/1.7 system-ui,"Microsoft YaHei",sans-serif;
display:flex;min-height:100vh;align-items:center;justify-content:center;padding:24px}
.box{background:#1c1f24;border:1px solid #2c3038;border-radius:14px;padding:26px 24px;max-width:420px;width:100%}
h1{font-size:17px;margin:0 0 10px}
p{color:#9aa0aa;font-size:13.5px;margin:0 0 16px}
input{width:100%;box-sizing:border-box;background:#15181d;border:1px solid #2c3038;border-radius:10px;
color:#e8e8ea;padding:10px 12px;font-size:14px;margin-bottom:10px}
button{width:100%;background:#4a6cff;border:0;color:#fff;border-radius:10px;padding:10px;font-size:14px;cursor:pointer}
</style>
<div class="box">
<h1>需要访问口令</h1>
<p>这套页面只给拿到口令的人用。在下面填口令，或者用带 <code>?k=</code> 的完整网址打开。</p>
<input id="k" placeholder="访问口令">
<button onclick="location.href='/?k='+encodeURIComponent(document.getElementById('k').value)">进入</button>
</div>
<script>document.getElementById('k').addEventListener('keydown',function(e){if(e.key==='Enter')location.href='/?k='+encodeURIComponent(e.target.value)})</script>
</html>"""

COOKIE_NAME = "qk"


def access_key():
    return str(CONFIG.get("access_key") or "").strip()


def check_access(handler):
    """返回 (是否放行, 是否该下发 cookie)。"""
    import hmac
    key = access_key()
    if not key:
        return True, False
    from_query = ""
    try:
        from_query = str((handler._query().get("k") or "")).strip()
    except Exception:
        from_query = ""
    sent = ""
    m = re.search(r"(?:^|;\s*)" + COOKIE_NAME + r"=([^;]*)", handler.headers.get("Cookie") or "")
    if m:
        sent = m.group(1).strip()
    if not sent:
        sent = (handler.headers.get("X-Access-Key") or "").strip()
    if not sent:
        sent = from_query
    ok = bool(sent) and hmac.compare_digest(sent, key)
    return ok, bool(ok and from_query)


def gate_headers(handler, fresh):
    if not fresh:
        return None
    return {"Set-Cookie": "%s=%s; Path=/; Max-Age=2592000; SameSite=Lax; HttpOnly"
                         % (COOKIE_NAME, access_key())}


class Handler(BaseHTTPRequestHandler):
    server_version = "qwensese-ui/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[ui] " + (fmt % args) + "\n")

    _extras = None

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for hk, hv in (getattr(self, "_extras", None) or {}).items():
            self.send_header(hk, hv)
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel):
        path = os.path.normpath(os.path.join(WEB_DIR, rel.lstrip("/")))
        if not path.startswith(WEB_DIR) or not os.path.isfile(path):
            self._send(404, {"error": "not found"})
            return
        ctype = "text/html; charset=utf-8"
        if path.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif path.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        path = self.path.split("?")[0]
        self._extras = None
        passed, fresh = check_access(self)
        self._extras = gate_headers(self, fresh)
        if not passed:
            if path in ("/", "/index.html"):
                self._send(403, KEY_PAGE, "text/html; charset=utf-8")
            else:
                self._send(403, {"error": "需要访问钥匙：在网址后面加 ?k=（管理员给的那串）"})
            return
        try:
            if path in ("/", "/index.html"):
                self._static("index.html")
            elif path in ("/app.js", "/style.css"):
                self._static(path[1:])
            elif path == "/api/state":
                self._send(200, {
                    "personas": list_personas(),
                    "model": CONFIG["model"],
                    "server_ok": ollama_up(),
                    "levels": LEVELS,
                    "root": ROOT,
                })
            elif path == "/api/persona":
                qid = self._query().get("id", "")
                path2 = safe_persona_path(qid)
                if not os.path.exists(path2):
                    self._send(404, {"error": "角色卡不存在"})
                    return
                self._send(200, {"file": os.path.basename(path2),
                                 "fields": parse_persona(path2)})
            elif path in ("/v1/models", "/models"):
                self._send(200, {"object": "list", "data": model_catalog()})
            elif path == "/api/models":
                self._send(200, {"models": model_list(), "ollama_ok": ollama_up()})
            elif path == "/api/ctx":
                q = self._query()
                self._send(200, ctx_report(q.get("persona_id"), q.get("level"), q.get("mode")));
            elif path == "/api/me":
                q = self._query()
                f = (q.get("file") or "").strip()
                self._send(200, {"fields": read_me(f or None), "file": me_current(),
                                 "list": list_me(), "path": os.path.join(ME_DIR, me_current())})
            elif path == "/api/me/list":
                self._send(200, {"list": list_me(), "file": me_current()})
            elif path == "/api/saves":
                qid = self._query().get("id", "")
                if qid:
                    d = load_save(qid)
                    if not d:
                        self._send(404, {"error": "存档不存在"})
                        return
                    self._send(200, d)
                else:
                    self._send(200, {"saves": list_saves()})
            elif path in ("/health", "/api/health"):
                self._send(200, {"ok": True, "ollama": ollama_up(),
                                 "personas": len(list_personas())})
            else:
                self._send(404, {"error": "not found"})
        except FileNotFoundError:
            try:
                self._send(404, {"error": "文件不存在"})
            except Exception:
                pass
        except ValueError as e:
            try:
                self._send(400, {"error": str(e) or "参数不对"})
            except Exception:
                pass
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_POST(self):
        path = self.path.split("?")[0]
        self._extras = None
        passed, fresh = check_access(self)
        self._extras = gate_headers(self, fresh)
        if not passed:
            self._send(403, {"error": "需要访问钥匙：在网址后面加 ?k=（管理员给的那串）"})
            return
        try:
            try:
                body = self._json_body()
            except ValueError as e:
                self._send(400, {"error": str(e) or "请求体不合法"})
                return
            if path == "/api/persona/save":
                fields = fields_dict(body.get("fields"), "fields")
                file = body.get("file") or ("角色-%s.md" % re.sub(r"[\\/:*?\"<>|]", "",
                                                                fields.get("名字") or "新角色"))
                try:
                    oldp = safe_persona_path(file)
                    if os.path.exists(oldp):
                        for k, v in parse_persona(oldp).items():
                            if k not in fields:
                                fields[k] = v
                except Exception:
                    pass
                saved = write_persona(file, fields)
                self._send(200, {"ok": True, "file": os.path.basename(saved),
                                 "personas": list_personas()})
            elif path == "/api/persona/delete":
                p = safe_persona_path(body.get("file", ""))
                if os.path.exists(p):
                    os.remove(p)
                self._send(200, {"ok": True, "personas": list_personas()})
            elif path == "/api/chat":
                require_list(body.get("messages"), "messages")
                self._chat(body)
            elif path == "/api/import":
                self._import(body)
            elif path == "/api/saves":
                hist = require_list(body.get("history"), "history")
                body["history"] = [m for m in hist if isinstance(m, dict)]
                self._send(200, write_save(body))
            elif path == "/api/saves/delete":
                delete_save(body.get("id", ""))
                self._send(200, {"ok": True, "saves": list_saves()})
            elif path == "/api/suggest":
                body["history"] = [m for m in require_list(body.get("history"), "history")
                                   if isinstance(m, dict)]
                self._send(200, suggest_lines(body))
            elif path == "/api/me":
                fields = fields_dict(body.get("fields"), "fields")
                file = (body.get("file") or "").strip() or None
                if file:
                    try:
                        for k, v in read_me(file).items():
                            if k not in fields:
                                fields[k] = v
                    except Exception:
                        pass
                saved = write_me(fields, file)
                name = os.path.basename(saved)
                if body.get("select", True):
                    me_select(name)
                self._send(200, {"ok": True, "path": saved, "file": name, "list": list_me()})
            elif path == "/api/me/delete":
                ok = delete_me(body.get("file") or "")
                self._send(200, {"ok": ok, "list": list_me(), "file": me_current()})
            elif path == "/api/compress":
                body["history"] = [m for m in require_list(body.get("history"), "history")
                                   if isinstance(m, dict)]
                self._send(200, compress_context(body))
            elif path == "/api/me/import":
                d = import_me(body)
                self._send(d.pop("status", 200), d)
            elif path == "/api/card/gen":
                d = card_from_desc(body)
                self._send(200, d)
            elif path == "/api/ollama/start":
                ok = ensure_ollama()
                self._send(200, {"ok": bool(ok), "ollama_ok": ollama_up(), "models": model_list()})
            elif path in ("/v1/chat/completions", "/chat/completions"):
                self._openai_chat(body)
            else:
                self._send(404, {"error": "not found"})
        except FileNotFoundError:
            try:
                self._send(404, {"error": "文件不存在"})
            except Exception:
                pass
        except ValueError as e:
            try:
                self._send(400, {"error": str(e) or "参数不对"})
            except Exception:
                pass
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self._send(500, {"error": str(e)})
            except Exception:
                pass

    def _query(self):
        from urllib.parse import urlparse, parse_qs
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            raise ValueError("请求体不是合法 JSON")
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def _chat(self, body):
        if not ensure_ollama():
            self._send(503, {"error": "Ollama 没起来，也没能自动拉起。先运行 scripts/start.ps1（Windows 也可双击 scripts/start.bat）"})
            return

        pfile = body.get("persona_id") or ""
        card = {}
        try:
            p = safe_persona_path(pfile)
            if os.path.exists(p):
                card = parse_persona(p)
        except Exception:
            pass

        level = body.get("level", card.get("尺度", 2))
        system = build_system(card, level, body.get("use_style", True),
                              body.get("extra_system", ""), body.get("mode"),
                              me=read_me(), summary=body.get("summary"),
                              min_chars=body.get("min_chars") or 0,
                              # 只有对话里已经出现过角色回复（=开场白已在 history 里演过）才去掉
                              drop_opening=any(m.get("role") == "assistant"
                                               for m in body.get("messages", [])))

        hist = []
        for m in body.get("messages", []):
            if m.get("role") in ("user", "assistant") and m.get("content"):
                hist.append({"role": m["role"], "content": m["content"]})

        _ctx = int(body.get("num_ctx") or CONFIG["num_ctx"])
        _np = int(body.get("num_predict") or 1024)

        # 预算必须是 system + 对话 + 留给输出的空间。超了的话 Ollama 会从最前面截断，
        # 也就是先吃掉 system（角色卡），模型会立刻"失忆、崩人设"。
        notes = []
        budget = _ctx - _np - max(64, int(_ctx * 0.03))   # 留 3% 余量：估算在不同文本上会偏几个点
        sys_tok = estimate_tokens(system)
        total = sys_tok + sum(estimate_tokens(m["content"]) for m in hist)
        dropped = 0
        while hist and total > budget and len(hist) > 4:
            total -= estimate_tokens(hist[0]["content"])
            hist.pop(0)
            dropped += 1
        if dropped:
            notes.append("这一段太长，已自动放掉最早的 %d 条对话（系统占 %d tokens，剩 %d 给对话+输出）。"
                         "想留住旧剧情就用 ⚙ →「把前面的对话压成摘要」" % (dropped, sys_tok, max(0, budget - sys_tok)))
        if sys_tok > budget:
            notes.append("角色卡本身就占了 %d tokens，模型只剩 %d tokens 能写正文。"
                         "建议换 32k 的 14B，或把卡里超长的字段删短一点" % (sys_tok, max(0, _ctx - sys_tok)))

        msgs = [{"role": "system", "content": system}] + hist

        payload = {
            "model": body.get("model") or CONFIG["model"],
            "messages": msgs,
            "stream": True,
            "think": False,          # 必须关：思考链就是自我审查发生的地方
            "keep_alive": "30m",
            "options": {
                "num_ctx": int(body.get("num_ctx") or CONFIG["num_ctx"]),
                "num_predict": int(body.get("num_predict") or 1024),
                "temperature": float(body.get("temperature", 0.8)),
                "top_p": float(body.get("top_p", 0.92)),
                "top_k": 40,
                "repeat_penalty": float(body.get("repeat_penalty", 1.05)),
            },
        }
        stream_chat(payload, self, notes=notes)

    # -------------------------------------------------------- 对话导入成角色卡
    def _import(self, body):
        import importer

        text = (body.get("text") or "").strip()
        if len(text) < 40:
            self._send(400, {"error": "文本太短了（至少几十个字）"})
            return

        if not ensure_ollama():
            self._send(503, {"error": "Ollama 没起来。先运行 scripts/start.ps1（Windows 也可双击 scripts/start.bat），或把「只用本地统计」勾上"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(obj):
            try:
                self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()
            except OSError:
                raise

        try:
            res = importer.analyze(
                text,
                narrator=body.get("narrator") or None,
                model=body.get("model") or CONFIG["model"],
                ollama=CONFIG["ollama"],
                progress=lambda stage, **kw: emit(dict(stage=stage, **kw)),
                use_model=bool(body.get("use_model", True)),
            )
            emit({"stage": "done", "draft": res["draft"], "stats": res["stats"],
                  "warnings": res["warnings"]})
        except OSError:
            pass
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                emit({"stage": "error", "error": str(e)})
            except OSError:
                pass

    # -------------------------------------------------------- OpenAI 兼容层
    # 手机上的 Chatbox 走这里：既能省掉"思考链"，也能用模型名切角色/切尺度。
    def _openai_chat(self, body):
        if not ensure_ollama():
            self._send(503, {"error": {"message": "Ollama 没起来"}})
            return

        model_id = body.get("model") or "qwensese-27b"
        pfile, forced_level = resolve_model(model_id)
        card = {}
        try:
            p = safe_persona_path(pfile)
            if os.path.exists(p):
                card = parse_persona(p)
        except Exception:
            pass

        level = forced_level if forced_level is not None else card.get("尺度", 2)

        # 客户端自带的 system（Chatbox 的默认提示词）不覆盖角色卡，降级成"额外指令"
        client_sys = []
        history = []
        for m in body.get("messages", []):
            role = m.get("role")
            content = m.get("content")
            if isinstance(content, list):  # 某些客户端会发分段数组
                content = "".join(seg.get("text", "") for seg in content if isinstance(seg, dict))
            if not content:
                continue
            if role == "system":
                client_sys.append(content)
            elif role in ("user", "assistant"):
                history.append({"role": role, "content": content})

        system = build_system(card, level, True, "\n".join(client_sys), me=read_me())

        payload = {
            "model": CONFIG["model"],
            "messages": [{"role": "system", "content": system}] + history,
            "stream": bool(body.get("stream")),
            "think": False,
            "keep_alive": "30m",
            "options": {
                "num_ctx": int(body.get("num_ctx") or CONFIG["num_ctx"]),
                "num_predict": int(body.get("max_tokens") or body.get("num_predict") or 1024),
                "temperature": float(body.get("temperature", 0.8)),
                "top_p": float(body.get("top_p", 0.92)),
                "top_k": 40,
                "repeat_penalty": float(body.get("repeat_penalty", 1.05)),
            },
        }

        if not payload["stream"]:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                CONFIG["ollama"] + "/api/chat", data=data,
                headers={"Content-Type": "application/json; charset=utf-8"})
            r = json.loads(urllib.request.urlopen(req, timeout=900).read())
            content = strip_think((r.get("message") or {}).get("content") or "")
            self._send(200, {
                "id": "chatcmpl-qw",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model_id,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": r.get("prompt_eval_count", 0),
                    "completion_tokens": r.get("eval_count", 0),
                    "total_tokens": r.get("prompt_eval_count", 0) + r.get("eval_count", 0),
                },
            })
            return

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            CONFIG["ollama"] + "/api/chat", data=data,
            headers={"Content-Type": "application/json; charset=utf-8"})
        resp = urllib.request.urlopen(req, timeout=900)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        # 不写 Connection: keep-alive —— 服务端是 HTTP/1.0，靠关闭连接告诉客户端"流结束了"，
        # 声明 keep-alive 反而会让客户端一直等 EOF。
        self.end_headers()

        try:
            buf = b""
            while True:
                chunk = resp.read(512)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        j = json.loads(line.decode("utf-8"))
                    except Exception:
                        continue
                    piece = (j.get("message") or {}).get("content") or ""
                    if piece:
                        self.wfile.write(sse_chunk(model_id, piece))
                        self.wfile.flush()
            self.wfile.write(sse_chunk(model_id, None, finish="stop"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except OSError:
            pass
        finally:
            try:
                resp.close()
            except Exception:
                pass


class UiServer(ThreadingHTTPServer):
    """Windows 上 SO_REUSEADDR 允许抢占已被监听的端口，
    关掉它才能真正检出"页面已经开着"，避免双击两次起两个服务。"""
    allow_reuse_address = False
    daemon_threads = True


def lan_ips():
    """猜本机的局域网地址（给手机填用）"""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(i for i in ips if not i.startswith("127.") and not i.startswith("169.254."))


def main():
    try:
        # 日志重定向到文件时也能立刻看到，并且固定 UTF-8（scripts/start.bat 里已 chcp 65001）
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    host = CONFIG["host"]
    port = int(CONFIG["port"])
    argv = sys.argv[1:]
    if "--lan" in argv or CONFIG.get("lan"):
        host = "0.0.0.0"
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
        if a == "--host" and i + 1 < len(argv):
            host = argv[i + 1]
    if host not in ("127.0.0.1", "localhost"):
        host = "0.0.0.0"

    url = "http://%s:%d/" % ("127.0.0.1", port)
    try:
        UiServer.request_queue_size = 128      # 默认 backlog=5，并发一多就会拒连接
        UiServer.daemon_threads = True
        srv = UiServer((host, port), Handler)
    except OSError as e:
        # 端口被占用：多半是页面已经开着了，直接开浏览器就好
        print("端口 %d 已被占用（%s）" % (port, e))
        print("如果页面已经开着，直接访问 %s ；否则关掉占用的进程再试。" % url)
        try:
            webbrowser.open(url)
        except Exception:
            pass
        return
    srv.daemon_threads = True
    print("=" * 62)
    print("  本地可视化对话台")
    print("  角色卡目录 : %s" % PERSONA_DIR)
    print("  模型       : %s" % CONFIG["model"])
    print("  本机地址   : %s" % url)
    if host == "0.0.0.0":
        print("  局域网开放 : 是（手机可用，仅同一 Wi-Fi/局域网内）")
        ips = lan_ips()
        for ip in ips:
            print("     http://%s:%d/   ← 手机浏览器或 Chatbox 填这个" % (ip, port))
        print("     Chatbox: 提供方选 OpenAI API 兼容，API Host = http://%s:%d/v1" %
              (ips[0] if ips else "<本机IP>", port))
        if not ips:
            print("     （没猜出局域网 IP，用 ipconfig 看 IPv4 地址）")
    else:
        print("  局域网开放 : 否（只有本机能访问）")
    print("  关闭本窗口 = 停止页面（不影响 Ollama）")
    print("=" * 62)
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
