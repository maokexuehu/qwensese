# 快速开始：从零装到能对话

本文按"一台干净的机器"来写，全部命令都在**仓库根目录**执行，只使用仓库内相对路径。
完成后你会得到一个打开就能聊的本地页面：<http://127.0.0.1:8717/>。

## 0. 准备清单

| 项目 | 要求 | 检查方式 |
| --- | --- | --- |
| 操作系统 | Windows 10/11（一键脚本）；Linux/macOS 可手工启动 | — |
| Python | 3.10+，只用标准库 | `python --version` |
| Ollama | 建议最新版 | `ollama --version` |
| 磁盘 | 约 12～15 GB（放下 GGUF 模型） | — |
| 显存 | 推荐 16 GB 级显卡；显存不足会卸载到内存并明显掉速 | — |

把仓库下载到本地（`git clone` 或下载 ZIP 解压），然后进入仓库根目录：

```powershell
cd <仓库根目录>
```

下文所有 `scripts/...`、`web/...`、`models/...` 都是相对仓库根的路径。

## 1. 安装 Python 与 Ollama

### Python 3.10+

从 Python 官网下载 3.10 或更新版本安装。Windows 安装时勾选 **Add python.exe to PATH**，然后确认：

```powershell
python --version
```

### Ollama

从 Ollama 官网下载安装包并安装。Windows 安装器通常会注册一个后台服务，装完确认：

```powershell
ollama --version
```

如果 `ollama --version` 能执行但服务没有在跑，先手动启动一次：

```powershell
ollama serve
```

（保持这个窗口开着，或在后台运行；下面的启动脚本也会在服务不可达时尝试拉起项目内的
`ollama/ollama.exe`，但如果你的 Ollama 是系统安装的，建议先自己启动。）

确认 Ollama 服务在监听默认地址：

```powershell
curl.exe http://127.0.0.1:11434/api/version
```

> 安全提醒：Ollama 自身没有鉴权，只应监听 `127.0.0.1`，不要把它暴露到公网。

## 2. 下载模型权重（GGUF）

推荐模型：`huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF`，默认量化 `UD-IQ4_XS`（约 13.4 GB）。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/download-model.ps1
```

脚本会从 Hugging Face 下载到 `models/gguf/`，支持断点续传（中断后重新执行会接着下）。

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `-Quant` | `UD-IQ4_XS` | 量化档位：`UD-IQ4_XS` / `UD-DW-Q4_K_M` / `UD-Q4_K_XL` / `UD-Q3_K_XL` / `UD-Q5_K_XL` |
| `-Repo` | `huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF` | Hugging Face 仓库 |
| `-Dest` | `models/gguf` | 下载到的目录 |
| `-DryRun` | 关 | 只打印解析出的 URL 与目标路径，不实际下载 |

例子：

```powershell
# 想更省显存（约 12.41 GB）
powershell -ExecutionPolicy Bypass -File scripts/download-model.ps1 -Quant UD-Q3_K_XL
```

不想用脚本（或使用 Linux/macOS）时，可以手动下载后放到 `models/gguf/`，文件名保持
`Huihui-Qwen3.8-27B-abliterated-<量化>.gguf` 的形式（`import-model.ps1` 按这个规则找文件）。

## 3. 导入成 Ollama 模型

```powershell
powershell -ExecutionPolicy Bypass -File scripts/import-model.ps1
```

默认会以 `qwensese-27b` 为名创建模型，确认它出现在列表里：

```powershell
ollama list
```

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `-Name` | `qwensese-27b` | 生成的 Ollama 模型名（必须和 `web/config.json` 的 `model` 一致） |
| `-Quant` | `UD-IQ4_XS` | 要导入的量化档位，必须和已下载的文件一致 |
| `-GgufPath` | `models/gguf/Huihui-Qwen3.8-27B-abliterated-<Quant>.gguf` | 手动指定要导入的 gguf 文件路径 |
| `-NoStyle` | 关 | 不把 `prompts/style-guide.md` 拼进 SYSTEM |
| `-Vision` | 关 | gguf 目录里有 `mmproj*.gguf` 时一并作为视觉投影导入；没有则按纯文本模型导入 |
| `-Temperature` | `0.8` | 写进 Modelfile 的采样温度 |
| `-NumCtx` | `16384` | 写进 Modelfile 的上下文长度 |
| `-DryRun` | 关 | 只生成并打印 Modelfile，不调用 `ollama create` |

导入脚本会把 `personas/persona.md`、`prompts/system-core.md`（以及默认的 `prompts/style-guide.md`）
拼成 SYSTEM 写进模型，便于直接 `ollama run qwensese-27b` 或给第三方前端使用。

> 导入时 Ollama 必须已经在跑，并且它的模型库目录要和启动脚本一致（`OLLAMA_MODELS` 指向 `<仓库根>/models`），
> 否则这里导入的模型，后面的启动脚本可能找不到。常见做法是**另开一个终端**先跑 `scripts/start.ps1`
> （它会按上面的环境变量拉起 Ollama），再回到当前终端跑导入脚本；或者按导入脚本提示手动设置
> `OLLAMA_MODELS` 后执行 `ollama serve`。

**注意**：Web 页面并不依赖这个内置 SYSTEM —— 页面每次请求都会重新拼 SYSTEM，所以改角色卡不需要重跑导入。
只有"直接用 Ollama/命令行聊"时才需要重新导入。

## 4. 启动页面

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

或者直接双击 `scripts/start.bat`。Linux/macOS：

```bash
chmod +x scripts/start.sh    # 首次运行需要执行权限
./scripts/start.sh
# 允许局域网访问：./scripts/start.sh --lan
# 等价手工命令：
python3 web/app.py
```

启动脚本会做这些事：

- 若 `web/config.json` 不存在，从 `web/config.example.json` 复制一份（不改模板本身）。
- 读取配置里的 `port`（默认 `8717`）。
- 若 `127.0.0.1:11434` 不可达，优先尝试项目内 `ollama/ollama.exe`，否则用 PATH 上的 `ollama`，
  并设置一组省显存的 `OLLAMA_*` 环境变量（见 [configuration.md](configuration.md)），日志写到 `logs/`。
- 前台运行 `python web/app.py`，并打印访问地址。

看到提示后，用浏览器打开：

<http://127.0.0.1:8717/>

关闭运行窗口即停止页面（不会停止 Ollama）。

已经导入过模型、只想手工启动时：

```powershell
python web/app.py
# 可选：--lan 绑定 0.0.0.0；--port 8717；--host 127.0.0.1
```

## 5. 配置（可选）

配置文件是 `web/config.json`（首次启动自动生成）。常用键：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `model` | `qwensese-27b` | Ollama 模型名，必须和导入时一致 |
| `ollama` | `http://127.0.0.1:11434` | Ollama 地址 |
| `num_ctx` | `16384` | 默认上下文长度（界面里可再切） |
| `host` | `127.0.0.1` | 监听地址；默认只有本机可访问 |
| `port` | `8717` | 页面端口 |
| `access_key` | `""` | 访问口令；留空不校验 |

