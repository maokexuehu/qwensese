# 配置参考

本文是 qwensese 的正式配置参考，覆盖：

- `web/config.json`（服务配置）
- 命令行参数
- `config/` 目录（模式、模板、我的角色卡、当前指针）
- `prompts/` 与 `personas/` 的生效方式
- Ollama 相关环境变量
- 采样参数与上下文预算

所有路径均为仓库内相对路径。示例角色只使用 `personas/persona.md`（林晚）与 `personas/example-mentor.md`（周屿）。

## 1. 服务配置 `web/config.json`

首次启动时，启动脚本/服务会以 `web/config.example.json` 为模板生成 `web/config.json`。模板内容：

```json
{
  "model": "qwensese-27b",
  "ollama": "http://127.0.0.1:11434",
  "num_ctx": 16384,
  "host": "127.0.0.1",
  "port": 8717,
  "access_key": ""
}
```

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `model` | string | `qwensese-27b` | 默认 Ollama 模型名。必须是 `ollama list` 里的名字；也可以是 `qwensese-14b` |
| `ollama` | string | `http://127.0.0.1:11434` | Ollama 服务地址；探活、模型列表、对话都走它 |
| `num_ctx` | number | `16384` | 默认上下文长度。请求体未带 `num_ctx` 时使用；界面里可以再切 8192/12288/16384 等 |
| `host` | string | `127.0.0.1` | 监听地址。除 `127.0.0.1`/`localhost` 外的值一律按 `0.0.0.0` 处理 |
| `port` | number | `8717` | 页面端口 |
| `access_key` | string | `""` | 访问口令；空字符串 = 不校验。设置后所有页面与 API 都要带口令 |
| `lan` | boolean | 不写 | 可选；`true` 等价于命令行 `--lan`（绑定 `0.0.0.0`） |

注意事项：

- `web/config.json` 可能包含口令，已被 `.gitignore` 忽略；**不要提交**。
- 修改 `web/config.json` 后需要**重启服务**才生效。
- 配置文件解析失败（JSON 写坏）时，服务会静默回退到默认值，不会崩溃；此时请修复 JSON。

### 生成一个随机口令

