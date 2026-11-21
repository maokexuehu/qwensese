#!/usr/bin/env bash
# qwensese 启动脚本（Linux / macOS）
#
# 用法：
#   ./scripts/start.sh
#   ./scripts/start.sh --lan     # 允许局域网访问（参数会原样传给 web/app.py）
#
# 首次使用可能要先加执行权限：chmod +x scripts/start.sh

set -u

# ---- 仓库根 = scripts/ 的上一级 ----------------------------------------
cd "$(dirname "$0")/.." || { printf '[错误] 无法进入脚本上级目录\n' >&2; exit 1; }
ROOT="$(pwd)"

note() { printf '[提示] %s\n' "$1" >&2; }
fail() { printf '[错误] %s\n' "$1" >&2; exit 1; }

# ---- 1. 找 Python 3.10+ ------------------------------------------------
PY=""
if command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    PY="$(command -v python)"
else
    fail "没找到 python3/python。装 Python 3.10+ 后重试（macOS：brew install python3）。"
fi

[ -f "web/app.py" ] || fail "找不到 web/app.py —— 本脚本应放在仓库的 scripts/ 目录里。"

# ---- 2. 首次运行：生成 web/config.json（等同于 start.ps1 的做法）------
if [ ! -f "web/config.json" ]; then
    if [ -f "web/config.example.json" ]; then
        cp "web/config.example.json" "web/config.json" || fail "复制 web/config.example.json 失败。"
        note "已从 web/config.example.json 生成 web/config.json"
    else
        note "web/config.json 与 web/config.example.json 都不存在，app.py 会使用内置默认值。"
    fi
fi

# ---- 2.5 模型文件检查（没有 GGUF 时提示先选型/下载）--------------------
GGUF_COUNT=$(find models/gguf -maxdepth 1 -name '*.gguf' 2>/dev/null | wc -l | tr -d ' ')
if [ "${GGUF_COUNT:-0}" = "0" ]; then
    note "models/gguf/ 里还没有 GGUF 模型。先按显卡选型并下载（任意平台通用）："
    note "  python3 scripts/model_advisor.py"
    note "  python3 scripts/model_advisor.py --download huihui-27b --quant UD-IQ4_XS"
    note "  python3 scripts/import_model.py --gguf models/gguf/<文件名>.gguf --name qwensese-27b"
else
    note "发现 $GGUF_COUNT 个 GGUF 模型文件（models/gguf/）。"
fi

# ---- 3. Ollama 可达性检查 ----------------------------------------------
OLLAMA_URL="http://127.0.0.1:11434"
if command -v curl >/dev/null 2>&1; then
    if ! curl -fsS --max-time 3 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
        note "Ollama 未运行或不可达（$OLLAMA_URL）。"
        note "先启动 Ollama，例如：OLLAMA_MODELS=\"$ROOT/models\" ollama serve"
        note "页面仍会打开，但对话需要 Ollama。"
    fi
else
    note "没找到 curl，跳过 Ollama 可达性检查。"
fi

# ---- 4. 端口提示（读不到就用缺省的 8717）--------------------------------
PORT="$("$PY" -c 'import json; print(int(json.load(open("web/config.json", encoding="utf-8")).get("port", 8717)))' 2>/dev/null || echo 8717)"
printf '\n[启动] 页面地址：http://127.0.0.1:%s/\n' "$PORT"
for arg in "$@"; do
    if [ "$arg" = "--lan" ] || [ "$arg" = "-lan" ]; then
        note "已开启 --lan：监听 0.0.0.0，同一局域网内可访问（app 起来后会打印具体 IP）。"
    fi
done
printf '[启动] 关闭本终端 = 停止页面（不影响已经在跑的 Ollama）\n\n'

# ---- 5. 前台运行 ------------------------------------------------------
exec "$PY" web/app.py "$@"
