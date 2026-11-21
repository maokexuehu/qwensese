# Changelog

本项目的变更记录遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 风格，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.2.0] - 2026-11-22

"按显卡选模型 + 安全加固 + clean clone 可测"版本。

### Added

- **硬件选型工具链（跨平台，纯 Python）**：
  - `scripts/model_advisor.py`：检测显卡/显存/内存/CPU（`nvidia-smi` → Windows 注册表 → Linux sysfs → macOS `system_profiler` 逐级降级），按显存档位推荐 Qwen3 GGUF 模型与量化，给出下载/导入命令；支持 `--json`、`--list`、`--check`、`--download`。
  - `scripts/download_model.py`：断点续传（HTTP Range）+ SHA256 校验（读取 HuggingFace LFS 元数据，或 `--sha256` 手工指定）；不匹配时改名 `.bad` 并失败。
  - `scripts/import_model.py`：复用 `web/app.py` 的 `build_system()` 生成 Modelfile（SYSTEM/TEMPLATE/num_ctx），执行 `ollama create`；支持 `--dry-run`。
  - `scripts/model_catalog.json`：候选模型数据文件（可直接编辑），含实测标记与上游变动提示。
  - `scripts/model-advisor.ps1`：Windows 薄包装（纯 ASCII）。
- `GET /api/hardware`：返回硬件检测结果与推荐（缓存 5 分钟），供网页使用。
- 网页新增「🖥️ 硬件选型」按钮与面板（`web/extra.js`）：展示显存/档位/推荐模型及可复制的下载、导入命令。
- 首次访问的 18+ 内容提示（前端本地确认，`adult_only` 可关）。
- `docs/model-selection.md`（选型/下载/校验/导入/14B 路径/Linux 对等/硬件账单）、
  `docs/security-model.md`（威胁矩阵与残余风险）、`docs/roadmap.md`（已知短板与计划）。
- GitHub Actions：`ci.yml`（Ubuntu/Windows × Python 3.10/3.12 全量测试）、`release.yml`（tag 自动打包发布）。
- issue / PR 模板、`CODE_OF_CONDUCT.md`。
- 测试：`tests/test_advisor.py`（16 个用例，含 16GB→27B 实测组合的回归保护）；layout 新增"运行时文件可选"、"必需文件必须被 git 跟踪"、"安全校验静态检查"等用例；API 测试新增安全响应头/Host/Origin/Content-Type/`/api/hardware` 用例。

### Changed

- 下载/导入/选型以纯 Python 脚本为主，PowerShell 脚本改为薄包装（Linux/macOS 获得对等体验）。
- README 重写：明确"首次要下十几 GB""16 GB 级显存""磁盘/内存/时间"账单，移除"3 步开始"的淡化表述（改为 4 步并附 Linux 命令）。
- `web/config.example.json` 增加 `host_check` / `origin_check` / `require_json` / `allow_insecure_lan` / `adult_only` / `access_log`。
- `scripts/start.sh` 在没有 GGUF 时提示先用选型/下载工具；README 与 API 文档同步 20 个 `/api/*` 路径。

### Security

- 新增 Host 白名单校验（防 DNS rebinding）与 POST 的 Origin/Referer 校验（防跨站删卡/烧卡）。
- 写接口强制 `application/json`（防表单 CSRF）；新增全局安全响应头（CSP、nosniff、X-Frame-Options、Referrer-Policy、COOP/CORP、Permissions-Policy）。
- 口令 cookie 收紧为 `SameSite=Strict; HttpOnly`。
- 角色卡/存档/"我"的读-改-写共用可重入锁，修复并发丢更新。
- `--lan` 且无 `access_key` 时拒绝启动（可用 `--insecure-lan` / `allow_insecure_lan` 显式覆盖）。
- 可选访问日志 `access_log`（默认关闭）。

### Fixed

- **clean clone 测试失败**：`config/current_user_persona.txt` 是 `.gitignore` 忽略的运行时指针，却被列为必需文件；现在按运行时文件处理并新增 git 跟踪检查。
- 文档中缺失的 `qwensese-14b` 安装路径已补齐（见 `docs/model-selection.md`）。

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