```powershell
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

把输出填进 `web/config.json` 的 `access_key`，重启服务即可。口令可放在 `?k=`、cookie `qk` 或请求头 `X-Access-Key` 中（详见 [API.md](API.md) 的「鉴权」一节）。

## 2. 命令行参数

```powershell
python web/app.py [--lan] [--port <端口>] [--host <地址>]
```

| 参数 | 说明 |
| --- | --- |
| `--lan` | 绑定 `0.0.0.0`，对局域网开放（优先级最高） |
| `--port N` | 覆盖配置里的 `port` |
| `--host H` | 覆盖配置里的 `host`；非 `127.0.0.1`/`localhost` 的值按 `0.0.0.0` 处理 |

`scripts/start.ps1 -Lan` 最终就是给 `web/app.py` 传 `--lan`。

## 3. `config/` 目录

### 3.1 `config/mode.txt`

记录"思考模式"偏好，当前内容为 `nothink`。

- 说明：这是早期命令行 / 手工 Modelfile 流程留下的偏好记录（`thinking` / `nothink`）。
- 当前 **Web 服务不读取它**：每轮请求都在代码里固定 `think: false` 并做思考链过滤，所以调整它不会改变页面行为。
- 保留给"手工写 Modelfile、直接命令行聊"的场景参考；若要与 `config/template-*.txt` 搭配使用，请自行在 Modelfile 中引用对应的 TEMPLATE。

### 3.2 `config/template-thinking.txt` / `config/template-nothink.txt`

两份 Ollama `TEMPLATE` 片段（ChatML 风格），用于手工创建模型时指定聊天模板：

| 文件 | 作用 |
| --- | --- |
| `config/template-thinking.txt` | 标准 ChatML 模板，结尾 `{{ .System }}`/消息循环后直接接 `<|im_start|>assistant` |
| `config/template-nothink.txt` | 同上，但结尾预填了一个空的 ` thinking\n\n</think>` 块，让模型从"思考已结束"的位置直接写正文 |

- 这两份文件**不会被 `web/app.py` 读取**；它们服务于"自己写 Modelfile、用 `ollama create` 建模型"的手工流程。
- 也注意：`scripts/import-model.ps1` 的默认流程生成的是最小 Modelfile（`FROM` + `SYSTEM` + 参数），不依赖这两个模板；如果你要定制 TEMPLATE，可参考它们的内容。

### 3.3 `config/user_personas/*.md`

"**我**"的角色卡：描述"用户/代入者"这个人本身，供 SYSTEM 在"用户（我）的设定"一节使用。可以有任意多张，页面里随时切换。

字段（`ME_FIELDS`，按顺序写入）：

| 字段 | 写什么 |
| --- | --- |
| `名字` | 角色的名字/自称 |
| `称呼` | AI 该怎么称呼"我" |
| `性别` | 男 / 女 / 其他 / 种族设定 |
| `年龄` | 必须 ≥ 18（虚构角色） |
| `种族` | 人类 / 雪狐族 / …… |
| `身份` | 是什么人（不写处境） |
| `外貌` | 具体特征 |
| `性格` | 会怎么反应，而不是形容词标签 |
| `说话风格` | 称呼、句子长度、口头禅 |
| `喜好` | 喜欢/讨厌什么 |
| `其他` | 任何补充设定 |

仓库自带 `config/user_personas/default.md`（阿雪）。页面里"我的角色卡"可新建/编辑/删除；删除当前唯一一张会被拒绝。
额外的自定义 `## 字段名` 也会被保留并使用。

### 3.4 `config/current_user_persona.txt`

记录当前使用哪一张"我"的角色卡（内容就是文件名，如 `default.md`）。

- 这是**运行时状态文件**，已被 `.gitignore` 忽略。
- 服务启动/切换时会自动写入或修正它（指向不存在文件时会回退到第一张）。
- 不要手工编辑它来"切换"——用页面切换即可。

## 4. `prompts/` 与 `personas/` 的生效方式

| 修改对象 | 生效方式 |
| --- | --- |
| `personas/*.md`（角色卡） | 页面保存后**下一条消息即生效**（SYSTEM 每轮现场拼）；用文件编辑器改也一样 |
| `config/user_personas/*.md`（我的角色卡） | 下一条消息生效 |
| `prompts/system-core.md`（系统提示词） | 下一条消息生效 |
| `prompts/style-guide.md`（文风规则） | 下一条消息生效（界面里关掉 `use_style` 则不拼） |
| `web/config.json` | 需要**重启服务** |
| `config/mode.txt`、`config/template-*.txt` | 对 Web 服务**不生效**；只影响手工 Modelfile 流程 |
| 采样参数（温度/top_p/复读惩罚） | 页面即时保存，下一轮请求生效 |

如果想把这些设定**固化进 Ollama 模型本身**（供命令行或第三方前端直连使用），改完 `personas/persona.md` 与
`prompts/*.md` 后重跑：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/import-model.ps1
```

## 5. Ollama 相关环境变量

`scripts/start.ps1` 在需要自行拉起 Ollama 时会设置以下变量；`web/app.py` 的 `ensure_ollama()` 自动拉起项目内
`ollama/ollama.exe` 时也设置同一组值：

| 变量 | 值 | 作用 |
| --- | --- | --- |
| `OLLAMA_MODELS` | `<项目根>/models` | 模型目录放在项目内，不落系统盘 |
| `OLLAMA_HOST` | `127.0.0.1:11434` | 只绑本机回环地址 |
| `OLLAMA_KEEP_ALIVE` | `30m` | 模型常驻 30 分钟，避免反复加载（加载一次约 20～40 秒） |
| `OLLAMA_FLASH_ATTENTION` | `1` | 开 flash attention，省显存、提速 |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | KV cache 量化，进一步省显存 |
| `OLLAMA_NUM_PARALLEL` | `1` | 单并发，把显存都给一个请求 |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | 不预载多个模型 |

另外启动脚本会为 Python 设置 `PYTHONIOENCODING=utf-8` 与 `PYTHONUTF8=1`，保证中文日志输出正常；
`web/app.py` 启动时也会把 stdout/stderr 重设为 UTF-8。

若你想自己控制这些变量（例如已有系统级 Ollama 服务），可以不使用 `scripts/start.ps1`，直接启动 Ollama 后
`python web/app.py`。

## 6. 采样参数与回复长度

服务端对 `/api/chat` 的默认值（请求体可覆盖，界面可调）：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `temperature` | `0.8` | 随机性；角色扮演不建议低于 0.5（会明显变保守） |
| `top_p` | `0.92` | nucleus 采样 |
| `top_k` | `40` | 候选数（服务端固定，不开放） |
| `repeat_penalty` | `1.05` | 抗复读；复读严重可到 1.08 |
| `num_predict` | `1024` | 单轮最大输出 token（请求缺省值） |
| `think` | `false` | 关闭思考链（固定） |
| `keep_alive` | `30m` | 模型常驻时间（固定） |

界面上的可调范围：

| 控件 | 范围 | 默认 |
| --- | --- | --- |
| 温度 | `0.1 ～ 1.3`（步进 0.05） | `0.80` |
| top_p | `0.6 ～ 1.0`（步进 0.01） | `0.92` |
| 复读惩罚 | `1.0 ～ 1.2`（步进 0.01） | `1.05` |
| 尺度 | `0 ～ 3` | 取角色卡 `尺度` |

回复长度预设（映射为 `num_predict`；"超长"另带最少字数）：

| 预设 | `num_predict` | 说明 |
| --- | --- | --- |
| 短 | 350 | 一两段，只推进一个动作 |
| 中 | 700 | 常规长度 |
| 长 | 1400 | 细节多、铺得开 |
| 超长 | 2400 | 至少 600 汉字，不够会自动续写（最多 3 次） |

## 7. 上下文长度、预算与显存

### 内置模型的推荐上下文

| 模型 | 推荐 `num_ctx` | 可选档位 | 说明 |
| --- | --- | --- | --- |
| `qwensese-27b`（27B） | `12288` | 8192 / 12288 / 16384 | 超过 12k 后显存吃紧，速度会明显下降（界面会提示） |
| `qwensese-14b`（14B） | `32768` | 16384 / 24576 / 32768 | 参数更少，可以开更长上下文 |

`web/config.json` 的 `num_ctx` 是请求缺省值（模板里为 `16384`）；界面切上下文会按所选模型档位生效。

### 预算裁剪规则

- 估算：中文 `1.25 字符 ≈ 1 token`。
- 请求预算：`num_ctx - num_predict - max(64, 3% × num_ctx)`。
- 超预算时从**最早的历史**开始丢弃（服务端至少保留 4 条，前端发送前至少保留 5 条），并给出 `{"note": ...}` 提示。
- 角色卡本身超预算时，会提示换更长上下文模型或删短卡内长字段。
- 更省上下文的办法：用"把前面的对话压成摘要"（`/api/compress`），摘要之后作为 `summary` 传入，不必逐条重发旧历史。

### 量化档位与体积参考

| 量化 | 体积（约） | 备注 |
| --- | --- | --- |
| `UD-IQ4_XS` | 13.4 GB | 默认推荐，16 GB 显存可基本全放 |
| `UD-Q3_K_XL` | 12.41 GB | 更省显存，质量略降 |
| `UD-DW-Q4_K_M` | 15.41 GB | 更高质量，可能需要部分卸载到内存 |
| `UD-Q4_K_XL` | 更大 | 下载脚本支持，按显存选择 |
| `UD-Q5_K_XL` | 更大 | 下载脚本支持，按显存选择 |

判断是否溢出显存：启动后看 `ollama ps` 的 `SIZE` 与 `PROCESSOR`；`PROCESSOR` 出现 `xx%/yy% CPU/GPU`
说明有层放到了内存，速度会掉到 1/2～1/3。想提速可以调小 `num_ctx`、换更小量化，或降低 `num_predict`。

## 8. 端口、局域网与安全配置

- 默认 `host: 127.0.0.1` + `port: 8717`：只有本机能访问。
- 局域网：`scripts/start.ps1 -Lan`（或 `python web/app.py --lan`，或 `"lan": true`），绑定 `0.0.0.0`。
- 开放局域网/公网前**必须**设置 `access_key`，公网还需要 HTTPS 反向代理与防火墙限制。
- Ollama 的 `11434` 端口不要暴露到公网；本项目默认只把它当作本机上游。
- 详细威胁模型见 [SECURITY.md](../SECURITY.md)。

## 9. 配置文件速查

| 你想要的 | 改哪里 |
| --- | --- |
| 换模型 | `web/config.json` 的 `model`（或界面顶栏模型下拉） |
| 换端口 | `web/config.json` 的 `port`（或 `--port`） |
| 手机能访问 | `scripts/start.ps1 -Lan` / `"lan": true` / `--lan` |
| 加访问口令 | `web/config.json` 的 `access_key` |
| 改角色人设 | `personas/*.md`（页面编辑或直接改文件） |
| 改"我"的设定 | `config/user_personas/*.md` 或页面"我的角色卡" |
| 改系统提示词 | `prompts/system-core.md` |
| 改文风规则 | `prompts/style-guide.md`（或界面关掉文风） |
| 改默认采样 | 界面设置抽屉（每次请求生效） |
| 改推荐上下文 | 内置预设写在 `web/app.py` 的 `MODEL_INFO`；界面可直接改所选模型上下文 |
| 模型放哪 | `OLLAMA_MODELS`（启动脚本自动设为 `<项目根>/models`） |
| 看日志 | `logs/`（启动脚本写 Ollama 日志；页面请求日志在服务窗口/终端） |
