# 架构说明

本文说明 qwensese 的三层结构、一次对话请求的完整链路，以及角色卡、存档、鉴权、流式输出等关键实现。

## 总览

```text
┌──────────────────────────────┐
│ 浏览器单页（web/）             │
│ index.html + app.js + style.css│
│ 原生 JS，无构建、无框架         │
└──────────────▲───────────────┘
               │ HTTP（默认 http://127.0.0.1:8717）
               │ JSON / NDJSON / SSE
┌──────────────┴───────────────┐
│ Python 标准库 HTTP 服务        │
│ web/app.py（ThreadingHTTPServer）│
│  · 路由与鉴权（access_key）     │
│  · 现场拼 SYSTEM               │
│  · 读写角色卡 / 存档 / 用户设定  │
│  · 流式转发 + 思考链清理        │
│ web/importer.py（被 app.py 调用）│
│  · 对话/描述 → 角色卡字段草稿    │
└──────────────▲───────────────┘
               │ HTTP（http://127.0.0.1:11434）
               │ /api/chat、/api/tags、/api/version
┌──────────────┴───────────────┐
│ Ollama（本机推理服务）          │
│ 模型 qwensese-27b / qwensese-14b│
│ GGUF 权重，GPU/CPU 推理        │
└──────────────────────────────┘
```

三层职责：

| 层 | 位置 | 职责 |
| --- | --- | --- |
| 前端 | `web/index.html`、`web/app.js`、`web/style.css` | 渲染对话、管理本地状态（`localStorage`）、调用 API、解析流式响应、编辑角色卡与存档 |
| 服务层 | `web/app.py`（约 2000 行）、`web/importer.py` | HTTP 路由、鉴权、SYSTEM 拼装、文件读写、调用 Ollama、转发流 |
| 推理层 | 本机 Ollama 进程 + GGUF 模型 | 实际生成 token；模型权重不在本仓库 |

没有数据库、没有进程内会话状态、没有第三方依赖：所有持久化都是项目目录里的 Markdown / JSON 文件。

## 一次对话请求的链路

以前端"发送"为例（`web/app.js` → `POST /api/chat`）：

1. 前端把新输入追加到 `state.history`（只保留 `user`/`assistant`），写入 `localStorage`。
2. 发送前按预算先裁一次历史（`num_ctx - 输出预留 - 3% 余量`，至少保留 5 条）。
3. 请求体带 `messages`（完整历史）、`persona_id`、`level`、`use_style`、`extra_system`、`mode`、`summary`、采样参数。
4. 服务端 `_chat()`：
   - 读取角色卡字段（`parse_persona`）；
   - `build_system()` 现场拼 SYSTEM（见下节）；
   - 过滤历史（只留 `user`/`assistant` 且内容非空）；
   - 按预算再从最早历史开始丢弃（至少保留 4 条），必要时生成提示行；
   - 组装 Ollama 请求（`stream: true`、`think: false`、`keep_alive: 30m`）；
   - `stream_chat()` 转发并清理思考链。
5. 前端逐行解析 NDJSON，累积 `message.content`，完成后把整条回复追加到历史。

### SYSTEM 的拼装顺序（`build_system`）

1. `prompts/system-core.md`（去掉 H1 与引用块说明后的正文）
2. `======== 角色卡 ========` + 13 个标准字段（`FIELD_ORDER`），开场白在"已出现过角色回复"时省略
3. 角色卡里的额外自定义字段（如 `世界观`、`结尾格式` 等）
4. `======== 用户（我）的设定 ========` + `config/user_personas/` 当前选中卡的字段
5. `======== 前情摘要（更早的剧情，已压缩）========` + 请求里的 `summary`
6. `【当前尺度档位】N —— 说明`
7. `======== 本轮额外指令 ========` + `extra_system`
8. `======== 本轮篇幅 ========`（`min_chars` > 0 时）
9. 叙事者模式提示块（`mode == "narrator"` 时）
10. 输出格式约束（要求直接给正文，不要思考过程）
11. 角色卡里的 `结尾格式`（若有，要求每轮最后一行照抄）
12. `prompts/style-guide.md`（`use_style` 为真时）

