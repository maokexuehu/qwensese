# HTTP API 参考

本文覆盖 qwensese 本地服务（`web/app.py`）提供的全部 HTTP 接口。所有接口都由同一个
`ThreadingHTTPServer` 提供，默认地址为 `http://127.0.0.1:8717`（由 `web/config.json` 的 `port` 决定）。

- 本服务接口：19 个 `/api/*` 路径（21 个方法-路径组合）+ `/v1` 兼容层 2 个端点（另有 `/health`、`/models`、`/chat/completions` 三个别名）。
- **不是本服务提供的**：`/api/tags`、`/api/version` 是 Ollama 上游接口（`http://127.0.0.1:11434`），本服务不代理它们，详见文末「Ollama 上游接口」。
- 需要看架构与数据流，见 [architecture.md](architecture.md)；配置项见 [configuration.md](configuration.md)。

## 基础约定

### 传输格式

| 场景 | Content-Type |
| --- | --- |
| 普通 JSON 请求/响应 | `application/json; charset=utf-8` |
| 对话流（`/api/chat`） | `application/x-ndjson; charset=utf-8` |
| 导入流（`/api/import`） | `application/x-ndjson; charset=utf-8` |
| OpenAI 兼容流（`/v1/chat/completions`） | `text/event-stream; charset=utf-8` |
| 访问口令页（GET `/`） | `text/html; charset=utf-8` |

- 所有 JSON 响应禁止缓存：`Cache-Control: no-store`。
- 请求体一律是 JSON 对象（`{}` 或省略请求体等价于空对象）。两类请求体错误都返回 `400`，但文案不同：
  - 不是合法 JSON：`{"error": "请求体不是合法 JSON"}`
  - 是合法 JSON 但不是对象（例如传了数组或字符串）：`{"error": "请求体必须是 JSON 对象"}`
- 字符串编码一律 UTF-8；`ensure_ascii=False`（中文直接输出，不转义）。
- 服务端为 HTTP/1.0（`server_version: qwensese-ui/1.0`），流式接口靠关闭连接表示结束。

### 鉴权（`access_key`）

若 `web/config.json` 的 `access_key` 非空，则**所有页面与 API**（包括 `/`、`/app.js`、`/style.css`）都要携带口令；
留空时完全不校验。

口令可从三处提供，优先级从高到低：

1. Cookie：`qk=<access_key>`
2. 请求头：`X-Access-Key: <access_key>`
3. 查询参数：`?k=<access_key>`

通过查询参数首次认证成功时，响应会附带：

```http
Set-Cookie: qk=<access_key>; Path=/; Max-Age=2592000; SameSite=Lax; HttpOnly
```

失败时：

- `GET /` 或 `/index.html` 返回 `403` + 一个 HTML 口令输入页（`text/html`）。
- 其它路径返回 `403` + JSON：`{"error": "需要访问钥匙：在网址后面加 ?k=（管理员给的那串）"}`。

注意：口令比较使用常量时间比较（`hmac.compare_digest`）；没有速率限制，也没有锁定机制，请使用足够长的随机口令。
安全细节见 [SECURITY.md](../SECURITY.md)。

### 状态码约定

| 状态码 | 含义 |
| --- | --- |
| 200 | 成功（部分"业务失败"也用 200 + `ok: false`，见各接口说明） |
| 400 | 参数/请求体不合法（JSON 解析失败、类型不对、文件名不合法、文本过短等） |
| 403 | `access_key` 已设置但未携带或不匹配 |
| 404 | 路径不存在，或请求的角色卡/存档不存在 |
| 500 | 服务端异常（Ollama 调用失败、模型不存在等） |
| 503 | Ollama 不可用（`/api/chat`、`/api/import`、`/api/ollama/start` 的部分路径） |

通用错误响应体：

```json
{"error": "错误说明"}
```

### 接口索引

| # | 方法 | 路径 | 用途 |
| --- | --- | --- | --- |
| 1 | GET | `/api/health`（别名 `/health`） | 存活探针 |
| 2 | GET | `/api/state` | 角色卡列表 / 模型 / 状态 |
| 3 | GET | `/api/persona` | 读取单张角色卡 |
| 4 | GET | `/api/models` | Ollama 模型列表 |
| 5 | GET | `/api/ctx` | SYSTEM 上下文估算 |
| 6 | GET | `/api/me` | 当前"我"的角色卡 |
| 7 | GET | `/api/me/list` | "我"的角色卡列表 |
| 8 | GET | `/api/saves` | 存档列表 / 单个存档 |
| 9 | POST | `/api/persona/save` | 保存/合并角色卡 |
| 10 | POST | `/api/persona/delete` | 删除角色卡 |
| 11 | POST | `/api/me` | 保存"我"的角色卡 |
| 12 | POST | `/api/me/delete` | 删除"我"的角色卡 |
| 13 | POST | `/api/chat` | 主对话（NDJSON 流） |
| 14 | POST | `/api/compress` | 上下文压缩为摘要 |
| 15 | POST | `/api/suggest` | 生成下一步建议 |
| 16 | POST | `/api/card/gen` | 从描述生成角色卡草稿 |
| 17 | POST | `/api/import` | 从对话导入角色卡（NDJSON 进度） |
| 18 | POST | `/api/me/import` | 从对话提取"我"的设定 |
| 19 | POST | `/api/saves` | 保存对话存档 |
| 20 | POST | `/api/saves/delete` | 删除对话存档 |
| 21 | POST | `/api/ollama/start` | 尝试拉起项目内 Ollama |
| 22 | GET | `/v1/models`（别名 `/models`） | OpenAI 兼容模型列表 |
| 23 | POST | `/v1/chat/completions`（别名 `/chat/completions`） | OpenAI 兼容对话 |

