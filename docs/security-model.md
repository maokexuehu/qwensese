# 安全模型与威胁矩阵

本文说明 qwensese **实际**提供了哪些防护、哪些只是软约束，以及残余风险。它不是合规承诺，也不构成
对任何使用场景的合法性背书。

## 定位：单用户本地工具

- 一个 Python 进程同时提供页面与 JSON API；对话历史在浏览器 `localStorage` 和 `saves/`，
  模型由本机 Ollama 提供。
- 默认 `host: 127.0.0.1`，只监听回环地址；不提供 TLS、用户体系、配额或速率限制。
- **不是沙箱**：服务以你的用户权限运行，能读写项目目录（角色卡、存档、配置、日志）。
- 设计给"一台电脑、一个用户"用；多用户同时使用不在设计范围内。

## 请求级防护（0.2.0 起）

| 防护 | 行为 | 挡住的典型攻击 |
| --- | --- | --- |
| **Host 校验** | `Host` 头必须是 `127.0.0.1` / `localhost` / 配置里的 host / 当前局域网 IP，否则 403 | DNS rebinding：恶意网页把自己的域名解析到 127.0.0.1 后读取你的页面与 API |
| **Origin / Referer 校验** | POST 携带的 `Origin`/`Referer` 必须是允许的 host；跨站直接 403。无 Origin 的非浏览器客户端（curl / SDK）放行 | 任意网页用 `fetch`/表单跨站向 `127.0.0.1:8717` 发写请求（删角色卡、删存档、烧 GPU） |
| **Content-Type 校验** | 写接口只接受 `application/json`，其它返回 415 | 简单表单 / `text/plain` 的"免预检"跨站 POST |
| **安全响应头** | `CSP default-src 'self'`、`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、`Permissions-Policy`、COOP/CORP | 点击劫持、MIME 嗅探、外链 Referer 泄露口令、第三方脚本注入 |
| **Cookie 收紧** | 口令 cookie `SameSite=Strict; HttpOnly` | 跨站请求携带 cookie 的 CSRF；JS 读取口令 |
| **写盘串行化** | 所有角色卡/存档/"我"的读-改-写共用一把可重入锁 | 并发请求同时改同一张卡导致的**丢更新** |
| **`--lan` 口令强制** | 监听 `0.0.0.0` 且 `access_key` 为空时拒绝启动（除非显式 `--insecure-lan` / `allow_insecure_lan: true`） | 误把无口令的写接口暴露到整个局域网 |

可在 `web/config.json` 中关闭这些校验（`host_check` / `origin_check` / `require_json`），
**不建议**；关闭后上述攻击面会回来。

## 访问口令（`access_key`）

- 唯一门禁。三处携带（优先级从高到低）：cookie `qk` → 请求头 `X-Access-Key` → 查询参数 `?k=`。
- 常量时间比较（`hmac.compare_digest`）；留空即完全放行（默认如此，适合纯本机）。
- 风险：没有锁定/限速，可被在线暴力枚举，请用 ≥24 字节随机串；口令明文存放在
  `web/config.json`（已被 `.gitignore` 忽略）；`?k=` 会进入浏览器历史与访问日志，首次进入后尽量改用 cookie。

## 敏感数据落盘

| 位置 | 内容 | 风险 |
| --- | --- | --- |
| `personas/*.md`、`config/user_personas/*.md` | 人设、尺度、禁忌、前情 | 明文；分享/提交前请自行审查 |
| `saves/*.json` | 完整对话历史 | 明文；可能包含成人向内容与个人信息 |
| `web/config.json` | 访问口令、模型名 | 明文；不要提交或截图外发 |
| `logs/access.log` | 写请求的时间/IP/路径（仅在 `access_log: true` 时） | 可用于审计，也可能暴露你的使用习惯 |
| 浏览器 `localStorage` | 当前角色、历史缓存、18+ 确认标记 | 同一浏览器 profile 内可见 |

默认不记录任何访问日志；需要追查"谁改了我的卡"时再打开 `access_log`。

## 供应链：模型权重与下载校验

- 运行时零第三方依赖：后端只用 Python 标准库，前端无 npm 依赖。唯一外部二进制是 Ollama。
- GGUF 来自 HuggingFace 第三方仓库。`scripts/download_model.py` 会：断点续传 → 计算 SHA256 →
  与 HuggingFace 元数据里的 LFS sha256 比对；不匹配则改名为 `.bad` 并失败退出。
  拿不到元数据时打印警告并把实际值写到 `<文件>.sha256`。
- 残余风险：**第一次**从某个仓库下载时，元数据本身就来自该仓库；如果上游仓库被完全接管并替换了
  文件与元数据，校验无法证明"作者意图"。需要更强保证时请用发布方在站外公布的校验值
  （`--sha256`），或改用官方仓库。
- 旧 PowerShell 下载脚本（curl）没有校验，仅保留兼容；新流程请用 Python 下载器。

## 模型层面：没有内容护栏

- 默认底座是权重层面消融拒答方向的衍生模型（`huihui-ai/...-abliterated-GGUF`），
  **模型自身不会拒绝**成人向或边界内容；`prompts/system-core.md` 的底线只是提示词软约束，
  不能作为强制机制。
- 服务端没有内容分类器、没有输出过滤、没有年龄验证。前端的 18+ 确认是**本地提示**
  （`localStorage` 标记），清掉即可绕过；它的作用是明确告知与提醒，不是执法。
- 提示词注入、越狱、角色崩坏属于设计用途，不作为安全漏洞处理；但生成内容是否合法由使用者负责。

## 威胁矩阵（摘要）

| 威胁 | 缓解 | 残余风险 |
| --- | --- | --- |
| 恶意网页跨站删卡/烧显卡 | Origin/Referer 校验 + JSON Content-Type + SameSite=Strict | 关闭校验或使用无 Origin 的非常规客户端时 |
| DNS rebinding 读取页面/API | Host 白名单 | 用户把 `extra_hosts` 配得过宽 |
| 局域网他人访问 | 默认只绑 127.0.0.1；`--lan` 强制口令 | 口令泄露、弱口令 |
| 口令暴力枚举 | 建议长随机串 | 无速率限制/锁定 |
| 并发写同一张卡丢更新 | 写锁串行化 | 多个进程同时改（自己开了两个服务） |
| 供应链投毒 | SHA256 + HF 元数据校验 | 上游同时替换文件与元数据；首次信任 |
| 无审查模型生成不当内容 | 提示词底线 + 文档告知 + 18+ 前端提示 | 无强制过滤；使用者自负 |
| 敏感对话落盘 | 文档明确位置；`.gitignore` 忽略运行时数据 | 无加密；本机其他用户可读 |

## 加固清单

1. 保持 `host: 127.0.0.1`；只在需要手机访问时临时开 `--lan`，并设置长随机 `access_key`。
2. 公网访问必须叠加 HTTPS 反向代理 + 来源 IP 限制；不要直接端口转发裸 HTTP。
3. 不要提交/分享 `web/config.json`、`saves/`、`logs/`；备份 `personas/` 与 `config/user_personas/`。
4. 不要把 Ollama 的 `11434` 暴露到公网（它没有鉴权）。
5. 下载后保留 `.sha256` 文件；对高风险场景用站外公布的校验值复核。
6. 需要审计时打开 `access_log`，定期清理 `logs/access.log`。

漏洞上报方式见仓库根目录 [SECURITY.md](../SECURITY.md)。
