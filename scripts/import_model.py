#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qwensese · 跨平台模型导入器：生成 Modelfile 并执行 ollama create

和其它脚本一样只依赖标准库。SYSTEM 块直接复用 web/app.py 的 build_system()，
所以网页上换角色/调尺度随时生效，导入后的模型自带同样的系统提示词与示例角色卡。

用法：
    python scripts/import_model.py --gguf models/gguf/Qwen3-8B-Q4_K_M.gguf --name qwensese-8b
    python scripts/import_model.py --gguf <path> --name qwensese-14b --num-ctx 16384
    python scripts/import_model.py --gguf <path> --name test --no-persona --dry-run
    python scripts/import_model.py --gguf <path> --name test --modelfile-out ./Modelfile
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB_DIR = os.path.join(ROOT, "web")
GGUF_DIR = os.path.join(ROOT, "models", "gguf")
DEFAULT_TEMPLATE = os.path.join(ROOT, "config", "template-nothink.txt")
DEFAULT_PERSONA = os.path.join(ROOT, "personas", "persona.md")


def _fix_stdout():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_app_module():
    """导入 web/app.py 里纯函数部分（不会启动服务器：main() 在 __main__ 保护下）。"""
    sys.path.insert(0, WEB_DIR)
    import app  # noqa: E402  (同目录的 web/app.py)
    return app


