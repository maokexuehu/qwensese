# 安全策略

## 支持版本

| 版本 | 支持状态 |
| --- | --- |
| 0.2.x（最新） | 接受安全报告 |
| 0.1.x | 仅关键问题，建议升级 |

## 如何报告漏洞

请**不要**用公开 issue 披露安全问题。优先使用 GitHub 的私有漏洞报告通道：

1. 打开仓库页面 → **Security** → **Report a vulnerability**（Private vulnerability reporting）。
2. 在报告里写清：受影响的版本/提交、复现步骤、影响范围、你建议的修复方向。
3. 如果仓库暂时没有开启该通道，请先开一个**不含利用细节**的 issue，说明"有安全问题需要私下联系"，维护者会提供后续渠道。

我们会尽量在 7 天内确认收到，并在修复发布后致谢（如你希望匿名请注明）。

完整的威胁矩阵与残余风险见 [docs/security-model.md](docs/security-model.md)。

## 威胁模型

### 部署形态与信任边界

- 本项目是**单用户本地工具**：一个 Python 进程同时提供页面与 API，对话历史存在浏览器 `localStorage` 与 `saves/` 目录，模型由本机 Ollama 进程提供。
- 默认 `host: 127.0.0.1`，只监听回环地址；页面、API、静态文件都只对本机进程开放。
- 服务**没有** TLS、没有用户体系、没有速率限制、没有多租户隔离；**不是沙箱**，以你的用户权限读写项目目录。
- Ollama 默认只监听 `127.0.0.1:11434` 且自身无鉴权。

### `access_key` 访问口令

- 在 `web/config.json` 设置 `access_key` 后，所有页面与 API 都会校验口令；留空则不校验（默认，适合纯本机）。
- 口令可从三处提供（优先级从高到低）：cookie `qk` → 请求头 `X-Access-Key` → 查询参数 `?k=`。
- 比较使用 `hmac.compare_digest`（常量时间）。
- 通过 `?k=` 成功认证后下发 cookie：`qk=<口令>; Path=/; Max-Age=2592000; SameSite=Strict; HttpOnly`。
- 风险与注意事项：
  - `?k=` 会出现在浏览器历史、代理/服务器访问日志里，尽量只在首次进入时使用，或改用 `X-Access-Key`。
  - `access_key` 是唯一门禁；泄露即等于把全部角色卡、存档与模型控制权交给对方。
  - 没有锁定/限速，理论上可被在线暴力枚举；请使用足够长的随机口令（例如 24 字节以上）。
  - 口令以明文保存在 `web/config.json`（该文件已被 `.gitignore` 忽略），请勿提交或分享。

### 请求级防护（0.2.0 起）

