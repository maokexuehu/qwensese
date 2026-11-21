# scripts/ 脚本说明

启动页面、下载模型、导入模型。所有脚本都用**脚本自身位置**推导仓库根，整个仓库可以拷到任意目录 / 盘符运行，不需要改脚本。

## 依赖

| 依赖 | 版本 | 用在哪 |
| --- | --- | --- |
| Python | 3.10+ | `web/app.py` 和启动脚本 |
| Ollama | 最新版 | 跑模型。Windows 可把 `ollama.exe` 放进仓库的 `ollama\` 目录，或装到 PATH |
| PowerShell | 5.1+（Windows 自带） | `start.ps1` / `download-model.ps1` / `import-model.ps1` |
| curl | Windows 10 1803+ 自带 `curl.exe` | `download-model.ps1` |
| bash + curl | Linux / macOS | `start.sh` |

Windows 侧脚本（`.ps1` / `.bat`）故意写成**纯 ASCII、提示信息用英文**：Windows PowerShell 5.1 读取「无 BOM 的 UTF-8 脚本」时会按系统 ANSI 代码页解码，脚本里直接写中文会乱码甚至解析失败。所有中文内容（人设、内核、文风、尺度表）都在运行时从仓库里的 UTF-8 `.md` 文件读取，不受影响。`start.sh` 与本文档是原生 UTF-8，可以正常用中文。

## 脚本一览

| 文件 | 平台 | 作用 |
| --- | --- | --- |
| `start.bat` | Windows | 双击入口：切 UTF-8 代码页、转到仓库根、调用 `start.ps1` |
| `start.ps1` | Windows | 首次生成 `web\config.json`、读端口、必要时后台拉起 Ollama，前台运行 `web\app.py` |
| `start.sh` | Linux / macOS | 找 `python3`、检查 Ollama、前台运行 `web/app.py` |
| `download-model.ps1` | Windows | 从 HuggingFace 下载 GGUF 到 `models\gguf\`，支持断点续传 |
| `import-model.ps1` | Windows | 生成 Modelfile（SYSTEM = 人设 + 内核 + 文风），调 `ollama create` 打包模型 |

## 一次完整的流程

```powershell
# 1) 下载默认量化（UD-IQ4_XS，约 13.4 GB）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\download-model.ps1

# 2) 导入成 Ollama 模型（默认名 qwensese-27b）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\import-model.ps1

# 3) 启动页面（也可以直接双击 scripts\start.bat）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
# 想让同一局域网里的手机访问：
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1 -Lan
```

Linux / macOS：

```bash
chmod +x scripts/start.sh      # 首次
./scripts/start.sh             # 允许局域网：./scripts/start.sh --lan
```

`start.ps1` / `start.sh` 首次运行会把 `web/config.example.json` 复制成 `web/config.json`（不改 example），端口缺省 **8717**，只监听 `127.0.0.1`。

## 参数

### start.ps1

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `-Lan` | 关 | 绑定 `0.0.0.0`（映射成 `web/app.py --lan`），同一 Wi-Fi/局域网内的手机也能访问 |
| `-Check` | 关 | 只做环境自检（仓库根、config、端口、Python、Ollama、模型是否已导入），不启动任何服务/页面，只读退出 |

自检示例：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1 -Check`

### download-model.ps1

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `-Quant` | `UD-IQ4_XS` | 量化档位：`UD-IQ4_XS` / `UD-DW-Q4_K_M` / `UD-Q4_K_XL` / `UD-Q3_K_XL` / `UD-Q5_K_XL` |
| `-Repo` | `huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF` | HuggingFace 仓库（`组织/仓库` 形式） |
| `-Dest` | `<仓库根>\models\gguf` | 保存目录，可改成别的目录 |
| `-DryRun` | 关 | 只打印解析出的 URL / 目标路径，不下载 |

