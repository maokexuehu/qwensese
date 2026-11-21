# tests/ —— 自动化测试

本目录是 qwensese 的测试套件，**纯 Python 标准库**，零第三方依赖、零外部服务。
不需要 Ollama、不需要模型文件、不需要联网，也不会写入仓库本身。

## 怎么跑

```bash
# 全部测试（布局/合规检查 + 端到端 API）
python tests/run_all.py

# 只跑布局与发布合规检查（不启动任何服务，几秒内结束）
python tests/run_all.py --skip-api

# 只跑某一个
python tests/run_all.py --only test_layout
python tests/run_all.py --only test_api
```

也可以单独运行：

```bash
python tests/test_layout.py
python tests/test_api.py
```

要求 Python **3.10+**（用到了 `sys.stdlib_module_names`；3.10 以下会跳过这一项检查）。
Windows / Linux / macOS 均可运行（不需要 Ollama 或模型）。

## 测试文件

| 文件 | 用例数 | 作用 |
| --- | --- | --- |
| `test_layout.py` | 16 + 1 个 XFAIL | 仓库布局、角色卡解析、发布合规扫描 |
| `test_api.py` | 43 | 临时副本里启动真实 `web/app.py`，端到端 HTTP 测试 |
| `run_all.py` | — | 依次运行上面两个，汇总 PASS/FAIL 并给退出码 |

`run_all.py` 退出码：**0 = 全部通过且 FAIL 数为 0**；1 = 有失败。
输出里每个用例一行 `PASS` / `FAIL`，最后有 `SUMMARY pass=.. fail=.. xfail=..`。

### XFAIL（已知差异，不计 FAIL）

本服务（Python HTTP 层）不提供 `/api/version`、`/api/tags` 路由：它们是 **Ollama 上游**接口
（`app.py` 内部会请求 `CONFIG.ollama + "/api/version"`），对本服务 GET 这两个路径返回 404。
该差异单独记为 `XFAIL`，不计入 FAIL；对应 API 测试断言 `/api/version` 返回 404。

## 覆盖什么

### test_layout.py

- 必需目录（`web/ personas/ prompts/ config/ docs/ tests/ scripts/`）与核心文件存在。
- 发布元数据文件（`README.md`、`docs/API.md`、`scripts/*.ps1` 等）存在性检查：
  默认缺失只打印 **WARN 不计 FAIL**（便于开发阶段跑），`--strict` 时缺失即 FAIL。
- `web/app.py` 能被 `ast.parse`；`web/app.js` 大小合理且含 `fetch(`。
- `personas/*.md` 能按 `## 字段名` 解析；角色卡核心字段（名字/年龄/身份/外貌/性格/说话风格/
  行为准则/尺度/禁忌）齐全；真实角色卡名字非空、年龄 ≥ 18、尺度在 0–3。
- NSFW 词扫描（`乳头 阴茎 射精 性交 高潮 淫`），带**白名单**并逐条说明：
  目前唯一命中是 `web/importer.py` 里的内容过滤词表（那行是导入器的屏蔽词清单，不是正文）。
  白名单之外命中即 FAIL。
- 仓库文本文件里没有 Windows 盘符绝对路径，也没有公网隧道域名（`trycloudflare.com`）与口令链接（`?k=<key>`）。
- `web/config.example.json` 存在、是合法 JSON、`access_key` 为空。
- `web/*.py` 只 import 标准库或同目录本地模块（守住"零第三方依赖"）。
- 文本文件 UTF-8 可解码、无 BOM（`tests/` 下 BOM 直接 FAIL；其它文件只 WARN）。
- `.gitignore` 忽略存档 / 本机配置 / 模型 / 日志；仓库里没有运行时私有数据。

### test_api.py

在 `tempfile` 临时根里复制 `web/ personas/ prompts/ config/`，写一份测试用
`web/config.json`，用 `subprocess` 启动真实 `web/app.py`，全部通过 `urllib` 发 HTTP 请求：