| 防护 | 实现 | 挡住的攻击 |
| --- | --- | --- |
| Host 白名单 | `Host` 必须是 `127.0.0.1` / `localhost` / 配置 host / 当前局域网 IP，否则 403 | DNS rebinding |
| Origin/Referer 校验 | POST 的 `Origin`/`Referer` 必须是允许的 host；跨站 403。无 Origin 的 curl/SDK 放行 | 恶意网页跨站删卡/删存档/烧 GPU |
| JSON Content-Type | 写接口只收 `application/json`，否则 415 | 表单类免预检 CSRF |
| 安全响应头 | CSP `default-src 'self'`、`nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、COOP/CORP、Permissions-Policy | 点击劫持、MIME 嗅探、Referer 泄密 |
| Cookie | `SameSite=Strict; HttpOnly` | 跨站携带 cookie、JS 读取口令 |
| 写盘串行化 | 所有读-改-写共用 `WRITE_LOCK` | 并发丢更新 |
| `--lan` 强制口令 | 监听 `0.0.0.0` 且 `access_key` 为空时拒绝启动（除非 `--insecure-lan`） | 无口令暴露局域网 |

这些校验可以在 `web/config.json` 中用 `host_check` / `origin_check` / `require_json` 关闭（不建议）。

### 局域网 / 公网暴露

- `--lan` 或 `host: 0.0.0.0` 会让同一网络内的设备访问页面与 API；`access_key` 为空时**拒绝启动**（除非显式 `--insecure-lan`）。
- 暴露到公网时至少需要：`access_key` + HTTPS（反向代理终止 TLS）+ 防火墙/来源 IP 限制；不要直接端口转发裸 HTTP 服务。
- cookie 为 `SameSite=Strict`，能缓解跨站携带，但**不能**替代口令与网络隔离。

### 写盘与删除类接口

以下接口会直接读写项目目录中的文件，口令泄露时具有破坏力：

| 接口 | 影响 |
| --- | --- |
| `POST /api/persona/save` | 覆盖/新建 `personas/*.md`（只允许该目录下的 `.md`，文件名为 basename） |
| `POST /api/persona/delete` | 删除 `personas/*.md` |
| `POST /api/me`、`POST /api/me/delete` | 读写/删除 `config/user_personas/*.md`（删除当前且仅剩一张时会被拒绝） |
| `POST /api/saves`、`POST /api/saves/delete` | 写入/删除 `saves/*.json`（存档 id 只允许 `[A-Za-z0-9_-]`） |
| `POST /api/ollama/start` | 以后台进程方式启动项目内 Ollama 可执行文件 |

路径处理：角色卡文件名取 `basename` 且强制 `.md` 后缀；存档 id 过滤为 `[A-Za-z0-9_-]`；写入采用"临时文件 + 原子替换"，
读-改-写过程加锁。这些是针对**目录穿越/并发覆盖**的防护，不是防**已认证用户**的破坏行为。

### 审计日志

- 默认不记录访问日志。`access_log: true` 时，每个响应写一行到 `logs/access.log`（时间、客户端 IP、方法、路径、状态码），可用于追查"谁改了我的卡"。
- 日志本身可能暴露使用习惯与局域网 IP；不需要时保持关闭，并定期清理。

### 模型与提示词

- 本项目刻意提供可自定义角色与题材的系统提示层；提示词注入、越狱、角色崩坏属于**设计用途**，不视为安全漏洞。
- 默认底座是**权重层面消融拒答方向**的 abliterated 模型，服务端没有内容分类器、输出过滤或年龄验证；前端的 18+ 确认只是本地提示。
- 请勿把本项目用于：未成年人、真实可识别个人、真实伤害、违法内容；`prompts/system-core.md` 里保留了最低限度的硬性约束，但那是**软约束**。
- 从外部来源导入的角色卡/提示词/对话文本可能包含恶意指令；这些指令只会影响模型输出，不会在本机执行代码，但请自行审查你把什么投喂给模型。
- 生成内容是否合法、是否符合你所在司法辖区与平台规则，由使用者自行负责。

### 供应链

- 后端只用 Python 标准库，`requirements.txt` 不包含任何第三方包；前端没有 npm 依赖、没有构建步骤。
- 唯一的外部依赖是 Ollama 可执行文件与 GGUF 模型权重；请从官方/可信来源获取。
- `scripts/download_model.py` 下载后会计算 SHA256 并与 HuggingFace 元数据里的 LFS sha256 比对，不匹配则改名 `.bad` 并失败；
  也可以 `--sha256 <64位hex>` 指定站外公布的校验值。拿不到元数据时打印警告并记录实际值。
- 残余风险：首次信任的元数据来自同一仓库；若上游被完全接管并同时替换文件与元数据，自动校验无法发现。

## 加固建议

1. 保持默认 `host: 127.0.0.1`；只在需要手机访问时临时开启 `--lan`，用完关掉，并设置长随机 `access_key`。
2. 任何时候对外暴露都设置长随机 `access_key`，并通过 HTTPS 反向代理访问。
3. 不要提交或分享 `web/config.json`、`saves/`、`logs/`；定期备份 `personas/` 与 `config/user_personas/`。
4. 不要把 Ollama 的 `11434` 端口暴露到公网。
5. 保留下载后的 `.sha256` 文件；高风险场景用站外校验值复核。
6. 需要审计时再打开 `access_log`，并定期清理日志。