---

## 系统与状态

### 1. `GET /api/health`

存活探针，等同 `GET /health`。不需要请求体。

**响应 200**

```json
{"ok": true, "ollama": true, "personas": 2}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ok` | boolean | 服务存活，恒为 `true` |
| `ollama` | boolean | Ollama 上游（`CONFIG.ollama + "/api/version"`）是否 2 秒内可达 |
| `personas` | number | `personas/` 下被识别为角色卡的数量 |

**错误**：仅 `403`（口令未通过）。

### 2. `GET /api/state`

页面启动时拉取的状态快照。

**响应 200**

```json
{
  "personas": [
    {
      "id": "persona.md",
      "name": "林晚",
      "age": "29",
      "role": "市中心一家旧书店的店主，二楼住人。",
      "level": 2,
      "file": "persona.md",
      "path": "<项目根>/personas/persona.md"
    },
    {
      "id": "example-mentor.md",
      "name": "周屿",
      "age": "24",
      "role": "同校研二，实验室和你隔一条走廊，管着那台老掉牙的离心机。",
      "level": 1,
      "file": "example-mentor.md",
      "path": "<项目根>/personas/example-mentor.md"
    }
  ],
  "model": "qwensese-27b",
  "server_ok": true,
  "levels": {
    "0": "纯剧情。身体接触止于牵手拥抱，不写情欲。",
    "1": "暧昧张力：目光、距离、呼吸、布料摩擦。不写具体身体。",
    "2": "情色场景：可写身体与感官，节奏放慢，重氛围与反应。",
    "3": "直白露骨：不避讳器官与行为，可用粗口。"
  },
  "root": "<项目根绝对路径>"
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `personas` | array | 角色卡摘要列表；`level` 为卡里 `尺度` 字段（缺失按 2，范围 0～3） |
| `model` | string | `web/config.json` 的 `model` |
| `server_ok` | boolean | 同 `/api/health` 的 `ollama` |
| `levels` | object | 尺度档位 0～3 的说明文案 |
| `root` | string | 项目根目录的绝对路径（本机路径，前端仅作展示/调试） |

角色卡目录里名为 `README.md`、`template.md` 的文件不会被当作角色卡列出；其余 `*.md` 都会被视为角色卡。

**错误**：仅 `403`。

### 3. `GET /api/persona`

读取一张角色卡的字段。

**查询参数**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | `personas/` 下的文件名（如 `persona.md`）；只取 basename，必须是 `.md` |

**响应 200**

```json
{
  "file": "persona.md",
  "fields": {
    "名字": "林晚",
    "年龄": "29",
    "身份": "市中心一家旧书店的店主，二楼住人。",
    "与用户的关系": "熟客。你每周三晚上来，买走一本旧诗集，从不还价。",
    "前情提要": "",
    "外貌": "个子不高，习惯把长发随手绾起来……",
    "性格": "- 说话慢，喜欢把话说到一半停住，看你反应。……",
    "说话风格": "- 称呼你\"你\"，偶尔叫\"这位客人\"来拉开距离。……",
    "行为准则": "- 不主动投怀送抱。靠近是一寸一寸的。……",
    "尺度": "2",
    "禁忌": "- 不要写成\"知心姐姐开导你\"的鸡汤走向。……",
    "开场白": "*晚上九点四十，店里的灯已经关了一半。……*",
    "示例对话": "**用户**：今天不买书，就是想坐一会儿。……"
  }
}
```

`fields` 是完整的字段字典：按 `## 字段名` 解析后的结果，值为去掉首尾空白后的字符串；卡里额外的自定义字段也会出现在这里。

**错误**

| 状态码 | 情况 |
| --- | --- |
| 400 | `id` 缺失/为空，或不是 `.md` 后缀（`{"error":"必须是 .md"}`） |
| 404 | 文件不存在（`{"error":"角色卡不存在"}`） |
| 403 | 口令未通过 |

---

## 模型与上下文

### 4. `GET /api/models`

列出 Ollama 里已安装的模型，并把内置预设里"还没装"的模型也标出来。

**响应 200**