- **服务**：轮询 `/api/health` 直到 200（最多 20 秒；失败打印进程输出便于诊断）。
- **只读接口**：`/api/health`、`/health`、`/api/state`（角色卡 ≥2 且含 `persona.md`、
  `root` 必须等于临时根而不是仓库）、`/api/persona?id=persona.md`（`fields` 含名字）、
  `/api/models`（离线也能列出预设模型、`ollama_ok` 为 bool）、`/v1/models`、
  `/api/me`、`/api/me/list`、`/api/ctx`（含角色卡 + 叙事模式时 system 变长）。
- **写入接口（都在临时副本里）**：
  - `/api/persona/save` 只提交一个字段 → 其它字段保留（验证合并写）；
  - `/api/persona/save` 新建测试卡 → `/api/state` 数量 +1；
  - `/api/persona/delete` 删除后数量恢复；
  - `/api/persona/save` 传 `../../evil-persona.md` → 只写进 `personas/`（路径收敛）；
  - `/api/saves` 初始为空 → `POST` 写 2 条 history → 返回 id 且 title 回退成第一句 user 消息 →
    单条读取 → 带 id 更新（title 覆盖、`saved_at` 保留）→ `/api/saves/delete` 后列表归零；
  - `/api/saves` 的 id 传 `../../evil-save` → 收敛成 `evil-save`，且落盘位置、响应 id、
    `/api/saves` 列表三处一致；id 全是不合法字符（如 `"!!!"`）→ 400。
- **错误处理**：畸形 JSON 400（「请求体不是合法 JSON」）、非对象 JSON 400
  （「请求体必须是 JSON 对象」）、`/api/chat` 的 `messages` 不是数组 400、
  未知路径 GET/POST 404。
- **静态页**：`GET /` 返回 HTML、`GET /app.js` 返回 JS。
- **鉴权门禁**：把临时 `config.json` 的 `access_key` 改成 `testkey` 并重启服务，验证
  无钥匙 `/api/state`、`/api/health`、`/`（口令页）、`POST /api/chat` 全部 403；
  `X-Access-Key: testkey`、`Cookie: qk=testkey`、`?k=testkey` 三种带法均 200，
  且 `?k=` 会下发 `qk` cookie；错误钥匙 403 且不下发 cookie；口令页种 cookie 后可继续访问。

### 值得注意的实现行为（测试按实际接口断言）

- `/api/version`、`/api/tags` 属于 Ollama 上游接口，不是本服务路由（见上文 XFAIL）。
- `/api/chat` 传**空** `messages` 不会返回 4xx：空数组能通过列表校验，请求会走到
  "确保 Ollama 在线"分支；测试里临时副本没有 `ollama.exe` 且 Ollama 指向空闲端口，
  因此确定性返回 **503**（Ollama 未启动），测试按 503 断言。

## 设计要点（为什么这么写）

- **绝不写仓库**：所有目录先复制到临时根，服务进程 `cwd` = 临时根；`ROOT` 从 `app.py`
  文件位置推导，所以角色卡 / 存档 / 用户设定全部落在临时目录，测试结束临时目录整个删除。
- **端口安全**：用 `socket.bind(("127.0.0.1", 0))` 拿空闲端口，并显式断言不等于
  8717 / 8718 / 11434。
- **Ollama 确定性离线**：只复制 `web/ personas/ prompts/ config/`（不含 `ollama/`），并把
  配置里的 `ollama` 指向一个刚释放的空闲端口 —— 这样本机是否真的开着 Ollama 都不影响结果。
- **清理**：`try/finally` 里 `terminate()` → `wait(5)` → 必要时 `kill()`；结束后单独检查
  "无残留进程 / 端口已释放 / 临时目录已删除"，任一项不满足即 FAIL。
- **编码**：测试输出强制 UTF-8；子进程设 `PYTHONIOENCODING=utf-8`，避免 Windows 控制台
  中文乱码。
