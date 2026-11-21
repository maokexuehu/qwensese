# 贡献指南

感谢有兴趣参与改进 qwensese。本文说明开发环境、代码风格、测试与 PR 流程。

## 开发环境

- Python **3.10+**（只用标准库，开发环境为 3.12）；不需要 `pip install` 任何东西。
- Ollama（可选）：只有真实跑模型时需要；跑自动化测试不需要 Ollama，也不需要模型权重。
- Windows 上直接用 PowerShell 5.1+；Linux/macOS 用系统自带终端即可。

准备本地配置（首次）：

```powershell
copy web/config.example.json web/config.json
```

启动开发服务：

```powershell
python web/app.py
# 可选参数：--lan 绑定 0.0.0.0；--port 8717；--host 127.0.0.1
```

注意：开发和调试时请不要把 `web/config.json` 里的 `access_key` 提交到仓库（该文件已被 `.gitignore` 忽略）。

## 代码风格

- **后端**：Python 标准库，不引入第三方依赖。与现有 `web/app.py`、`web/importer.py` 保持一致：4 空格缩进、中文注释解释"为什么"、函数保持短小、错误信息用中文。
- **前端**：原生 JavaScript/CSS，不引入框架、不使用构建工具。保持 `web/index.html` + `web/app.js` + `web/style.css` 三文件结构，新增 API 时同步更新 `docs/API.md`。
- **文本编码**：所有文本文件统一 UTF-8（无 BOM）；换行遵循 `.gitattributes`（`*.py`/`*.js`/`*.md`/`*.json` 等为 LF，`*.ps1`/`*.bat` 为 CRLF）。
- **文档**：中文为主；命令与路径一律写仓库内相对路径，不要写作者本机的绝对路径。
- **接口兼容**：对外 API 路径与字段名保持稳定；确需修改时，同一个 PR 里更新 `docs/API.md` 与 `CHANGELOG.md`。

## 如何跑测试

测试全部是标准库实现，可以在没有任何模型的情况下运行：

```powershell
python tests/run_all.py
```

- `tests/test_layout.py`：检查仓库目录/文件完整性、`web/app.py` 可被 `ast.parse`、角色卡可解析、无绝对路径/密钥/NSFW 关键词等。
- `tests/test_advisor.py`：硬件选型的纯函数测试（不联网、不需要 GPU），含 16GB→27B 实测组合的回归保护。
- `tests/test_api.py`：把仓库复制到临时目录，启动真实 `web/app.py`（随机空闲端口），用 `urllib` 覆盖主要 GET/POST 接口、字段合并写、存档流程与 `access_key` 鉴权；不需要 Ollama 与模型。
- `python tests/run_all.py --skip-api`：只跑快速布局检查（不启动服务，几秒结束）。
- `python tests/run_all.py --strict`：布局检查把"发布元数据文件缺失"也当作 FAIL（默认只 WARN）。
- `python tests/run_all.py --only test_api`：只跑某一个测试文件。

各用例覆盖范围见 `tests/README.md`。

提交 PR 前请确保 `tests/run_all.py` 退出码为 0。CI（`.github/workflows/ci.yml`）会在 Ubuntu/Windows
× Python 3.10/3.12 上跑同一套测试；新增行为请优先补测试而不是只改文档。已知短板与拆分计划见
[docs/roadmap.md](docs/roadmap.md)，欢迎认领。

## PR 流程

1. Fork 仓库，从默认分支切出主题分支（例如 `fix/chat-stream`、`docs/quickstart`）。
2. 尽量保持 PR 小而聚焦：一个 PR 解决一个问题，不要混入无关格式化。
3. 本地跑 `python tests/run_all.py`，确认全部通过。
4. 提交 PR 时说明：改动内容、动机、验证方式（跑了什么命令、结果如何）；涉及界面的附截图。
5. 如果改了 API/配置/脚本参数，请在同一个 PR 中更新对应文档（`docs/API.md`、`docs/configuration.md`、`scripts/README.md`）。

评审时可能会要求补测试或拆分 PR；合并后由维护者更新 `CHANGELOG.md` 的 `Unreleased` 部分。

## 不要提交的内容

仓库会发布到公开 GitHub，请务必不要把以下内容提交上来（`.gitignore` 已忽略大部分，但仍需留意 `git add -f` 之类误操作）：

- **模型权重**：`models/`、`gguf/`、`*.gguf`、`ollama/ollama*.exe`、`cloudflared/`。
- **对话存档**：`saves/`（包含私人聊天记录）。
- **本机配置与密钥**：`web/config.json`（可能含 `access_key`）、任何 `.env`、token、口令。
- **运行时日志**：`logs/`。
- **个人角色卡**：`personas/private/` 或任何你自己写的、不想公开的角色卡/提示词。
- **`config/current_user_persona.txt`**：运行时状态文件（已忽略）。

## 角色卡与内容类贡献

- 示例角色必须是 **18 岁及以上的虚构角色**；不要提交未成年人、真实人物、真实伤害或违法内容。
- 默认示例保持克制、以人设与写作质量取胜；露骨内容不要进入仓库示例文件。
- 新增示例角色卡请同时在 `personas/README.md` 之外的地方（如 PR 说明）描述它的用途，避免和默认示例重复。

## 许可证

提交代码即表示你同意以本仓库的 [MIT License](LICENSE) 授权你的贡献。模型权重与第三方内容不适用 MIT 许可，详见 `NOTICE.md`。