def find_ollama():
    candidates = [
        os.path.join(ROOT, "ollama", "ollama.exe"),
        os.path.join(ROOT, "ollama", "ollama"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return shutil.which("ollama") or shutil.which("ollama.exe")


def build_modelfile(app, gguf_path, name, num_ctx, temperature, level=None,
                    no_persona=False, template_path=DEFAULT_TEMPLATE, system_extra=""):
    if not os.path.isfile(gguf_path):
        raise ValueError("找不到 GGUF 文件: %s" % gguf_path)
    system = ""
    if no_persona:
        core = os.path.join(ROOT, "prompts", "system-core.md")
        if os.path.isfile(core):
            system = _read(core).strip()
        if system_extra:
            system = (system + "\n\n" + system_extra).strip()
    else:
        card = {}
        if os.path.isfile(DEFAULT_PERSONA):
            card = app.parse_persona(DEFAULT_PERSONA)
        if level is None:
            try:
                level = int(card.get("尺度") or 2)
            except ValueError:
                level = 2
        system = app.build_system(card, int(level), True, system_extra, None, me=None)
    if '"""' in system:
        raise ValueError('SYSTEM 块里出现三引号（"""), Modelfile 会解析失败；请先清理 prompts/ 或角色卡')

    parts = ['FROM "%s"' % os.path.abspath(gguf_path).replace("\\", "/"),
             "PARAMETER num_ctx %d" % int(num_ctx),
             "PARAMETER temperature %.2f" % float(temperature)]
    if template_path and os.path.isfile(template_path):
        tpl = _read(template_path).strip()
        if tpl:
            parts.append('TEMPLATE """\n%s\n"""' % tpl)
    parts.append('SYSTEM """\n%s\n"""' % system.strip())
    return "\n".join(parts) + "\n", system


def main(argv=None):
    _fix_stdout()
    ap = argparse.ArgumentParser(description="生成 Modelfile 并把 GGUF 导入 Ollama")
    ap.add_argument("--gguf", default="", help="GGUF 文件路径（默认自动找 models/gguf 里最新的）")
    ap.add_argument("--name", default="", help="Ollama 里的模型名（默认由文件名推导）")
    ap.add_argument("--num-ctx", type=int, default=0, help="上下文长度（默认读 web/config.json 的 num_ctx）")
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--level", type=int, default=None, help="尺度 0-3（默认取角色卡里的尺度）")
    ap.add_argument("--no-persona", action="store_true", help="SYSTEM 只用 prompts/system-core.md，不附加示例角色卡")
    ap.add_argument("--template", default=DEFAULT_TEMPLATE, help="聊天模板文件（默认 config/template-nothink.txt）")
    ap.add_argument("--system-extra", default="", help="额外追加到 SYSTEM 的文字")
    ap.add_argument("--modelfile-out", default="", help="把 Modelfile 写到这个路径（便于手改）")
    ap.add_argument("--dry-run", action="store_true", help="只生成并打印 Modelfile，不执行 ollama create")
    ap.add_argument("--ollama", default="", help="Ollama 服务地址（默认读配置或 http://127.0.0.1:11434）")
    args = ap.parse_args(argv)

    app = load_app_module()
    cfg = dict(getattr(app, "CONFIG", {}) or {})
    num_ctx = args.num_ctx or int(cfg.get("num_ctx") or 16384)
    ollama_url = (args.ollama or cfg.get("ollama") or "http://127.0.0.1:11434").rstrip("/")

    gguf = args.gguf
    if not gguf:
        files = sorted((os.path.join(GGUF_DIR, f) for f in os.listdir(GGUF_DIR)
                        if f.lower().endswith(".gguf")), key=os.path.getmtime) if os.path.isdir(GGUF_DIR) else []
        if not files:
            print("[error] models/gguf/ 里没有 .gguf；先下载模型（python scripts/model_advisor.py --download ...）",
                  file=sys.stderr)
            return 2
        gguf = files[-1]
        print("[info] 未指定 --gguf，使用最新的: %s" % gguf)

    name = args.name
    if not name:
        stem = os.path.basename(gguf).rsplit(".", 1)[0]
        stem = re.sub(r"-*(UD-)?(IQ|Q)[0-9].*$", "", stem)
        name = "qwensese-" + (re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").lower() or "model")

    try:
        text, system = build_modelfile(app, gguf, name, num_ctx, args.temperature, args.level,
                                       args.no_persona, args.template, args.system_extra)
    except ValueError as e:
        print("[error] %s" % e, file=sys.stderr)
        return 2

    print("[info] gguf   : %s" % os.path.abspath(gguf))
    print("[info] name   : %s" % name)
    print("[info] num_ctx: %d | temperature: %.2f" % (num_ctx, args.temperature))
    print("[info] system : %d 字符（%s）" % (len(system), "仅 system-core.md" if args.no_persona else "system-core.md + personas/persona.md"))

    if args.modelfile_out:
        with open(args.modelfile_out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print("[info] Modelfile: %s" % os.path.abspath(args.modelfile_out))

    if args.dry_run:
        print("\n----- Modelfile preview -----")
        print(text)
        print("----- （--dry-run：没有执行 ollama create）-----")
        return 0

    ollama = find_ollama()
    if not ollama:
        print("[error] 找不到 ollama 可执行文件（仓库 ollama/ 目录或 PATH）", file=sys.stderr)
        return 2
    env = dict(os.environ)
    if "OLLAMA_MODELS" not in env:
        env["OLLAMA_MODELS"] = os.path.join(ROOT, "models")
        print("[note] 已为本次 ollama create 设置 OLLAMA_MODELS=%s" % env["OLLAMA_MODELS"])
        print("[note] 如果 Ollama 服务已经启动，它不会继承这个变量；若导入后网页找不到模型，"
              "请用 scripts/start.ps1 / scripts/start.sh 的方式重启 Ollama。")

    fd, tmp = tempfile.mkstemp(prefix="qwensese-Modelfile-", suffix=".txt")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print("[info] running: %s create %s -f <temp Modelfile>" % (ollama, name))
        p = subprocess.run([ollama, "create", name, "-f", tmp], env=env)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if p.returncode != 0:
        print("[error] ollama create 失败（exit %d）。同名模型已存在时先 ollama rm %s 再试。" % (p.returncode, name),
              file=sys.stderr)
        return p.returncode

    try:
        with urllib.request.urlopen(ollama_url + "/api/tags", timeout=5) as r:
            tags = json.loads(r.read().decode("utf-8"))["models"]
        names = [t.get("name") for t in tags]
        print("[ok] Ollama 现有模型: %s" % ", ".join(names))
        if not any(n and n.split(":")[0] == name for n in names):
            print("[warn] /api/tags 里没看到 %s，可能落到了另一个模型目录；用 ollama list 确认。" % name)
    except Exception as e:
        print("[warn] 无法查询 /api/tags（%s）；请用 ollama list 确认。" % type(e).__name__)

    print("")
    print("下一步：把 web/config.json 里的 model 改成 %r，然后重启页面（或直接刷新）。" % name)
    print("示例：scripts/start.ps1 -Check     /     ./scripts/start.sh --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