URL 规则：`https://huggingface.co/<Repo>/resolve/main/Huihui-Qwen3.8-27B-abliterated-<Quant>.gguf?download=true`。

### import-model.ps1

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `-Name` | `qwensese-27b` | Ollama 模型名（要和 `web\config.json` 的 `model` 一致） |
| `-Quant` | `UD-IQ4_XS` | 决定找哪个 gguf 文件 |
| `-GgufPath` | `<仓库根>\models\gguf\Huihui-Qwen3.8-27B-abliterated-<Quant>.gguf` | 手动指定 gguf 路径 |
| `-NoStyle` | 关 | 不把 `prompts\style-guide.md` 拼进 SYSTEM |
| `-Vision` | 关 | gguf 目录里有 `mmproj*.gguf` 时一并作为视觉投影导入；没有就按纯文本 |
| `-Temperature` | `0.8` | 写进 Modelfile 的 `PARAMETER temperature` |
| `-NumCtx` | `16384` | 写进 Modelfile 的 `PARAMETER num_ctx` |
| `-ModelfileOut` | 空（不落盘） | 把生成的 Modelfile 额外保存到指定路径，方便自己检查或手动改动 |
| `-DryRun` | 关 | 只生成并打印 Modelfile，不调用 `ollama create`（可配合 `-ModelfileOut` 留存） |

SYSTEM 的顺序和 `web/app.py` 的 `build_system` 对齐：`prompts/system-core.md` → `personas/persona.md` 的字段（按 `## 字段名` 解析，写作【字段】正文）→ 当前尺度提示（档位数字 + `system-core.md` 尺度表里对应那一行的描述）→ `prompts/style-guide.md`（`-NoStyle` 时跳过）。所以改了人设或提示词后重跑本脚本，模型里内置的 SYSTEM 才会更新。

## 产物目录

| 目录 | 内容 | 说明 |
| --- | --- | --- |
| `models\gguf\` | 下载的 `.gguf` | 单个文件很大，发布仓库时不要提交 |
| `models\` | Ollama 模型库（`OLLAMA_MODELS` 指向这里） | 也不要提交 |
| `logs\` | `ollama.out.log` / `ollama.err.log` / `ollama.web.log` | 排查启动问题先看这里 |
| `web\config.json` | 本地配置（首次由 example 生成） | 端口、模型名、`access_key` 都在这里，别把带密钥的版本提交 |

## 常见问题

- **提示「Ollama 未运行 / 127.0.0.1:11434 不可达」**：装好 Ollama 后重跑 `start.ps1`；手动启动时要带模型目录：`OLLAMA_MODELS=<仓库根>\models ollama serve`。
- **页面能开，但对话提示找不到模型**：先跑 `download-model.ps1`，再跑 `import-model.ps1`；确认 `web\config.json` 的 `model` 与 `-Name` 一致；确认 Ollama 是用 `OLLAMA_MODELS=<仓库根>\models` 启动的。
- **下载中断**：直接重跑 `download-model.ps1`，`curl -C -` 会从断点续传。
- **curl 报 `--ssl-no-revoke` 不支持**：说明 PATH 上的 `curl.exe` 不是 Windows 自带的那份；本脚本优先调用系统自带 curl，若仍报错可改用浏览器手动下载到 `models\gguf\`。
- **端口被占用**：改 `web\config.json` 的 `port`，或关掉占用端口的进程（页面可能已经在别处开着）。
- **PowerShell 提示「禁止运行脚本」**：用 `start.bat`（已带 `-ExecutionPolicy Bypass`），或自己给 `powershell` 命令加 `-ExecutionPolicy Bypass`。

## 安全

- 缺省只监听 `127.0.0.1`，只有本机能访问。
- `-Lan` / `--lan` 会绑定 `0.0.0.0`，同一局域网内的设备都能打开页面；需要门禁就在 `web\config.json` 里设置 `access_key`。不要把开了 `-Lan` 的页面直接暴露到公网。