```json
{
  "models": [
    {
      "id": "qwensese-27b:latest",
      "name": "qwensese-27b",
      "label": "27B · 质量最高",
      "num_ctx": 12288,
      "ctx_choices": [8192, 12288, 16384],
      "gb": 13.4,
      "family": "qwen3",
      "params": "27B",
      "default": true
    },
    {
      "id": "qwensese-14b",
      "name": "qwensese-14b",
      "label": "14B · 快 1.5 倍（未安装）",
      "num_ctx": 32768,
      "ctx_choices": [16384, 24576, 32768],
      "gb": 0,
      "family": "",
      "params": "",
      "default": false,
      "missing": true,
      "offline": false
    }
  ],
  "ollama_ok": true
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `models[].id` | string | 传给 Ollama 的模型名（可能带 `:latest`） |
| `models[].name` | string | 去掉 `:latest` 的短名 |
| `models[].label` | string | 显示文案；内置预设带中文标签，未知模型等于名字 |
| `models[].num_ctx` | number | 推荐上下文长度（内置预设值，未知模型回退到配置的 `num_ctx`） |
| `models[].ctx_choices` | array | 界面可选上下文长度 |
| `models[].gb` | number | 模型文件大小（GiB，保留 1 位小数） |
| `models[].family` / `params` | string | Ollama `details.family` / `details.parameter_size` |
| `models[].default` | boolean | 是否为配置里的 `model` |
| `models[].missing` | boolean | 仅内置预设未安装时出现 |
| `models[].offline` | boolean | 仅"未安装且 Ollama 不可达"时出现 |
| `ollama_ok` | boolean | Ollama 是否可达（键名是 `ollama_ok`，不是 `ollama`） |

内置预设：`qwensese-27b`（27B · 质量最高，推荐上下文 12288）、`qwensese-14b`（14B · 快 1.5 倍，推荐上下文 32768）。
Ollama 不可达时 `models` 里仍会包含预设模型（带 `missing`/`offline` 标记），不会报错。

**错误**：仅 `403`。

### 5. `GET /api/ctx`

估算当前角色卡拼出的 SYSTEM 占多少上下文（前端用它显示"上下文进度"）。

**查询参数**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `persona_id` | 否 | 角色卡文件名；缺失时按空卡计算 |
| `level` | 否 | 尺度 0～3；缺失时用卡里的 `尺度`（再缺省 2） |
| `mode` | 否 | `dialogue`（默认）或 `narrator`；`narrator` 会把叙事者提示块计入 |

**响应 200**

```json
{"system_chars": 5120, "system_tokens": 4096, "chars_per_token": 1.25}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `system_chars` | number | SYSTEM 字符串字符数 |
| `system_tokens` | number | 估算 token 数（`int(len / 1.25) + 1`） |
| `chars_per_token` | number | 当前使用的换算系数（1.25 字符 ≈ 1 token） |

计算时会排除"开场白"字段（对话里已经演过第一句），并按当前"我"的角色卡计入用户设定。

**错误**：仅 `403`。

---

## 角色卡（AI 扮演的人）

### 6. `POST /api/persona/save`

保存角色卡。**只提交部分字段时会与旧卡合并**（旧卡里有、本次没提交的字段会保留），因此适合做"局部编辑"。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | string | 否 | 目标文件名（`personas/` 下的 `.md`，只取 basename）；缺省时按 `角色-<名字>.md` 生成，非法文件名字符会被去掉 |
| `fields` | object | 否 | 字段字典；值会被统一转成字符串（数组用 `、` 连接，null 变空串） |

```json
{
  "file": "persona.md",
  "fields": {
    "名字": "林晚",
    "尺度": "2",
    "禁忌": "- 不要写成\"知心姐姐开导你\"的鸡汤走向。"
  }
}
```

写入规则：

- 13 个标准字段按固定顺序写出：`名字 / 年龄 / 身份 / 与用户的关系 / 前情提要 / 外貌 / 性格 / 说话风格 / 行为准则 / 尺度 / 禁忌 / 开场白 / 示例对话`。
- `尺度` 会被强制规整为 0～3 的整数（无法解析时按 2）。
- 其余自定义字段（如 `世界观`、`称呼`）追加在标准字段之后，按提交顺序写出。
- 写入采用"临时文件 + 原子替换"，不会出现写了一半的卡。

**响应 200**

```json
{"ok": true, "file": "persona.md", "personas": [ /* 同 /api/state 的 personas */ ]}
```

**错误**

| 状态码 | 情况 |
| --- | --- |
| 400 | `fields` 不是对象；`file` 不是 `.md` 后缀；请求体非法 |
| 403 | 口令未通过 |

### 7. `POST /api/persona/delete`

删除角色卡。删除不存在的文件也返回成功。

**请求体**