因为 SYSTEM 是**每次请求现场拼**的，改角色卡、改提示词、切尺度、切模式都是下一条消息生效，不需要重建模型。
（导入脚本把 SYSTEM 写进 Ollama 模型只是为"命令行/第三方前端"准备的另一条路径。）

### 无状态对话

服务端不保存任何对话状态：

- 历史由前端每次请求完整带上（`messages`）；刷新页面/换设备后从 `localStorage` 恢复。
- 存档是显式动作：`POST /api/saves` 把历史写成 `saves/<id>.json`；自动保存会覆盖同一份存档。
- 所以同一个服务可以被多个标签页/设备同时使用，彼此历史互不干扰（存档除外）。

### NDJSON 流式转发与思考链清理

Ollama 的 `/api/chat` 流本身就是 NDJSON（一行一个 JSON）。服务端在 `stream_chat()` 里：

- 给最后一条 `user` 消息追加一条预填的 `assistant: "*"`，让模型直接续写正文，减少先写思考块的概率。
- 逐行解析 Ollama 输出，删除 `message.thinking` 字段；
- `ThinkFilter`：开头先攒 24 个字符，见到 ` thinking`/`</think>` 标记就切掉；没有台词/引号等正文特征时最多再等 900 字；思考块一直不闭合时最多攒 6000 字后放行。
- `MarkerScrubber`：删掉正文中间夹带的思考标记，末尾可能是半个标记时先按住等下一段。
- 把清理后的对象原样写回给浏览器（仍是一行一个 JSON），最后一行带 `done: true`。
- 浏览器中途断开（停止生成/关页面）会触发 `OSError`，服务端静默结束并关闭上游连接。

`/api/import` 是另一条 NDJSON 流：`importer.analyze()` 通过 `progress` 回调把 `parse`/`chunk`/`reduce`/`synth` 等阶段事件逐行发给前端，最后 `done` 带草稿。

### 上下文预算与压缩

- 估算：中文按 `1.25 字符 ≈ 1 token`（`CHARS_PER_TOKEN`）。
- 预算：`num_ctx - num_predict - max(64, 3% × num_ctx)`，留给 system + 历史 + 输出。
- 超预算：服务端从最早的历史开始丢（至少剩 4 条），并额外发一条 `{"note": ...}` 提示；前端发送前也会先裁一次（至少剩 5 条）。
- 压缩：`POST /api/compress` 让模型把最近最多 60 条对话压成 300～500 字摘要；摘要之后作为 `summary` 字段传回，替代逐条历史。
  前端把压缩前状态存在 `localStorage` 备份里，支持"撤销压缩"。

## 角色卡：解析、合并与写入

- 角色卡是 `personas/*.md`，正文用 `## 字段名` 分段；`<!-- -->` 注释会被剥离；H1 与引用块说明在拼 SYSTEM 时被 `clean_doc()` 去掉。
- 标准字段顺序（`FIELD_ORDER`）：`名字 / 年龄 / 身份 / 与用户的关系 / 前情提要 / 外貌 / 性格 / 说话风格 / 行为准则 / 尺度 / 禁忌 / 开场白 / 示例对话`；额外的 `## 字段名` 也保留并使用。
- 列表与摘要：`list_personas()` 遍历 `*.md`，跳过 `README.md` 与 `template.md`；`name` 取 `名字`，缺失回退文件名；`level` 取 `尺度`（缺失 2，夹到 0～3）。
- 写入：`POST /api/persona/save` 会先读旧卡，把本次没提交的旧字段补回来（**合并写**），再按固定字段顺序重写整张卡；`尺度` 强制规整为 0～3；文件名只取 basename 且强制 `.md`。
- 原子性：所有卡与存档写入都先写临时文件、`fsync` 后 `os.replace`；Windows 上文件被其它线程占用导致替换失败时会重试，最后退化为直接写。
- "我的角色卡"逻辑相同，位于 `config/user_personas/*.md`，字段集不同（`名字 / 称呼 / 性别 / 年龄 / 种族 / 身份 / 外貌 / 性格 / 说话风格 / 喜好 / 其他`），当前选择记录在 `config/current_user_persona.txt`；首次运行会自动建 `default.md`。

