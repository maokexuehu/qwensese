# Changelog

本项目的变更记录遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.1.0] - 2026-11-21

首个公开版本。

### Added

- 本地可视化对话台：纯 Python 标准库的 `ThreadingHTTPServer` + 原生 JS 单页前端，零第三方依赖、无构建步骤。
- 角色卡系统：`personas/*.md` 以 `## 字段名` 分段解析；页面内编辑保存即合并写回，SYSTEM 每轮现场拼装，改卡即时生效。
- 系统提示层与文风层：`prompts/system-core.md`、`prompts/style-guide.md`，随请求注入 SYSTEM。
- 流式对话：NDJSON 逐行转发 Ollama 输出；思考链过滤（开头 `<think>` 块、零宽字符、残缺标记）与开头垃圾清理。
- 对话操作：重新生成、撤回上一轮、叙事者模式（第三人称多人物）、回复长度预设（短/中/长/超长）与超长自动续写。
- 上下文管理：token 估算、超出预算自动丢弃最早对话、把前段对话压成摘要（可撤销）、自动压缩开关。
- "帮我想"：结合最近对话、角色设定与"我"的设定，生成 3～5 条可接的台词/动作建议。
- 角色卡生成：从现成对话提取、从一段描述生成；两步预览与确认后才写盘；同时支持生成"我"的自我角色卡。
- "我的角色卡"：`config/user_personas/` 下多张卡随时切换，`current_user_persona.txt` 记录当前指针。
- 对话存档：`saves/*.json` 自动/手动保存、列表排序、载入续聊、删除；标题缺失时自动回退；存档 id 自动收敛为 `[A-Za-z0-9_-]`（非法字符剔除，收敛后为空返回 400），落盘路径 / 响应 / 列表三处保持一致。
- 统一的 400 错误文案：非法 JSON 返回「请求体不是合法 JSON」，合法 JSON 但非对象返回「请求体必须是 JSON 对象」。
- 模型管理：模型下拉、上下文长度切换、温度/top_p/复读惩罚调节；Ollama 未启动时尝试拉起项目内 `ollama/ollama.exe`。
- 访问口令：`access_key` 非空时校验所有页面与 API，支持 `?k=`、cookie `qk`、`X-Access-Key`，常量时间比较；默认只监听 `127.0.0.1`。
- OpenAI 兼容层：`/v1/models`、`/v1/chat/completions`（含 SSE 流式）；虚拟模型名 `qwensese/<角色>/<尺度>` 与 `qwensese/c<序号>/<尺度>` 用于切换角色与尺度。
- 一键脚本：`scripts/start.ps1`、`scripts/start.bat`、`scripts/start.sh`、`scripts/download-model.ps1`、`scripts/import-model.ps1`。
- 文档：`README.md`、`docs/quickstart.md`、`docs/configuration.md`、`docs/API.md`、`docs/architecture.md`。
- 自动化测试：`tests/run_all.py`（仓库布局合规检查 + 真实 HTTP 端到端测试，纯标准库，不需要模型权重）。
- 项目元数据：MIT `LICENSE`、`.gitignore`、`.gitattributes`、`CONTRIBUTING.md`、`SECURITY.md`、`requirements.txt`（零依赖说明）。

### Security

- 默认只监听回环地址 `127.0.0.1`，不对局域网开放。
- `access_key` 非空时页面与所有 API 一律校验，比较使用 `hmac.compare_digest`。
- 角色卡路径只取 basename 且强制 `.md`；存档 id 收敛为 `[A-Za-z0-9_-]`，防目录穿越。
