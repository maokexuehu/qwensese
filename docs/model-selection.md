# 按你的显卡选模型（显存 / 性能 → Qwen GGUF 量化）

显存决定你能把多少权重放进显卡。放不下就会卸载到内存，
生成速度会掉一个数量级。这个仓库自带的 27B 模型需要约 13.4 GB 权重 + 上下文开销，**建议 16 GB 级显存**；
小显存机器改用 8B / 14B 体验会好得多。

## 一键：检测硬件 + 给推荐

任意平台（只用 Python 标准库，不需要装包）：

```bash
python scripts/model_advisor.py            # 检测显卡/显存/内存/CPU，打印推荐
python scripts/model_advisor.py --json     # 机器可读（网页的「硬件选型」按钮用的就是它）
python scripts/model_advisor.py --list     # 列出目录里所有候选模型与量化
python scripts/model_advisor.py --check    # 联网核对 HuggingFace 上的文件是否存在（需要能访问 HF）
```

Windows 也可以用 PowerShell 包装脚本（纯 ASCII，参数一一对应）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/model-advisor.ps1
powershell -ExecutionPolicy Bypass -File scripts/model-advisor.ps1 -Json
powershell -ExecutionPolicy Bypass -File scripts/model-advisor.ps1 -Download qwen3-8b -Quant Q4_K_M
```

网页里模型下拉旁边的 **「🖥️ 硬件选型」** 按钮会调用 `GET /api/hardware`，直接把推荐、显存估算与
下载/导入命令显示出来（结果缓存 5 分钟）。

检测方式：优先 `nvidia-smi`；Windows 退化到注册表 `qwMemorySize`（WMI 的 `AdapterRAM` 在
4 GB 以上会被截断，不可靠）；Linux 读 `/sys/class/drm/*/mem_info_vram_total`；macOS 用
`system_profiler`。都检测不到就按 CPU 模式给建议。**显存数值是驱动上报值，可能略小于标称。**

## 推荐表（经验值，来自本仓库作者实测 + 量化体积估算）

| 显存 | 档位 | 建议模型 | 建议量化 | 文件大小 | 建议 num_ctx |
| --- | --- | --- | --- | --- | --- |
| 无独显 / 核显 | CPU | Qwen3 4B（官方） | Q4_K_M | 约 2.5 GB | 4096 |
| 4–6 GB | 入门 | Qwen3 4B / 8B 小量化 | Q4_K_M | 2.5–5 GB | 4096–6144 |
| 8 GB | 主流 | Qwen3 8B 或 8B abliterated | Q4_K_M | 约 5 GB | 6144 |
| 12 GB | 高性能入门 | Qwen3 14B 或 14B abliterated | Q4_K_M | 约 9 GB | 8192 |
| 16 GB | 高性能 | **27B abliterated（本仓库默认，实测）+ UD-IQ4_XS** | UD-IQ4_XS | 约 13.4 GB | 12288 |
| 24 GB | 旗舰 | 27B UD-Q4_K_XL 或 32B Q4_K_M | — | 16.5–20 GB | 16384 |
| 32 GB+ | 旗舰 | 32B Q5_K_M / 27B UD-Q5_K_XL | — | 19.5–24 GB | 24576 |

> - 27B 的上下文有实测拐点：10k/12k 约 44.5 tok/s，14k 掉到 16.5、16k 约 12 tok/s。
>   16 GB 卡建议 `num_ctx 12288`，不要贪 16k。
> - 速度估算由参数规模与显卡档位粗略换算，**不是 benchmark**；量化、上下文、驱动版本都会影响结果。
> - 标 `abliterated` 的是权重层面消融拒答方向的衍生模型（本仓库默认底座）。官方 Qwen3 对成人向内容
>   会拒答；本项目的人设/尺度玩法建议用 abliterated 版本，并自行承担上游许可与合规责任。

## 目录里有哪些候选

`scripts/model_catalog.json` 是纯数据文件，直接改它就能增删候选（不用改代码）：

| id | 底座 | 参数量 | 是否作者实测 | 适用 |
| --- | --- | --- | --- | --- |
| `huihui-27b` | huihui-ai 27B abliterated | 27B | **是** | 16 GB+，成人向长剧情（默认） |
| `huihui-14b` / `huihui-8b` / `huihui-32b` | huihui-ai abliterated | 8/14/32B | 否 | 需要先 `--check` 核对上游仓库名 |
| `qwen3-4b` / `qwen3-8b` / `qwen3-14b` / `qwen3-32b` | Qwen 官方 | 4–32B | 否 | 通用对话；成人向内容会拒答 |
| `qwen3-1.7b` | Qwen 官方 | 1.7B | 否 | 极低配置 / 只想试流程 |

只有 `huihui-27b` 是本仓库作者在本机实测过的组合；其它条目是**选型起点**，不是质量背书。
上游仓库可能改名/删档，下载前先跑 `--check`。

## 下载（断点续传 + SHA256 校验）

```bash
# 直接下载推荐组合（走 model_advisor 的推荐结果）
python scripts/model_advisor.py --download qwen3-8b --quant Q4_K_M

# 或者手动指定仓库与文件
python scripts/download_model.py --repo Qwen/Qwen3-8B-GGUF --file Qwen3-8B-Q4_K_M.gguf
python scripts/download_model.py --repo huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF \
    --file Huihui-Qwen3.8-27B-abliterated-UD-IQ4_XS.gguf
```

特性与注意点：

- **断点续传**：下载中断后重跑同一条命令，会从 `.part` 文件继续（HTTP Range）。
- **SHA256 校验**：脚本先读 HuggingFace 模型元数据里的 LFS sha256，下载完成后比对；不匹配会把文件
  改名成 `.bad` 并报错。离线/元数据不可用时打印警告，并把实际 sha256 记到 `<文件>.sha256` 供你复查。
  也可以手工指定：`--sha256 <64位hex>`；`--no-verify` 可跳过（不推荐）。
- 默认落到 `models/gguf/`（`.gitignore` 已忽略）；`--dest` 可改。
- `--dry-run` 只打印 URL 与目标路径，不联网。
- Windows 旧脚本 `scripts/download-model.ps1` 仍然可用（它是 curl 实现），但它**不做** sha256 校验；
  新代码建议用上面的 Python 下载器。

## 导入 Ollama

```bash
python scripts/import_model.py --gguf models/gguf/Qwen3-8B-Q4_K_M.gguf --name qwensese-8b
python scripts/import_model.py --gguf models/gguf/Huihui-Qwen3.8-27B-abliterated-UD-IQ4_XS.gguf \
    --name qwensese-27b --num-ctx 12288
```

- `SYSTEM` 块复用 `web/app.py` 的 `build_system()`：`prompts/system-core.md` + `prompts/style-guide.md`
  + `personas/persona.md`，所以导入出来的模型自带和网页一致的人设/尺度。`--no-persona` 只保留系统核心提示词。
- `TEMPLATE` 默认取 `config/template-nothink.txt`；`--dry-run` 只生成并打印 Modelfile。
- 导入前 Ollama 必须在运行；导入后把 `web/config.json` 的 `model` 改成新名字（例如 `qwensese-8b`）。

Windows 也可以用原有的 `scripts/import-model.ps1 -Name qwensese-8b -GgufPath models\gguf\...`，
两者行为一致。

## 我在 14B / 8B 上怎么跑（文档以前缺的那一段）

```bash
# 14B 示例（16 GB 卡可以上 Q5_K_M；12 GB 卡用 Q4_K_M）
python scripts/model_advisor.py --download huihui-14b --quant Q5_K_M
python scripts/import_model.py --gguf models/gguf/Qwen3-14B-abliterated-Q5_K_M.gguf \
    --name qwensese-14b --num-ctx 16384
```

然后把 `web/config.json` 的 `"model"` 改为 `"qwensese-14b"`，`"num_ctx"` 改成 16384 左右
（app.py 里预置了 `qwensese-14b` 的上下文档位）。同理 8B 用 `qwensese-8b`。

## 硬件账单（说清楚，不淡化）

- **下载量**：默认 27B `UD-IQ4_XS` 约 13.4 GB；14B Q5_K_M 约 10.8 GB；8B Q4_K_M 约 5 GB。
- **磁盘**：给 `models/gguf/` 留至少 20 GB（下载临时 `.part` + 导入后 Ollama 自己的 blobs 各占一份）。
- **内存**：即使显存够，也建议 ≥16 GB 系统内存；显存不足时会卸载部分层到内存，速度骤降。
- **网络**：首次必须联网下载；之后推理完全离线。
- **时间**：十几 GB 视带宽可能 10 分钟到数小时；断点续传可以分多次下。

## 常见问题

- **检测到显存 0 / 识别成核显**：先确认 `nvidia-smi` 能在终端运行；AMD 在 Windows 上注册表可能没有
  `qwMemorySize`，脚本会按 CPU/核显模式降级建议，不影响手动指定模型。
- **Apple Silicon**：统一内存，脚本按可用内存折算（约 RAM 的 60–70%）；M 系列 16 GB 建议 8B Q4，
  24 GB 以上可尝试 14B/27B 小量化。
- **`--check` 全部显示"无法检查"**：HuggingFace 不可达（网络受限）。下载前请确认能访问
  `huggingface.co`，或先手动下载到 `models/gguf/` 再 `import_model.py --gguf`。
- **下载后 sha256 不匹配**：上游可能重新上传过文件；保留 `.bad` 文件，重新 `--check` 确认元数据或
  用发布方公布的校验值重试。
- **想加自己的模型**：编辑 `scripts/model_catalog.json`，补 `repo` / `file` / `size_gb` / `min_vram_gb`，
  然后 `python scripts/model_advisor.py --check`。

## Linux / macOS 对等

选型、下载（含校验）、导入三个脚本都是纯 Python，Windows 上的 PowerShell 脚本只是薄包装。
Linux/macOS 直接用 `python3 scripts/model_advisor.py`、`python3 scripts/download_model.py`、
`python3 scripts/import_model.py` 即可，行为与 Windows 一致。启动脚本 `scripts/start.sh` 会检查
Python、生成配置、探测 Ollama，并在没有 GGUF 时提示先跑选型/下载。