## 存档 JSON 结构

存档位于 `saves/<id>.json`；id 只保留 `[A-Za-z0-9_-]`（传入 id 的非法字符会被自动剔除，收敛后为空则返回 400；
新建时由服务端生成"时间戳 + 毫秒 + 随机后缀"）。

```json
{
  "id": "s202611211030001234",
  "name": "林晚 · 2026-11-21 10:30",
  "title": "今天不买书，就是想坐一会儿。",
  "auto": true,
  "persona_id": "persona.md",
  "persona_name": "林晚",
  "level": 2,
  "mode": "dialogue",
  "model": "qwensese-27b",
  "num_ctx": 12288,
  "len": 1,
  "saved_at": "2026-11-21 10:30",
  "updated_at": "2026-11-21 10:42",
  "summary": "",
  "sum_count": 0,
  "history": [
    {"role": "user", "content": "今天不买书，就是想坐一会儿。"},
    {"role": "assistant", "content": "*她把柜台上的书挪开，腾出一块地方。* \"坐吧。\""}
  ]
}
```

- `history` 只保留合法 role 与内容；标题缺失时列表接口会回退到第一条用户内容（前 40 字），再回退角色名。
- 列表接口返回摘要（`msg_count`、`chars`、`last_line`、时间戳等），按 `updated_at` 倒序。
- 删除时若文件正被其它线程读取（Windows 常见），会重试并最终改名成 `.deleted`，列表只认 `.json` 所以立刻"消失"。
- `saves/` 与 `logs/`、`models/`、`web/config.json` 都被 `.gitignore` 忽略，不会进仓库。

## 访问口令（`access_key`）门禁

- 配置：`web/config.json` 的 `access_key`；非空才启用。
- 入口：`do_GET` / `do_POST` 开头统一调用 `check_access()`，因此页面、静态资源、所有 API 都受同一道门禁保护。
- 口令来源优先级：cookie `qk` → 请求头 `X-Access-Key` → 查询参数 `?k=`；比较使用 `hmac.compare_digest`。
- 通过 `?k=` 认证成功会下发 cookie（30 天、`SameSite=Lax`、`HttpOnly`）。
- 失败：`GET /` 返回 HTML 口令页（403）；其它路径返回 JSON 403。
- 没有用户体系、没有速率限制：口令即全部权限。

更多安全细节见 [SECURITY.md](../SECURITY.md)。

## 硬件选型与模型工具链（0.2.0）

浏览器点「🖥️ 硬件选型」→ `GET /api/hardware` → `app.py` 的 `hardware_payload()` 懒加载
`scripts/model_advisor.py`（通过 `sys.path` 引入，不复制逻辑）→ `detect_hardware()` 调外部命令
（`nvidia-smi` / 注册表 / sysfs / `system_profiler`）→ `recommend()` 用 `scripts/model_catalog.json`
做纯函数推荐 → 结果缓存 300 秒返回。检测失败只影响推荐，不影响对话主链路。

`scripts/` 下的三个工具互相独立、可单独使用：

```text
model_advisor.py  ——检测硬件、读 catalog、算推荐，可选 --download 转调下载器
download_model.py ——HF 元数据取 sha256 → Range 断点续传 → 校验 → 写 .sha256
import_model.py   ——import web/app.py 的 build_system() 生成 Modelfile → ollama create
```

`import_model.py` 复用 `app.py` 的 `build_system()`，所以"网页里改角色卡即时生效"与"导入模型的
内置 SYSTEM"来自同一份拼装逻辑；不会出现两套提示词行为不一致。

## 模型列表与虚拟模型名

- `model_list()` 调 Ollama `/api/tags` 拿到已安装模型，结合内置预设（`qwensese-27b`、`qwensese-14b`）补充中文标签、推荐 `num_ctx` 与可选上下文档位；未安装的预设也列出（`missing`/`offline` 标记）。
- `/v1` 兼容层用"虚拟模型名"把角色卡与尺度编码进模型名：`qwensese/<角色>/<0-3>`；`<角色>` 支持文件名、去后缀、卡内名字、`c<序号>`；解析不到时回退 `personas/persona.md`。
- `/v1/models` 为每张卡展开 4 个尺度并同时给出 `stem` 与 `c<序号>` 两种写法。