```json
{"file": "example-mentor.md"}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | string | 是 | `personas/` 下的 `.md` 文件名 |

**响应 200**

```json
{"ok": true, "personas": [ /* 删除后的角色卡列表 */ ]}
```

**错误**：`400`（`file` 为空或不是 `.md`）、`403`。

---

## "我的角色卡"（用户在 AI 眼里的设定）

### 8. `GET /api/me`

读取当前"我"的角色卡字段，同时返回全部可选列表。数据在 `config/user_personas/*.md`，
当前选择记录在 `config/current_user_persona.txt`。

**查询参数**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `file` | 否 | 指定读取哪个文件；缺省读当前选中的那张 |

**响应 200**

```json
{
  "fields": {
    "名字": "阿雪",
    "称呼": "阿雪",
    "性别": "男",
    "年龄": "19",
    "种族": "雪狐族",
    "身份": "雪狐族王室出身，排行第三；从小受的是宫廷教养，会读书、会箭术。",
    "外貌": "雪白的长发垂到肩胛……",
    "性格": "表面端得住，说话不急……",
    "说话风格": "短句，常用反问……",
    "喜好": "喜欢安静的屋子、旧书和甜的东西……",
    "其他": "耳朵和尾巴是真的，不是装饰……"
  },
  "file": "default.md",
  "list": [
    {"file": "default.md", "name": "阿雪", "sub": "雪狐族王室出身，排行第三", "current": true}
  ],
  "path": "<项目根>/config/user_personas/default.md"
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `fields` | object | 选中（或 `file` 指定）卡的字段；空值字段会被省略 |
| `file` | string | 当前选中/实际使用的文件名 |
| `list[].name` | string | 卡里 `名字` 字段，缺失回退文件名 |
| `list[].sub` | string | 副标题：依次取 `身份` / `种族` / `称呼` |
| `list[].current` | boolean | 是否当前选中 |
| `path` | string | 当前文件的绝对路径（本机路径，仅展示/调试用） |

**错误**：仅 `403`。

### 9. `GET /api/me/list`

精简版列表，等价于 `/api/me` 但只返回列表与当前文件名。

**响应 200**

```json
{"list": [{"file": "default.md", "name": "阿雪", "sub": "雪狐族王室出身，排行第三", "current": true}], "file": "default.md"}
```

**错误**：仅 `403`。

### 10. `POST /api/me`

保存"我"的角色卡（可新建），并可顺带设为当前。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `fields` | object | 否 | 字段字典；值统一转字符串；与已有卡合并（旧卡字段保留） |
| `file` | string | 否 | 目标文件名（自动补 `.md`）；缺省写入当前选中的卡 |
| `select` | boolean | 否 | 默认 `true`：保存后设为当前使用的卡 |

```json
{
  "file": "我-阿雪.md",
  "select": true,
  "fields": {"名字": "阿雪", "称呼": "阿雪", "种族": "雪狐族"}
}
```

标准字段顺序：`名字 / 称呼 / 性别 / 年龄 / 种族 / 身份 / 外貌 / 性格 / 说话风格 / 喜好 / 其他`，自定义字段追加在后。

**响应 200**

```json
{"ok": true, "path": "<项目根>/config/user_personas/我-阿雪.md", "file": "我-阿雪.md", "list": [ /* 同 /api/me/list */ ]}
```

**错误**：`400`（`fields` 不是对象）、`403`。

### 11. `POST /api/me/delete`

删除"我"的角色卡。**不允许删除当前选中且只剩一张的卡**。

**请求体**

```json
{"file": "我-阿雪.md"}
```

**响应 200**

```json
{"ok": true, "list": [ /* 剩余列表 */ ], "file": "default.md"}
```

`ok: false` 表示拒绝删除（当前唯一一张）。

**错误**：仅 `403`。

---

## 对话存档

存档文件位于项目根的 `saves/` 目录（运行时生成，`.gitignore` 已忽略）。存档 id 只保留 `[A-Za-z0-9_-]`：
传入的 id 会自动剔除其它字符（例如 `../../evil-save` 收敛为 `evil-save`），保证落盘文件名、响应与列表三处一致；
收敛后为空则返回 `400`（`{"error":"存档 id 只允许字母、数字、下划线和短横线"}`）。

### 存档对象（完整）

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

| 字段 | 说明 |
| --- | --- |
| `id` | 存档 id（新建时由服务端生成：时间戳 + 随机后缀） |
| `name` | 存档显示名；缺省 `<角色名> · <保存时间>` |
| `title` | 存档标题；缺省取第一条用户内容前 60 字，再缺省角色名 |
| `auto` | 是否为自动保存（每轮覆盖同一份） |
| `persona_id` / `persona_name` | 关联的角色卡文件与名字 |
| `level` / `mode` | 尺度档位与对话模式（`dialogue` / `narrator`） |
| `model` / `num_ctx` / `len` | 当时的模型、上下文长度、回复长度预设索引 |
| `saved_at` / `updated_at` | `YYYY-MM-DD HH:MM` |
| `summary` / `sum_count` | 前情摘要与其覆盖的条数 |
| `history` | 只保留 `role` 为 `user`/`assistant` 且内容非空的条目 |

列表项（`GET /api/saves` 无 `id` 时）是存档的摘要：`id / name / title / auto / persona_id / persona_name / level / mode / model / num_ctx / len / saved_at / updated_at / msg_count / chars / last_line`，并按 `updated_at` 倒序。

### 12. `GET /api/saves`

**查询参数**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 否 | 给定则返回单份存档的完整内容，否则返回列表 |

**无 `id` 时响应 200**

```json
{
  "saves": [
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
      "msg_count": 12,
      "chars": 5120,
      "last_line": "她把杯子放在你手边，自己没坐，靠在书架边上看着你。"
    }
  ]
}
```

**带 `id` 时响应 200**：完整的存档对象（结构见上）。

**错误**

| 状态码 | 情况 |
| --- | --- |
| 400 | `id` 过滤后为空（不合法） |
| 404 | 存档不存在（`{"error":"存档不存在"}`） |
| 403 | 口令未通过 |

### 13. `POST /api/saves`

新建或更新存档。`id` 缺省时服务端生成新 id；同一 `id` 再次提交会更新该存档并刷新 `updated_at`。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `history` | array | 是 | `[{"role":"user"|"assistant","content":"..."}]`；其它 role 会被丢弃 |
| `id` | string | 否 | 存档 id；缺省新建。非法字符会被自动剔除，收敛为 `[A-Za-z0-9_-]` |
| `name` / `title` / `auto` | string/string/boolean | 否 | 显示名、标题、是否自动保存 |
| `persona_id` / `persona_name` | string | 否 | 关联角色 |
| `level` / `mode` / `model` / `num_ctx` / `len` | 否 | 尺度、模式、模型、上下文、长度预设 |
| `summary` / `sum_count` | string/number | 否 | 前情摘要与条数 |

```json
{
  "history": [
    {"role": "user", "content": "今天不买书，就是想坐一会儿。"},
    {"role": "assistant", "content": "*她把柜台上的书挪开，腾出一块地方。* \"坐吧。\""}
  ],
  "persona_id": "persona.md",
  "persona_name": "林晚",
  "level": 2,
  "mode": "dialogue",
  "model": "qwensese-27b",
  "num_ctx": 12288,
  "auto": true
}
```

**响应 200**

```json
{"ok": true, "id": "s202611211030001234", "saves": [ /* 更新后的列表摘要 */ ]}
```

**错误**：`400`（`history` 不是数组；请求体非法；或 id 收敛后为空 —— `{"error":"存档 id 只允许字母、数字、下划线和短横线"}`）、`403`。

### 14. `POST /api/saves/delete`

删除存档。删除不存在的 id 也返回成功（id 同样会先收敛为 `[A-Za-z0-9_-]` 再查找）。

**请求体**

```json
{"id": "s202611211030001234"}
```

**响应 200**

```json
{"ok": true, "saves": [ /* 删除后的列表摘要 */ ]}
```

**错误**：仅 `403`。

---

## 对话

### 15. `POST /api/chat`

主对话接口。服务端现场拼 SYSTEM，把完整对话历史发给 Ollama，并逐行转发 Ollama 的流式响应（NDJSON）。

**请求体**

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `messages` | array | **是** | — | 完整对话历史 `[{"role":"user"|"assistant","content":"..."}]`，服务端无状态，历史由前端每次带上来 |
| `persona_id` | string | 否 | `""` | 角色卡文件名；缺失/非法时按空卡（只用内核） |
| `level` | number | 否 | 卡里 `尺度`（再缺省 2） | 尺度 0～3 |
| `use_style` | boolean | 否 | `true` | 是否拼接 `prompts/style-guide.md` |
| `extra_system` | string | 否 | `""` | 本轮额外指令（"这一轮写慢一点"之类） |
| `mode` | string | 否 | `dialogue` | `dialogue` 或 `narrator`（叙事者模式） |
| `summary` | string | 否 | `""` | 更早剧情的前情摘要 |
| `min_chars` | number | 否 | `0` | 本轮最少汉字数（超出会写进 SYSTEM 约束，前端"超长"档用它） |
| `model` | string | 否 | 配置 `model` | Ollama 模型名 |
| `num_ctx` | number | 否 | 配置 `num_ctx` | 上下文长度 |
| `num_predict` | number | 否 | `1024` | 单轮最大输出 token |
| `temperature` | number | 否 | `0.8` | 采样温度 |
| `top_p` | number | 否 | `0.92` | nucleus 采样 |
| `repeat_penalty` | number | 否 | `1.05` | 复读惩罚 |

```json
{
  "persona_id": "persona.md",
  "level": 2,
  "use_style": true,
  "extra_system": "",
  "temperature": 0.8,
  "top_p": 0.92,
  "repeat_penalty": 1.05,
  "model": "qwensese-27b",
  "num_ctx": 12288,
  "num_predict": 700,
  "min_chars": 0,
  "mode": "dialogue",
  "summary": "",
  "messages": [
    {"role": "user", "content": "今天不买书，就是想坐一会儿。"}
  ]
}
```

**响应 200**：`Content-Type: application/x-ndjson; charset=utf-8`

响应体是逐行的 JSON 对象（一行一个），字段与 Ollama `/api/chat` 的流式响应一致，另外：

- 开头可能出现若干提示行：`{"note": "..."}`（例如"这一段太长，已自动放掉最早的 N 条对话"），前端显示在输入框上方。
- 思考链字段（`message.thinking`）会被剥掉；开头 ` thinking` 块、零宽字符、残缺标记会被过滤（见 [architecture.md](architecture.md)）。

```text
{"note":"这一段太长，已自动放掉最早的 2 条对话（系统占 4096 tokens，剩 6789 给对话+输出）。想留住旧剧情就用 ⚙ →「把前面的对话压成摘要」"}
{"model":"qwensese-27b","created_at":"2026-11-21T10:40:00.000Z","message":{"role":"assistant","content":"*她把柜台"},"done":false}
{"model":"qwensese-27b","created_at":"2026-11-21T10:40:00.100Z","message":{"role":"assistant","content":"上的书挪开，"."},"done":false}
{"model":"qwensese-27b","created_at":"2026-11-21T10:40:03.000Z","message":{"role":"assistant","content":""},"done":true,"total_duration":9123456789,"eval_count":312}
```

客户端应逐行 `JSON.parse`，把 `j.message.content` 拼接起来显示；`j.done === true` 表示本轮结束。
服务端会在 SYSTEM 与输出之间预留预算：`num_ctx - num_predict - max(64, 3% num_ctx)`，超出时从最早的历史开始丢弃（至少保留 4 条）；若角色卡本身超预算，会额外给出提示行。

**错误**

| 状态码 | 情况 |
| --- | --- |
| 400 | 请求体非法 JSON / 非对象；`messages` 缺失或不是数组（`{"error":"messages 必须是 JSON 数组"}`） |
| 403 | 口令未通过 |
| 500 | Ollama 调用失败（含模型未安装：`Ollama 里找不到模型「qwensese-27b」…`） |
| 503 | Ollama 没起来且无法自动拉起（`{"error":"Ollama 没起来，也没能自动拉起。..."}`） |

### 16. `POST /api/compress`

把最近一段对话压成"前情摘要"，供后续对话作为 `summary` 使用，节省上下文。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `history` | array | 是 | 待压缩的对话；服务端只取最后 60 条、单条内容截断到 1200 字 |
| `model` | string | 否 | 默认配置模型；非法模型名会被忽略 |
| `num_ctx` | number | 否 | 默认配置 `num_ctx` |

**响应 200**（成功）

```json
{"ok": true, "summary": "……（第三人称、300～500 字的前情摘要）", "chars": 412, "model": "qwensese-27b"}
```

**响应 200**（业务失败，注意仍是 200）

```json
{"ok": false, "error": "这段对话还是空的", "summary": ""}
```

**错误**：`400`（`history` 不是数组）、`403`。

### 17. `POST /api/suggest`

"帮我想"：让模型结合最近对话与当前设定，给出 3～5 条用户下一步可以说的台词/动作。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `history` | array | 是 | 最近对话（服务端只取最后 10 条） |
| `persona_id` | string | 否 | 角色卡文件名，用于给建议提供设定参考 |
| `n` | number | 否 | 建议条数，夹在 3～5，默认 4 |
| `model` | string | 否 | 必须是 Ollama 已安装模型名（否则忽略，用配置模型） |
| `num_ctx` | number | 否 | 默认配置 `num_ctx` |
| `summary` | string | 否 | 前情摘要，会拼进提示词 |
| `debug` | boolean | 否 | `true` 时响应里附带 `raw` 原文（前 800 字） |

**响应 200**

```json
{"ok": true, "lines": ["*我把门带上，在柜台边坐下。* \"外面风挺大的。\"", "「你平时都这么晚关门吗？」", "……"], "model": "qwensese-27b"}
```

失败时也是 200：

```json
{"ok": false, "error": "HTTP Error 404: Not Found", "lines": []}
```

**错误**：`400`（`history` 不是数组）、`403`。

---

## 生成与导入

> 这一组接口都需要 Ollama 在线；它们不会直接写盘：生成结果只返回草稿，由前端预览确认后再调用 `/api/persona/save` 或 `/api/me` 落盘。

### 18. `POST /api/card/gen`

把一段口语描述/关键词整理成角色卡字段草稿（`target: persona`），或整理成"我"的卡草稿（`target: me`）。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `text` | string | 是 | 描述文本，至少 6 个字符 |
| `target` | string | 否 | `persona`（默认）或 `me` |
| `level` | number | 否 | 生成结果里 `尺度` 字段的兜底值（默认 2） |
| `model` | string | 否 | 必须是已安装模型，否则忽略 |
| `num_ctx` | number | 否 | 默认 12288 |

**响应 200**（成功）

```json
{
  "ok": true,
  "target": "persona",
  "fields": {
    "名字": "林晚",
    "年龄": "29",
    "身份": "旧书店店主",
    "与用户的关系": "熟客",
    "前情提要": "",
    "外貌": "……",
    "性格": "……",
    "说话风格": "……",
    "行为准则": "……",
    "尺度": "2",
    "禁忌": "……",
    "开场白": "……",
    "示例对话": "林晚：打烊了。"
  },
  "chars": 486
}
```

**响应 200**（失败，注意仍是 200）

```json
{"ok": false, "error": "描述太短了，至少写几个关键词或一两句话", "fields": {}}
```

**错误**：`400`（`text` 类型不对等参数问题）、`403`。

### 19. `POST /api/import`

把一段现成的角色扮演对话导入成角色卡草稿。响应是 **NDJSON 进度流**（每行一个 JSON 事件），前端据此显示进度；
`use_model=false` 时只做"只用本地统计"的快速（粗糙）提取。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `text` | string | 是 | 对话原文，至少 40 个字符 |
| `narrator` | string | 否 | 指定谁是"用户/我"那一侧（对话里的名字） |
| `use_model` | boolean | 否 | 默认 `true`；`false` 时不调用模型 |
| `model` | string | 否 | 默认配置模型 |

```json
{"text": "林晚：打烊了。\n我：今天不买书，就是想坐一会儿。\n林晚：坐吧。", "narrator": "我", "use_model": true}
```

**响应 200**：`Content-Type: application/x-ndjson; charset=utf-8`

事件流（按实际顺序出现）：

```text
{"stage":"parse","speakers":["林晚","我"],"role":"林晚","user":"我","lines":12}
{"stage":"direct","chars":1530}
{"stage":"synth"}
{"stage":"done","draft":{"名字":"林晚","年龄":"29","尺度":"2","……":"……"},"stats":{"lines":12,"role_chars":860,"user_chars":240,"……":"……"},"warnings":[]}
```

| `stage` | 含义 |
| --- | --- |
| `parse` | 已解析出说话人；字段：`speakers`、`role`、`user`、`lines` |
| `raw` | 没认出"名字：内容"格式，改为整段文本交给模型；字段：`chars` |
| `direct` | 文本较短，一次直接分析；字段：`chars` |
| `chunk` | 长文本分块摘要中；字段：`i`、`n` |
| `reduce` | 合并各块摘要；字段：`chunks` |
| `synth` | 正在合成最终角色卡（最慢的一步） |
| `done` | 完成；字段：`draft`（字段字典）、`stats`（统计）、`warnings`（提示数组） |
| `error` | 失败；字段：`error` |

> 注意：即使 `use_model=false`，当前实现仍会先检查/尝试拉起 Ollama，Ollama 不可用时返回 `503`。

**错误**

| 状态码 | 情况 |
| --- | --- |
| 400 | `text` 少于 40 字符（`{"error":"文本太短了（至少几十个字）"}`） |
| 403 | 口令未通过 |
| 503 | Ollama 不可用（`{"error":"Ollama 没起来。..."}`） |

### 20. `POST /api/me/import`

从一段对话里提取"我"（用户/代入者）本身的设定，返回字段草稿。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `text` | string | 是 | 对话原文，至少 40 个字符 |
| `model` | string | 否 | 默认配置模型 |
| `num_ctx` | number | 否 | 默认配置 `num_ctx` |

**响应 200**（成功）

```json
{
  "ok": true,
  "fields": {
    "名字": "阿雪",
    "称呼": "阿雪",
    "性别": "男",
    "年龄": "19",
    "种族": "雪狐族",
    "身份": "雪狐族王室出身",
    "外貌": "……",
    "性格": "……",
    "说话风格": "……",
    "喜好": "……"
  },
  "chars": 11
}
```

**响应 400**（文本太短，注意这个错误带 `status` 字段）

```json
{"ok": false, "error": "文本太短了，至少贴几十个字", "fields": {}, "status": 400}
```

其它失败（模型输出不是 JSON、调用失败）为 200 + `ok: false`。

**错误**：`400`（过短，如上）、`403`。

---

## Ollama 控制

### 21. `POST /api/ollama/start`

尝试拉起 Ollama：若 Ollama 已可达直接返回；否则如果项目里有 `ollama/ollama.exe`，会以本机回环地址后台启动它，
并设置一组省显存的 `OLLAMA_*` 环境变量（见 [configuration.md](configuration.md)），最多等待约 90 秒。

**请求体**：`{}`（不需要字段）

**响应 200**

```json
{"ok": true, "ollama_ok": true, "models": [ /* 同 /api/models 的 models */ ]}
```

| 字段 | 说明 |
| --- | --- |
| `ok` | 是否成功拉起/已在运行 |
| `ollama_ok` | 拉起后再次探活的结果 |
| `models` | 当前模型列表 |

`ok: false` 表示没有找到可执行文件或启动失败（仍是 HTTP 200）。

**错误**：`403`。

---

## OpenAI 兼容层

给 Chatbox 等支持 OpenAI API 的客户端使用。**虚拟模型名**可以切换角色与尺度。

### 虚拟模型名规则

| 模型名 | 含义 |
| --- | --- |
| `qwensese-27b` | 默认角色卡 `personas/persona.md` + 卡里写的尺度 |
| `qwensese/<角色>/<0-3>` | 指定角色卡 + 指定尺度；`角色` 可以是角色卡文件名（`persona.md`）、文件名去后缀（`persona`）、卡里的 `名字`（`林晚`），或 `c<序号>`（第几张卡的纯 ASCII 写法） |
| `qwensese/c2/1` | 第 2 张角色卡（按 `/api/state` 的 `personas` 顺序，从 0 开始）+ 尺度 1 |

解析规则：先按 `文件 stem`、`c<序号>`、`名字`、`文件名` 精确匹配，再做一次"名字包含"模糊匹配；
都匹配不到时退回默认卡 `persona.md`。

### 22. `GET /v1/models`（别名 `GET /models`）

**响应 200**

```json
{
  "object": "list",
  "data": [
    {"id": "qwensese-27b", "object": "model", "created": 1700000000, "owned_by": "qwensese"},
    {"id": "qwensese/persona/0", "object": "model", "created": 1700000000, "owned_by": "qwensese"},
    {"id": "qwensese/persona/1", "object": "model", "created": 1700000000, "owned_by": "qwensese"},
    {"id": "qwensese/c0/0", "object": "model", "created": 1700000000, "owned_by": "qwensese"}
  ]
}
```

每个角色卡会展开成 4 个尺度（0～3），并同时给出"文件名 stem"与"c<序号>"两种写法。

**错误**：仅 `403`。

### 23. `POST /v1/chat/completions`（别名 `POST /chat/completions`）

OpenAI Chat Completions 兼容接口。服务端用请求里的 `model` 解析出角色卡与尺度，再用同一套 SYSTEM 拼装逻辑
（客户端自带的 `system` 消息不会覆盖角色卡，而是作为"本轮额外指令"降级处理）。

**请求体**

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `messages` | array | 是 | — | OpenAI 格式消息；`content` 可为字符串或分段数组；`user`/`assistant` 进历史，`system` 进额外指令 |
| `model` | string | 否 | `qwensese-27b` | 见上方虚拟模型名规则 |
| `stream` | boolean | 否 | `false` | `true` 时返回 SSE 流 |
| `max_tokens` 或 `num_predict` | number | 否 | `1024` | 单轮最大输出 token |
| `temperature` | number | 否 | `0.8` | |
| `top_p` | number | 否 | `0.92` | |
| `repeat_penalty` | number | 否 | `1.05` | |
| `num_ctx` | number | 否 | 配置 `num_ctx` | |

```json
{
  "model": "qwensese/林晚/2",
  "stream": false,
  "messages": [
    {"role": "system", "content": "你是一个乐于助人的助手。"},
    {"role": "user", "content": "今天不买书，就是想坐一会儿。"}
  ]
}
```

**非流式响应 200**

```json
{
  "id": "chatcmpl-qw",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "qwensese/林晚/2",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "*她把柜台上的书挪开，腾出一块地方。* \"坐吧。\""},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 4096, "completion_tokens": 312, "total_tokens": 4408}
}
```

`usage` 的数值来自 Ollama 的 `prompt_eval_count` / `eval_count`。

**流式响应 200**：`Content-Type: text/event-stream; charset=utf-8`

```text
data: {"id":"chatcmpl-qw","object":"chat.completion.chunk","created":1700000000,"model":"qwensese/林晚/2","choices":[{"index":0,"delta":{"content":"*她把柜台"},"finish_reason":null}]}

