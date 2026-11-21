# scripts/ 脚本说明

启动页面、按显卡选模型、下载模型、导入 Ollama。所有脚本都按**脚本自身位置**推导仓库根，
整个仓库可以挪到任意目录/盘符运行，不需要改脚本。

## 依赖

| 依赖 | 版本 | 用在哪 |
| --- | --- | --- |
| Python | 3.10+ | `web/app.py`、全部纯 Python 工具链、启动脚本 |
| Ollama | 最新版 | 跑模型。Windows 可把 `ollama.exe` 放进仓库的 `ollama/` 目录，或装到 PATH |
| PowerShell | 5.1+（Windows 自带） | `start.ps1` / `download-model.ps1` / `import-model.ps1` / `model-advisor.ps1` |
| curl | Windows 10 1803+ 自带 `curl.exe` | 仅 `download-model.ps1`（旧下载器） |
| bash + curl | Linux / macOS | `start.sh` |

Windows 侧脚本（`.ps1` / `.bat`）故意写成**纯 ASCII、提示信息用英文**：Windows PowerShell 5.1 读取
「无 BOM 的 UTF-8 脚本」时会按系统 ANSI 代码页解码，脚本里直接写中文会乱码甚至解析失败。
所有中文内容（人设、内核、文风、尺度表）都在运行时从仓库里的 UTF-8 `.md` 文件读取，不受影响。
`start.sh` 与本文档是原生 UTF-8，可以正常用中文。

## 脚本一览

| 文件 | 平台 | 作用 |
| --- | --- | --- |
| `start.bat` | Windows | 双击入口：切 UTF-8 代码页、转到仓库根、调用 `start.ps1` |
| `start.ps1` | Windows | 首次生成 `web\config.json`、读端口、必要时后台拉起 Ollama，前台运行 `web\app.py` |
| `start.sh` | Linux / macOS | 找 `python3`、生成配置、检查 Ollama、前台运行 `web/app.py` |
| `model_advisor.py` | 全平台 | **检测显卡/显存/内存，推荐 Qwen GGUF 模型；可 `--download` 转调下载器** |
| `download_model.py` | 全平台 | 从 HuggingFace 下载 GGUF：断点续传 + SHA256 校验 + `.sha256` 记录 |
| `import_model.py` | 全平台 | 复用 `web/app.py` 的 `build_system()` 生成 Modelfile，执行 `ollama create` |
| `model_catalog.json` | 全平台 | 候选模型目录（可直接编辑，不需要改代码） |
| `model-advisor.ps1` | Windows | `model_advisor.py` 的薄包装（纯 ASCII） |
| `download-model.ps1` | Windows | 旧版下载器（curl，断点续传，**不做** SHA256 校验） |
| `import-model.ps1` | Windows | 旧版导入器（功能与 `import_model.py` 对等） |

> 新流程推荐用 Python 工具链：跨平台一致、带 SHA256 校验。PowerShell 旧脚本保留兼容。

## 一次完整的流程

### 任意平台（推荐）

```bash
# 1) 检测显卡，拿到推荐（默认 16 GB 级显卡 -> 27B UD-IQ4_XS）
python scripts/model_advisor.py

# 2) 按推荐下载（断点续传 + SHA256 校验）
python scripts/model_advisor.py --download huihui-27b --quant UD-IQ4_XS

# 3) 导入 Ollama（需要 Ollama 在运行）
python scripts/import_model.py --gguf models/gguf/Huihui-Qwen3.8-27B-abliterated-UD-IQ4_XS.gguf \
    --name qwensese-27b --num-ctx 12288

# 4) 启动页面
# Windows:  powershell -ExecutionPolicy Bypass -File scripts\start.ps1
# Linux/macOS: ./scripts/start.sh
```

### Windows（旧脚本）

```powershell
# 1) 下载默认量化（UD-IQ4_XS，约 13.4 GB，断点续传，无校验）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\download-model.ps1

# 2) 导入成 Ollama 模型（默认名 qwensese-27b）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\import-model.ps1

# 3) 启动
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
```

## `model_advisor.py` 参数

| 参数 | 说明 |
| --- | --- |
| （无） | 检测硬件并打印完整推荐报告 |
| `--json` | 机器可读输出（网页 `/api/hardware` 用的同一份数据） |
| `--list` | 列出 `model_catalog.json` 里的全部候选模型与量化 |
| `--check` | 联网核对 HuggingFace 上文件是否存在，并打印实际大小/sha256 前缀 |
| `--download <id>` | 下载指定模型（可配 `--quant`、`--dest`、`--dry-run`） |
| `--no-uncensored` | 优先官方有审查模型（默认优先 abliterated） |

## `download_model.py` 参数

| 参数 | 说明 |
| --- | --- |
| `--repo owner/name` | HuggingFace 仓库（必填） |
| `--file xxx.gguf` | 仓库内文件名（必填） |
| `--dest` | 保存目录（默认 `models/gguf`） |
| `--sha256` | 手工指定期望校验值（覆盖 HF 元数据） |
| `--no-verify` | 跳过校验（不推荐） |
| `--dry-run` | 只打印 URL 与目标路径 |
| `--insecure` | 跳过 TLS 证书校验（仅当本机证书链有问题） |

## `import_model.py` 参数

| 参数 | 说明 |
| --- | --- |
| `--gguf` | GGUF 路径（省略则取 `models/gguf/` 里最新的） |
| `--name` | Ollama 模型名（省略则由文件名推导） |
| `--num-ctx` | 写进 Modelfile 的上下文长度（省略读 `web/config.json`） |
| `--temperature` | 采样温度（默认 0.8） |
| `--level` | 尺度 0–3（默认取角色卡里的尺度） |
| `--no-persona` | SYSTEM 只用 `prompts/system-core.md`，不附加示例角色卡 |
| `--template` | 聊天模板文件（默认 `config/template-nothink.txt`） |
| `--modelfile-out` | 把 Modelfile 写到指定路径（便于手改） |
| `--dry-run` | 只生成并打印 Modelfile，不执行 `ollama create` |

## 换量化 / 换模型 / 加模型

```bash
# 更省显存的量化
python scripts/model_advisor.py --download huihui-27b --quant UD-Q3_K_XL

# 自己的仓库与文件
python scripts/download_model.py --repo owner/repo --file model-Q4_K_M.gguf --dest models/gguf

# 加进目录（之后 --check / --download 都能用）
#   编辑 scripts/model_catalog.json，补 repo / file / size_gb / min_vram_gb，
#   然后：python scripts/model_advisor.py --check
```

## 说明

- 模型文件在 `models/` 下，已被 `.gitignore` 忽略，不会进仓库。
- 导入时 Ollama 必须在运行，并且其模型库目录要与启动脚本一致（`OLLAMA_MODELS` 指向 `<仓库根>/models`）；
  否则导入的模型可能落到别处，启动后网页里看不到。常见做法：另开终端先跑 `start.ps1`，再跑导入。
- Web 页面不依赖导入时写死的 SYSTEM（每轮现场拼装）；只有 `ollama run` 或第三方前端才需要重新导入。
- 更多选型/校验细节见 [../docs/model-selection.md](../docs/model-selection.md)。