## 生成角色卡的两步预览确认流程

"✨ 生成角色卡"是一个**两步确认**流程，避免模型草稿直接落盘：

1. **第 1 步：输入**
   - 目标：`角色卡（AI 演的人）` / `自我角色卡（"我"）`；
   - 方式：`从对话提取`（可指定谁是"我"、可勾选"只用本地统计（不调模型）"）/ `从描述生成`；
   - 提交后按目标调用：`POST /api/import`（角色卡·对话，NDJSON 进度）、`POST /api/me/import`（"我"·对话）、`POST /api/card/gen`（描述生成，任意目标）。
   - 后两个只返回草稿；都不落盘。

2. **第 2 步：预览编辑**
   - 前端把草稿渲染成可编辑字段（长字段用多行文本域），显示"填好 X/Y 个字段"与提示/警告；
   - 可以"← 返回改输入"重来；
   - 点"确认加入"才真正保存：角色卡走 `POST /api/persona/save`，自我角色卡走 `POST /api/me`（并设为当前）。

所以模型生成的任何内容都要经过用户过目与确认，才会写进 `personas/` 或 `config/user_personas/`。

## Ollama 的自启动

- `ollama_up()` 用 `/api/version` 做 2 秒超时探活；`ensure_ollama()` 在需要时尝试拉起来。
- 优先使用项目内 `ollama/ollama.exe`（不存在则放弃，由用户自行启动系统 Ollama）。
- 启动时设置：`OLLAMA_MODELS=<项目根>/models`、`OLLAMA_HOST=127.0.0.1:11434`、`OLLAMA_KEEP_ALIVE=30m`、`OLLAMA_FLASH_ATTENTION=1`、`OLLAMA_KV_CACHE_TYPE=q8_0`、`OLLAMA_NUM_PARALLEL=1`、`OLLAMA_MAX_LOADED_MODELS=1`；日志写 `logs/ollama.web.log`。
- 最多等待约 90 秒；失败返回"Ollama 没起来"。
- `POST /api/ollama/start` 是同一逻辑的手动入口。

## 前端状态（`localStorage`）

前端不依赖后端会话，关键状态都存在浏览器本地：

| 键前缀 | 内容 |
| --- | --- |
| `qwensese:hist:<角色卡id>` | 该角色的对话历史 |
| `qwensese:current` | 当前选中的角色卡 id |
| `qwensese:model` / `qwensese:ctx:<模型>` / `qwensese:len` | 模型、上下文长度、回复长度预设 |
| `qwensese:settings` | 尺度、温度、top_p、复读惩罚、文风开关等 |
| `qwensese:mode:<角色卡id>` | 该角色的对话模式（对话/叙事者） |
| `qwensese:save:<角色卡id>` | 自动保存对应的存档 id |
| `qwensese:sum:<角色卡id>` / `qwensese:sumbak:<角色卡id>` | 前情摘要 / 压缩前备份（用于撤销） |
| `qwensese:level:<角色卡id>` | 每个角色单独的尺度偏好 |
| `qwensese:autosave` / `qwensese:autosum` | 自动保存 / 自动压缩开关 |

## 边界与已知取舍

- **单用户**：没有多用户、权限、配额；`access_key` 是唯一的访问控制。
- **无 TLS**：HTTPS 需自行用反向代理提供。
- **无速率限制**：本地自用场景没有做限流。
- **HTTP/1.0**：服务端为简单可靠使用 HTTP/1.0，流式接口靠关闭连接表示结束；并发请求由 `ThreadingHTTPServer` 的线程处理。
- **模型权重不在仓库**：首次使用需要自行下载 GGUF 并导入（见 [quickstart.md](quickstart.md)）。
- **前端无构建**：`app.js` 是直接运行的普通脚本，没有打包步骤，也因此没有 npm 供应链。
