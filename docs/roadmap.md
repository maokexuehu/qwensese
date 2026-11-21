# 路线图（Roadmap）

公开记录"已知短板 → 计划中的处理"，避免这些问题只留在作者脑子里。顺序按性价比排，
不承诺具体时间；欢迎用 issue 认领或提出不同意见。

## v0.2.0 已处理（本次发布）

- **clean clone 测试失败**：`config/current_user_persona.txt` 是运行时指针（`.gitignore` 忽略），
  之前被误列为必需文件。现在测试把它当"可选运行时文件"，并新增"必需文件必须被 git 跟踪"的用例；
  GitHub Actions 在 Ubuntu/Windows × Python 3.10/3.12 上跑全量测试。
- **硬件账单说清楚 + 选型工具**：`scripts/model_advisor.py` 按显存/显卡档位/内存推荐 Qwen GGUF
  量化；`/api/hardware` 与网页「硬件选型」按钮；README 不再淡化十几 GB 下载与显存门槛。
- **Linux/macOS 对等**：下载、导入、选型全部提供纯 Python 实现（含断点续传与 SHA256 校验），
  PowerShell 脚本退化为 Windows 包装。
- **写盘并发丢更新**：角色卡/存档/"我"的读-改-写共用一把可重入锁（`WRITE_LOCK`）。
- **跨站请求 / DNS rebinding**：Host 白名单、Origin/Referer 校验、JSON Content-Type 强制、
  CSP 等安全响应头、cookie `SameSite=Strict`；`--lan` 无口令拒绝启动。
- **权重完整性**：下载器支持 HuggingFace LFS sha256 自动校验与 `--sha256` 手工校验。
- **内容安全边界**：新增前端 18+ 确认、`docs/security-model.md` 明确"无内容护栏/无年龄验证"。
- **社区验证入口**：CI、Release 工作流（tag 自动打包发布）、issue/PR 模板、路线图。

## 下一步（按优先级）

### P1 · 单文件巨石拆分（app.py 约 2200 行 / app.js 约 83 KB）

目标不是"为了分层而分层"，而是让改动有边界、有测试保护：

- `web/core/`（纯标准库包）拆出：`config.py`、`personas.py`、`saves.py`、`prompts.py`
  （SYSTEM 拼装）、`models.py`（Ollama 调用与模型目录）、`auth.py`（口令/Host/Origin）。
- `web/app.py` 只保留 `Handler` 路由与 `main()`，目标 < 400 行。
- `web/app.js` 按功能拆成 ES modules（聊天流、角色卡弹窗、存档、选型面板），
  `index.html` 改 `<script type="module">`；现代浏览器都支持，不需要构建步骤。
- 拆分前先把 tests 覆盖率补齐到关键纯函数（build_system / parse_persona / 存档合并），
  拆分本身按"行为完全不变"的 PR 提交。

### P1 · 静态检查进 CI（仅开发期依赖）

- 加 `ruff`（lint + format check）与 `mypy --strict`（或 pyright）到 CI，**不进入运行时依赖**；
  先对新增 `scripts/*.py` 与 `web/core/*` 生效，再逐步覆盖 `app.py`。
- `scripts/` 的 Python 文件补类型注解，给下载/校验/导入路径加单元测试。

### P2 · 认证与网络暴露

- 可选 CSRF token（session cookie + header 双提交），在"口令已设置"模式下默认开启。
- 登录失败限速（内存滑动窗口，按 IP），避免口令被在线枚举。
- 可选 `--auth-file` / 多口令（给"家里几个人共用一台机器"的场景）。
- 访问日志轮转（按大小切分），以及"只记录写操作"的精细开关。

### P2 · 模型与硬件支持

- 扩充 `scripts/model_catalog.json`：更多实测组合（8B/14B abliterated、Qwen3-30B-A3B MoE）、
  每个条目标注"谁在什么显卡上实测过、速度多少"。
- 增加 `--benchmark`：用固定 prompt 跑 Ollama `/api/chat`，输出 tok/s，验证推荐不是拍脑袋。
- Apple Silicon / AMD ROCm / Intel Arc 的实际性能数据与显存识别改进。
- 可选自动更新检查（只提示，不自动下载）。

### P3 · 部署与易用性

- Dockerfile / compose（挂载 `personas/`、`models/`，默认只绑 127.0.0.1）。
- 一键安装器（Windows：检测 Python/Ollama/显存 → 装依赖 → 下模型 → 起服务）。
- 英文文档（README 已有英文摘要，`docs/` 目前以中文为主）。
- 性能基准与回归数据（记录"哪个量化在哪个卡上多少 tok/s"）。

## 明确不做（设计约束）

- **不引入运行时第三方 Python 包**：零依赖是本项目的核心卖点；需要依赖的功能优先用标准库实现，
  或做成可选的外部脚本。
- **不做多用户/云托管**：没有账号、数据库、多租户隔离的规划；想多用户请自己加反向代理与鉴权。
- **不承诺内容审查**：不做输出分类器或年龄验证服务，安全边界以文档告知与使用者责任为主。
