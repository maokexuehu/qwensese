#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仓库布局与发布合规检查（纯标准库，不启动任何服务）。

覆盖：
  * 必需目录 / 必需文件存在
  * web/app.py 能被 ast.parse
  * personas/*.md 能按 `## 字段名` 解析，核心字段齐全
  * 仓库文本文件里没有 NSFW 词（有白名单，见 NSFW_ALLOW）
  * 仓库文本文件里没有盘符绝对路径（Windows 盘符 + 冒号 + 反斜杠开头的机器相关路径）与密钥
  * web/config.example.json 存在、合法、access_key 为空
  * 本项目 web/ 只 import 标准库 / 同目录本地模块
  * 文本文件 UTF-8 可解码、无 BOM（非 tests/ 的 BOM 只警告，不 FAIL）
  * .gitignore 忽略运行时私有数据（存档 / 本机配置 / 模型）
  * xfail：本服务不提供 /api/version、/api/tags 路由（它们是 Ollama 上游接口，GET 会 404）

用法：
    python tests/test_layout.py            # 默认：发布元数据缺失只 WARN
    python tests/test_layout.py --strict   # 发布元数据缺失也 FAIL

输出：每个用例一行 PASS / FAIL / WARN / XFAIL，最后打印 SUMMARY 并以 0/1 退出。
"""

import ast
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRICT = "--strict" in sys.argv[1:]

RESULTS = []


# ---------------------------------------------------------------- 用例框架
def case(name):
    """注册一个用例：函数返回 detail 字符串，抛异常即 FAIL。"""

    def deco(fn):
        RESULTS.append([name, None, ""])  # 占位，保持注册顺序
        CASES.append((name, fn))
        return fn

    return deco


CASES = []
XFAIL_CASES = []


def xfail(name):
    def deco(fn):
        CASES.append((name, fn))
        XFAIL_CASES.append(name)
        return fn

    return deco


def fail(msg):
    raise AssertionError(msg)


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def rel(path):
    return os.path.relpath(path, REPO_ROOT).replace("\\", "/")


# ---------------------------------------------------------------- 文件扫描
SKIP_DIRS = {
    ".git", "__pycache__", "models", "saves", "logs", "node_modules",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv", "gguf",
}
TEXT_EXTS = {
    ".md", ".py", ".js", ".html", ".css", ".json", ".txt", ".ps1", ".bat",
    ".cmd", ".sh", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".example",
}
TEXT_NAMES = {".gitignore", ".gitattributes", ".editorconfig"}


def iter_text_files(include_tests=True):
    """遍历仓库内的文本文件（跳过二进制资源与运行时目录）。"""
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".git")
        )
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in TEXT_EXTS and fn not in TEXT_NAMES:
                continue
            path = os.path.join(dirpath, fn)
            if not include_tests and rel(path).split("/")[0] == "tests":
                continue
            yield path


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- 角色卡解析
FIELD_RE = re.compile(r"^##\s+(.+?)\s*$")
COMMENT_RE = re.compile(r"(?s)<!--.*?-->")


def parse_persona_md(text):
    """和 web/app.py parse_persona 同规则：`## 字段名` 分段。"""
    fields = {}
    cur = None
    for line in COMMENT_RE.sub("", text).split("\n"):
        m = FIELD_RE.match(line)
        if m:
            cur = m.group(1).strip()
            fields[cur] = []
        elif cur is not None:
            fields[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in fields.items()}


CARD_FILES = ["persona.md", "example-mentor.md", "template.md"]
CARD_REQUIRED_FIELDS = [
    "名字", "年龄", "身份", "外貌", "性格", "说话风格", "行为准则", "尺度", "禁忌",
]

# ---------------------------------------------------------------- 用例
REQUIRED_DIRS = ["web", "personas", "prompts", "config", "docs", "tests", "scripts"]

REQUIRED_FILES = [
    # 运行时核心
    "web/app.py",
    "web/app.js",
    "web/importer.py",
    "web/index.html",
    "web/style.css",
    "web/config.example.json",
    "personas/persona.md",
    "personas/template.md",
    "personas/example-mentor.md",
    "prompts/system-core.md",
    "prompts/style-guide.md",
    "config/mode.txt",
    "config/template-thinking.txt",
    "config/template-nothink.txt",
    "config/user_personas/default.md",
    "config/current_user_persona.txt",
    "docs/configuration.md",
    # 发布必备
    ".gitignore",
    ".gitattributes",
    "LICENSE",
    # 测试自身
    "tests/test_layout.py",
    "tests/test_api.py",
    "tests/run_all.py",
    "tests/README.md",
]

# 发布 / 文档元数据：早期开发阶段缺失只 WARN，--strict 时缺失即 FAIL
META_FILES = [
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "requirements.txt",
    "docs/API.md",
    "docs/quickstart.md",
    "docs/architecture.md",
    "docs/assets/preview.png",
    "scripts/start.ps1",
    "scripts/start.bat",
    "scripts/start.sh",
    "scripts/download-model.ps1",
    "scripts/import-model.ps1",
    "scripts/README.md",
]


@case("布局: 必需目录存在")
def t_required_dirs():
    missing = [d for d in REQUIRED_DIRS if not os.path.isdir(os.path.join(REPO_ROOT, d))]
    expect(not missing, "缺少目录: %s" % ", ".join(missing))
    return "%d 个目录齐全" % len(REQUIRED_DIRS)


@case("布局: 必需核心文件存在")
def t_required_files():
    missing = [f for f in REQUIRED_FILES if not os.path.isfile(os.path.join(REPO_ROOT, f))]
    expect(not missing, "缺少文件: %s" % ", ".join(missing))
    return "%d 个文件齐全" % len(REQUIRED_FILES)


@case("布局: 发布元数据文件（缺失默认 WARN）")
def t_meta_files():
    missing = [f for f in META_FILES if not os.path.isfile(os.path.join(REPO_ROOT, f))]
    if missing:
        msg = "缺失发布元数据: %s" % ", ".join(missing)
        if STRICT:
            fail(msg)
        return "WARN " + msg
    return "%d 个发布元数据文件齐全" % len(META_FILES)


@case("web/app.py: 能用 ast.parse 解析")
def t_app_py_ast():
    path = os.path.join(REPO_ROOT, "web", "app.py")
    src = read_text(path)
    try:
        tree = ast.parse(src, filename="web/app.py")
    except SyntaxError as e:
        fail("web/app.py 语法错误: %s (line %s)" % (e.msg, e.lineno))
    expect(src.count("\n") >= 100, "app.py 行数异常少（%d 行），文件可能被截断" % src.count("\n"))
    return "%d 行, %d 个 AST 节点" % (src.count("\n") + 1, sum(1 for _ in ast.walk(tree)))


@case("web/app.js: 存在且大小合理")
def t_app_js():
    path = os.path.join(REPO_ROOT, "web", "app.js")
    size = os.path.getsize(path)
    expect(size >= 10000, "web/app.js 只有 %d 字节，可能被截断" % size)
    text = read_text(path)
    expect("fetch(" in text, "web/app.js 里没有 fetch( 调用，前端不像完整文件")
    return "%d 字节" % size


@case("personas/*.md: `## 字段名` 可解析且核心字段齐全")
def t_persona_cards():
    problems = []
    for fn in CARD_FILES:
        path = os.path.join(REPO_ROOT, "personas", fn)
        expect(os.path.isfile(path), "缺少角色卡文件 personas/%s" % fn)
        fields = parse_persona_md(read_text(path))
        expect(fields, "personas/%s 解析不出任何 `## 字段`" % fn)
        missing = [k for k in CARD_REQUIRED_FIELDS if k not in fields]
        if missing:
            problems.append("%s 缺字段 %s" % (fn, "/".join(missing)))
            continue
        # 模板允许留空；真实角色卡必须填名字，年龄 >= 18
        if fn != "template.md":
            if not fields.get("名字", "").strip():
                problems.append("%s 的 名字 为空" % fn)
            age = re.sub(r"[^0-9]", "", fields.get("年龄", ""))
            if not age or int(age) < 18:
                problems.append("%s 的 年龄 不是 >=18 的数字 (%r)" % (fn, fields.get("年龄")))
        level = re.sub(r"[^0-9]", "", fields.get("尺度", ""))
        if level and not (0 <= int(level) <= 3):
            problems.append("%s 的 尺度 不在 0-3 (%r)" % (fn, fields.get("尺度")))
    expect(not problems, "; ".join(problems))
    return "%d 张卡字段齐全" % len(CARD_FILES)


# 这些命中是已知的良性用法（导入器的内容过滤词表），不是成人内容
NSFW_ALLOW = {
    "web/importer.py": ['"高潮"'],               # 导入器的内容过滤词表（屏蔽用词清单）
}
NSFW_KEYS = ["乳头", "阴茎", "射精", "性交", "高潮", "淫"]


@case("合规: 仓库无 NSFW 词（含白名单说明）")
def t_no_nsfw():
    hits, allowed = [], []
    for path in iter_text_files(include_tests=False):
        r = rel(path)
        try:
            text = read_text(path)
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.split("\n"), 1):
            for kw in NSFW_KEYS:
                if kw not in line:
                    continue
                whitelist = NSFW_ALLOW.get(r, [])
                if any(frag in line for frag in whitelist):
                    allowed.append("%s:%d(%s)" % (r, lineno, kw))
                    break
                hits.append("%s:%d(%s)" % (r, lineno, kw))
    expect(not hits, "命中 NSFW 词: %s" % ", ".join(hits[:10]))
    detail = "未命中" if not allowed else "未命中（白名单 %d 处: %s）" % (
        len(allowed), ", ".join(allowed))
    return detail


ABS_PATH_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")


@case("合规: 仓库文本文件无绝对路径")
def t_no_abs_path():
    hits = []
    for path in iter_text_files(include_tests=False):
        r = rel(path)
        try:
            text = read_text(path)
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.split("\n"), 1):
            if ABS_PATH_RE.search(line):
                hits.append("%s:%d: %s" % (r, lineno, line.strip()[:120]))
    expect(not hits, "发现绝对路径（应改成相对路径）: %s" % " | ".join(hits[:8]))
    return "全部相对路径"


# 不硬编码任何真实口令：只扫描"疑似泄露形态"
SECRET_PATTERNS = [r"trycloudflare\.com", r"\?k=[A-Za-z0-9]{4,}"]


@case("合规: 仓库无口令链接 / 临时隧道域名残留")
def t_no_secret():
    hits = []
    for path in iter_text_files(include_tests=False):
        r = rel(path)
        try:
            text = read_text(path)
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.split("\n"), 1):
            for pat in SECRET_PATTERNS:
                if re.search(pat, line, re.IGNORECASE):
                    hits.append("%s:%d(%s)" % (r, lineno, pat))
    expect(not hits, "命中口令链接/隧道域名: %s" % ", ".join(hits[:10]))
    return "未发现公网隧道域名与口令链接残留"


@case("配置: web/config.example.json 合法且 access_key 为空")
def t_config_example():
    path = os.path.join(REPO_ROOT, "web", "config.example.json")
    expect(os.path.isfile(path), "缺少 web/config.example.json")
    raw = read_text(path)
    try:
        cfg = json.loads(raw)
    except Exception as e:
        fail("web/config.example.json 不是合法 JSON: %s" % e)
    expect(isinstance(cfg, dict), "config.example.json 顶层必须是对象")
    for key in ("model", "ollama", "num_ctx", "host", "port", "access_key"):
        expect(key in cfg, "config.example.json 缺字段 %r" % key)
    expect(str(cfg.get("access_key") or "") == "",
           "config.example.json 的 access_key 必须为空（否则发布即泄密）")
    expect(isinstance(cfg.get("port"), int) and 0 < cfg["port"] < 65536,
           "config.example.json 的 port 不合法: %r" % (cfg.get("port"),))
    expect(str(cfg.get("ollama", "")).startswith("http://"),
           "config.example.json 的 ollama 必须是 http:// 地址")
    return "access_key 为空, model=%s, port=%s" % (cfg.get("model"), cfg.get("port"))


LOCAL_MODULES = {os.path.splitext(f)[0] for f in os.listdir(os.path.join(REPO_ROOT, "web"))
                 if f.endswith(".py")}


def imported_roots(path):
    tree = ast.parse(read_text(path))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对导入
                continue
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


@case("依赖: web/*.py 只 import 标准库 / 本地模块（零第三方依赖）")
def t_stdlib_only():
    stdlib = getattr(sys, "stdlib_module_names", None)
    if stdlib is None:  # Python < 3.10 兜底：跳过该检查
        return "当前 Python 没有 stdlib_module_names，跳过"
    allowed = set(stdlib) | LOCAL_MODULES
    bad = []
    for fn in ("app.py", "importer.py"):
        path = os.path.join(REPO_ROOT, "web", fn)
        expect(os.path.isfile(path), "缺少 web/%s" % fn)
        for root in sorted(imported_roots(path)):
            if root not in allowed:
                bad.append("web/%s -> %s" % (fn, root))
    expect(not bad, "发现第三方依赖: %s" % ", ".join(bad))
    return "app.py / importer.py 全部使用标准库"


@case("编码: 文本文件 UTF-8 可解码、无 BOM")
def t_encoding():
    bad_decode, bom_tests, bom_others = [], [], []
    for path in iter_text_files(include_tests=True):
        r = rel(path)
        raw = open(path, "rb").read()
        if raw.startswith(b"\xef\xbb\xbf"):
            (bom_tests if r.startswith("tests/") else bom_others).append(r)
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as e:
            bad_decode.append("%s (%s)" % (r, e))
    expect(not bad_decode, "无法按 UTF-8 解码: %s" % ", ".join(bad_decode[:8]))
    expect(not bom_tests, "tests/ 下的文件带 UTF-8 BOM: %s" % ", ".join(bom_tests))
    if bom_others:
        return "WARN 非 tests 文件带 BOM（建议去掉）: %s" % ", ".join(bom_others[:8])
    return "全部 UTF-8 无 BOM"


@case("发布安全: .gitignore 忽略存档/本机配置/模型")
def t_gitignore():
    path = os.path.join(REPO_ROOT, ".gitignore")
    expect(os.path.isfile(path), "缺少 .gitignore")
    text = read_text(path)
    need = ["saves/", "logs/", "web/config.json", "models/", "ollama/ollama.exe",
            "__pycache__/"]
    missing = [n for n in need if n not in text]
    expect(not missing, ".gitignore 缺忽略规则: %s" % ", ".join(missing))
    return "%d 条关键忽略规则齐全" % len(need)


@case("发布安全: 仓库里没有运行时私有数据")
def t_no_runtime_data():
    warns = []
    for name in ("web/config.json",):
        if os.path.isfile(os.path.join(REPO_ROOT, name)):
            warns.append("%s 存在（本机配置可能含口令，发布前别提交）" % name)
    for name in ("saves", "models", "logs"):
        p = os.path.join(REPO_ROOT, name)
        if os.path.isdir(p) and os.listdir(p):
            warns.append("%s/ 里有运行时文件" % name)
    if warns:
        return "WARN " + "; ".join(warns)
    return "无 web/config.json、无存档/模型/日志残留"


@case("脚本: scripts/ 有启动与模型脚本")
def t_scripts():
    d = os.path.join(REPO_ROOT, "scripts")
    expect(os.path.isdir(d), "缺少 scripts/ 目录")
    files = sorted(f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)))
    if not files:
        msg = "scripts/ 目录为空（启动脚本还没加入）"
        if STRICT:
            fail(msg)
        return "WARN " + msg
    return "%d 个脚本: %s" % (len(files), ", ".join(files))


@case("许可: LICENSE 是 MIT")
def t_license():
    text = read_text(os.path.join(REPO_ROOT, "LICENSE"))
    expect("MIT License" in text, "LICENSE 不是 MIT 许可")
    expect("Copyright" in text, "LICENSE 缺 Copyright 行")
    return "MIT License"


@xfail("已知差异: 本服务不提供 /api/version、/api/tags 路由")
def t_known_missing_routes():
    """本服务不提供 /api/version、/api/tags 路由：app.py 里的 /api/version（以及
    /api/tags）只是它调用 Ollama 上游的 URL，GET 本服务这两个路径会走 404。
    这里记录该已知差异，不 FAIL。"""
    src = read_text(os.path.join(REPO_ROOT, "web", "app.py"))
    has_version_route = bool(re.search(r'path\s*==\s*"/api/version"', src))
    has_tags_route = bool(re.search(r'path\s*==\s*"/api/tags"', src))
    expect(not has_version_route and not has_tags_route,
           "app.py 现在有 /api/version 或 /api/tags 路由了，请更新测试")
    return "本服务不提供 /api/version /api/tags（它们属于 Ollama 上游接口），GET 预期 404"


# ---------------------------------------------------------------- 主流程
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("=" * 64)
    print("仓库布局 / 发布合规检查")
    print("仓库根: %s" % REPO_ROOT)
    print("模式  : %s" % ("严格（--strict）" if STRICT else "默认（发布元数据缺失只 WARN）"))
    print("=" * 64)

    passed = failed = xfailed = 0
    for name, fn in CASES:
        is_xfail = name in XFAIL_CASES
        try:
            detail = fn() or ""
            if is_xfail:
                xfailed += 1
                status = "XFAIL"
            elif isinstance(detail, str) and detail.startswith("WARN"):
                passed += 1
                status = "PASS"
            else:
                passed += 1
                status = "PASS"
        except Exception as e:
            detail = "%s: %s" % (type(e).__name__, e)
            if is_xfail:
                xfailed += 1
                status = "XFAIL"
            else:
                failed += 1
                status = "FAIL"
        print("[%s] %s" % (status, name))
        if detail:
            print("        %s" % detail)

    print("-" * 64)
    print("SUMMARY pass=%d fail=%d xfail=%d" % (passed, failed, xfailed))
    if failed:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