完整说明见 [configuration.md](configuration.md)。

### 手机 / 局域网访问

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -Lan
```

`-Lan` 会绑定 `0.0.0.0`，启动日志会打印本机局域网 IP。手机与电脑在同一 Wi-Fi 下时，用手机浏览器打开
`http://<本机局域网IP>:8717/` 即可。**开放局域网前请在 `web/config.json` 设置 `access_key`**，否则同网络内任何人都能改你的角色卡与存档。

用 Chatbox 之类的客户端时，把 API Host 填成 `http://<本机局域网IP>:8717/v1`，
模型名可以用 `qwensese-27b`，或用虚拟模型名 `qwensese/<角色>/<尺度>` 切角色与尺度（见 [API.md](API.md)）。

## 6. 第一次对话与换角色

- 左侧是角色卡列表：默认有 **林晚**（书店店主）与 **周屿**（研究生）两个示例。
- 点角色卡右侧的铅笔图标可以编辑字段，保存后会**立即写回** `personas/*.md`，下一条消息就生效。
- 想换人设：复制 `personas/template.md` 新建一张卡（页面里"＋ 新建角色卡"更简单），或直接改 `personas/persona.md`。
- 想快速要一张新卡：点"✨ 生成角色卡"，可以从一段现成对话提取，或从一段描述生成，两步预览确认后才保存。
- 尺度（0～3）、温度、`top_p`、复读惩罚在顶栏与右侧设置里调。
- 对话默认存到浏览器本地；打开"对话存档"可手动保存/载入到 `saves/`，也可以开启每轮自动保存。
- 对话很长时用"把前面的对话压成摘要"，可撤销。

## 常见问题

**`python` 不是内部或外部命令 / 找不到 python**
安装时没勾选 Add to PATH。重新运行安装程序勾选，或用 `py -3` 代替 `python`。

**端口被占用（`端口 8717 已被占用`）**
页面可能已经开着了；或者改 `web/config.json` 的 `port` / 启动时加 `--port 8718`。

**页面能打开但发消息报 503**
Ollama 没在运行。先 `ollama serve` 或确认系统里的 Ollama 服务在跑，再试。

**报错"Ollama 里找不到模型「qwensese-27b」"**
模型名不一致（看 `ollama list`），或 `OLLAMA_MODELS` 指向了目录联接（junction），Ollama 读不到；
把 `OLLAMA_MODELS` 指到真实模型目录。模型确实没导入时重跑 `scripts/import-model.ps1`。

**速度慢 / 显存不够**
调小界面上的上下文长度（27B 超过 12k 会明显掉速），或换更小的量化（如 `UD-Q3_K_XL`）；
显存不足时 Ollama 会把部分层放到内存，速度可能掉到 1/3。

**改了角色卡但"命令行聊"没变化**
Web 页面改卡即时生效；但模型内置的 SYSTEM 是导入时写死的，直接用 `ollama run qwensese-27b` 需要重跑
`scripts/import-model.ps1`。

**忘记访问口令**
查看 `web/config.json` 的 `access_key`；或清空它并重启页面（仅限本机自用场景）。

**页面样式/功能异常**
用浏览器强制刷新（Ctrl+F5）；确认 `web/index.html`、`web/app.js`、`web/style.css` 都在。

**想重置所有个人数据**
删除 `saves/`、（可选）`config/current_user_persona.txt` 与你自己新建的角色卡即可；`web/config.json` 也删掉会重新生成默认配置。