data: {"id":"chatcmpl-qw","object":"chat.completion.chunk","created":1700000000,"model":"qwensese/林晚/2","choices":[{"index":0,"delta":{"content":"上的书挪开"},"finish_reason":null}]}

data: {"id":"chatcmpl-qw","object":"chat.completion.chunk","created":1700000000,"model":"qwensese/林晚/2","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]

```

服务端不使用 `Connection: keep-alive`：流结束后直接关闭连接，客户端读到 EOF 结束。

**错误**

| 状态码 | 情况 |
| --- | --- |
| 400 | 请求体非法 JSON / 非对象 |
| 403 | 口令未通过 |
| 500 | Ollama 调用失败 |
| 503 | Ollama 不可用：`{"error": {"message": "Ollama 没起来"}}`（注意这里是嵌套结构） |

---

## Ollama 上游接口（非本服务提供）

下面两个路径是 **Ollama 自己**的接口，本服务只用它们做探活/列模型，**不代理给浏览器**。
需要时请直接访问 Ollama 的默认地址 `http://127.0.0.1:11434`。

### `GET /api/tags`（上游）

列出 Ollama 已安装的模型。

```json
{
  "models": [
    {
      "name": "qwensese-27b:latest",
      "model": "qwensese-27b:latest",
      "modified_at": "2026-11-21T09:00:00Z",
      "size": 14393200000,
      "digest": "sha256:…",
      "details": {"family": "qwen3", "parameter_size": "27B", "quantization_level": "IQ4_XS"}
    }
  ]
}
```

本服务的等价接口：`GET /api/models`（额外带中文标签、`ctx_choices`、`ollama_ok`）。

### `GET /api/version`（上游）

Ollama 版本探活。

```json
{"version": "0.12.0"}
```

本服务的等价接口：`GET /api/health` 的 `ollama` 字段，或 `GET /api/models` 的 `ollama_ok`。

---

## 调用示例

Windows 10/11 自带 `curl.exe`；也可以直接用 `python -c` 或任意 HTTP 客户端。

```powershell
# 探活
curl.exe http://127.0.0.1:8717/api/health

# 读取角色卡
curl.exe "http://127.0.0.1:8717/api/persona?id=persona.md"

# 发一条对话（流式，逐行打印）
curl.exe -N -X POST http://127.0.0.1:8717/api/chat `
  -H "Content-Type: application/json" `
  -d '{"persona_id":"persona.md","level":2,"messages":[{"role":"user","content":"今天不买书，就是想坐一会儿。"}]}'

# 带访问口令时（任选一种）
curl.exe -H "X-Access-Key: <你的口令>" http://127.0.0.1:8717/api/state
curl.exe "http://127.0.0.1:8717/api/state?k=<你的口令>"
```

Python 标准库：

```python
import json
import urllib.request

req = urllib.request.Request(
    "http://127.0.0.1:8717/api/persona?id=persona.md",
    headers={"X-Access-Key": "<你的口令>"},
)
print(json.loads(urllib.request.urlopen(req, timeout=10).read())["fields"]["名字"])
```
