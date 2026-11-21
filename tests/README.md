# tests/ —— 自动化测试

本目录是 qwensese 的测试套件，**纯 Python 标准库**，零第三方依赖、零外部服务。
不需要 Ollama、不需要模型文件、不需要显卡、不需要联网，也不会写入仓库本身。

## 怎么跑

```bash
# 全部测试（布局/合规检查 + 硬件选型 + 端到端 API）
python tests/run_all.py

# strict 模式：发布元数据文件缺失也当 FAIL（CI 用这个）
python tests/run_all.py --strict

# 只跑布局与发布合规检查（不启动任何服务，几秒内结束）
python tests/run_all.py --skip-api

# 只跑某一个
python tests/run_all.py --only test_layout
python tests/run_all.py --only test_advisor
python tests/run_all.py --only test_api
```

也可以单独运行：

```bash
python tests/test_layout.py
python tests/test_advisor.py
python tests/test_api.py
```

要求 Python **3.10+**（用到了 `sys.stdlib_module_names`；3.10 以下会跳过这一项检查）。
Windows / Linux / macOS 均可运行（不需要 Ollama 或模型）。

## 测试文件

| 文件 | 用例数 | 作用 |
| --- | --- | --- |
| `test_layout.py` | 21 + 1 个 XFAIL | 仓库布局、角色卡解析、发布合规扫描、安全实现静态检查、git 跟踪检查 |
| `test_advisor.py` | 16 | 硬件选型的纯函数测试（不联网、不需要 GPU）、下载器/导入器 dry-run、脚本编码 |
| `test_api.py` | 50 | 临时副本里启动真实 `web/app.py`，端到端 HTTP 测试（含安全校验与 `/api/hardware`） |
| `run_all.py` | — | 依次运行上面三个，汇总 PASS/FAIL 并给退出码 |

`run_all.py` 退出码：**0 = 全部通过且 FAIL 数为 0**；1 = 有失败。
输出里每个用例一行 `PASS` / `FAIL`，最后有 `SUMMARY pass=.. fail=.. xfail=..`。

### XFAIL（已知差异，不计 FAIL）

本服务（Python HTTP 层）不提供 `/api/version`、`/api/tags` 路由：它们是 **Ollama 上游**接口
（`app.py` 内部会请求 `CONFIG.ollama + "/api/version"`），对本服务 GET 这两个路径返回 404。
该差异单独记为 `XFAIL`，不计入 FAIL；对应 API 测试断言 `/api/version` 返回 404。

## 覆盖什么

### test_layout.py

- 必需目录（`web/ personas/ prompts/ config/ docs/ tests/ scripts/`）与核心文件存在。
- 发布元数据文件（`README.md`、`docs/API.md`、`scripts/*.ps1`、`.github/workflows/ci.yml` 等）存在性检查：
  默认缺失只打印 **WARN 不计 FAIL**（便于开发阶段跑），`--strict` 时缺失即 FAIL。
- **clean clone 保护**：`config/current_user_persona.txt` 是 `.gitignore` 忽略的运行时指针，
  缺失只 WARN；同时新增"必需/发布文件必须在 `git ls-files` 里"的用例——这两个用例一起保证
  "从 GitHub 全新 clone 后跑测试不会因缺运行时文件而 FAIL"。
- `web/app.py` 能被 `ast.parse`；`web/app.js` 大小合理且含 `fetch(`。
- **零依赖静态检查**：`web/*.py` 的 import 只能是标准库或本仓库模块（`web/` 与 `scripts/` 都算本地模块，
  `app.py` 通过 `sys.path` 引入 `model_advisor` 是这个口径）。
- `personas/*.md` 能按 `## 字段名` 解析；角色卡核心字段（名字/年龄/身份/外貌/性格/说话风格/
  行为准则/尺度/禁忌）齐全；真实角色卡名字非空、年龄 ≥ 18、尺度在 0–3。
- NSFW 词扫描（`乳头 阴茎 射精 性交 高潮 淫`），带**白名单**并逐条说明：
  目前唯一命中是 `web/importer.py` 里的内容过滤词表（那行是导入器的屏蔽词清单，不是正文）。
  白名单之外命中即 FAIL。
- 仓库文本文件里没有 Windows 盘符绝对路径，也没有公网隧道域名（`trycloudflare.com`）与口令链接（`?k=<key>`）。
- **安全实现静态检查**：`app.py` 里必须有 `SECURITY_HEADERS`、Host/Origin/Content-Type 校验、
  `SameSite=Strict`、`WRITE_LOCK`、`/api/hardware`、`hardware_payload`。
- `web/config.example.json` 合法、`access_key` 为空、`port`/`ollama` 合法，且安全开关
  （`host_check` / `origin_check` / `require_json`）默认 `true`。
- `scripts/model_catalog.json` 是合法 JSON、模型 id 不重复、至少一个 `tested: true`。

### test_advisor.py

- `model_catalog.json` 结构与量化字段合法（9 个模型 / 24 个量化）。
- 推荐纯函数回归：CPU 兜底 ≤8B、4GB→≤8B、8GB→8B 级、**16GB→`huihui-27b` + `UD-IQ4_XS`**
  （本仓库作者实测组合，防止以后有人手滑改坏推荐）、24GB→27B/32B 级。
- `prefer_uncensored` 开关、装不下时的 fallback（不崩溃且有 warning）、`num_ctx` 随显存单调不减。
- `detect_hardware()` 返回结构合法（无 GPU 也允许，CI 上是 CPU 兜底）。
- CLI：`--json` 合法 JSON、`--list` 列全部候选、`--download --dry-run` 不联网不落盘。
- 下载器 `sha256_file()` 与 `hashlib` 一致；导入器 `--dry-run` 生成含 `FROM` / `SYSTEM` / `PARAMETER` 的 Modelfile。
- `scripts/model-advisor.ps1` 是纯 ASCII（PowerShell 5.1 兼容要求）。

### test_api.py

- 把 `web/ personas/ prompts/ config/ scripts/` 复制到临时目录，用随机空闲端口启动真实 `web/app.py`；
  不碰仓库本身，测试结束清理进程与临时目录。
- 阶段 1（无口令）：GET/POST 主要接口、字段合并写回、存档流程、错误处理（400/404/500/503）、
  `/v1` 兼容层、`/api/health` & `/api/state`。
- **安全加固用例**：每个响应带 CSP/nosniff/X-Frame-Options；`Host: evil.example` → 403；
  跨站 `Origin` 的 POST → 403；同源 Origin 放行（走到路由层 404）；`text/plain` POST → 415；
  `/api/hardware` 返回 200 且结构含 GPU 信息与下载/导入命令。
- 阶段 2（`access_key=testkey`）：重启服务，验证无钥匙 403、`X-Access-Key` / cookie `qk` / `?k=`
  三种带法 200、错误钥匙 403、口令页种 cookie、cookie 为 `SameSite=Strict`。
- 每次结束断言进程退出、端口释放、临时目录删除。

## CI

`.github/workflows/ci.yml` 在 **Ubuntu / Windows** × **Python 3.10 / 3.12** 上跑
`python tests/run_all.py --strict`，并额外跑 `model_advisor.py --json` / `--list` 的 smoke test
（CI 机器没有 GPU，走 CPU 兜底路径）。

## 约定

- 新行为必须同步加测试；安全相关改动至少要有"挡住攻击"的负向用例。
- 不引入第三方测试库（pytest 等），继续用本目录的 `@case` 装饰器风格，保证零依赖。
