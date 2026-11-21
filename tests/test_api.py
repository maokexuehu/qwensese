#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端 HTTP 测试：在临时目录里复制仓库必要文件，启动真实 web/app.py 服务。

原则（见 tests/README.md）：
  * 只用标准库（urllib / subprocess / socket / tempfile / json）。
  * 绝不写仓库本身：web/personas/prompts/config 复制到 tempfile 临时根，
    服务进程 cwd = 临时根，因此 ROOT / 存档 / 角色卡写入全部落在临时目录。
  * 端口用 socket bind(0) 取空闲端口，且绝不复用 8717/8718/11434。
  * 配置里 Ollama 指向一个"已释放的空闲端口"，确定性模拟 Ollama 未启动
    （这样 /api/chat 等分支不受本机是否开着 Ollama 影响）。
  * 结束（含异常）一定 terminate/kill 子进程并删临时目录。

覆盖：/api/health(/health)、/api/state、/api/persona、/api/persona/save、/api/persona/delete、
/api/models、/v1/models、/api/me、/api/me/list、/api/saves（GET/POST/delete/单条）、/api/ctx、
静态页、畸形 JSON、非对象 JSON、/api/chat 参数校验、未知路径 404；
以及 access_key 门禁：无钥匙 403、X-Access-Key / cookie qk / ?k= 三种带法 200、错误钥匙 403。

用法：python tests/test_api.py
输出：每个用例一行 PASS/FAIL，最后 SUMMARY，FAIL>0 退出码 1。
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COPY_DIRS = ["web", "scripts", "personas", "prompts", "config"]
FORBIDDEN_PORTS = {8717, 8718, 11434}  # 项目默认 / Ollama 端口，测试绝不用
ACCESS_KEY = "testkey"

RESULTS = []
CASES_P1 = []
CASES_P2 = []
STATE = {
    "base": None,
    "tmp": None,
    "save_id": None,
    "save_saved_at": None,
    "evil_save_id": None,
    "persona_count": None,
}

_UNSET = object()


# ---------------------------------------------------------------- 基础工具
def log(msg=""):
    print(msg, flush=True)


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def rel(path):
    return os.path.relpath(path, REPO_ROOT).replace("\\", "/")


def free_port():
    """socket bind(0) 拿一个空闲端口；避开项目默认端口。"""
    while True:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        finally:
            s.close()
        if port not in FORBIDDEN_PORTS:
            return port


def port_open(port, timeout=0.3):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def http(method, path, body=_UNSET, headers=None, timeout=10):
    """发请求，返回 (status_code, raw_bytes, response_headers)。HTTP 错误码也算正常返回。"""
    url = STATE["base"] + path
    data = None
    hdrs = dict(headers or {})
    if body is not _UNSET:
        if isinstance(body, bytes):
            data = body
        else:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json; charset=utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def http_json(method, path, body=_UNSET, headers=None, timeout=10):
    code, raw, hdrs = http(method, path, body=body, headers=headers, timeout=timeout)
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:
        obj = None
    return code, obj, raw, hdrs


def get(path, headers=None, timeout=10):
    return http_json("GET", path, headers=headers, timeout=timeout)


def post(path, body, headers=None, timeout=10):
    return http_json("POST", path, body=body, headers=headers, timeout=timeout)


def write_config(tmp_root, port, ollama_port, access_key):
    """在临时副本 web/config.json 写测试配置（UTF-8 无 BOM，newline=\\n）。"""
    cfg = {
        "model": "test-model",
        # 指向一个刚释放的空闲端口：确定性模拟 "Ollama 未启动"，不用 11434
        "ollama": "http://127.0.0.1:%d" % ollama_port,
        "num_ctx": 4096,
        "host": "127.0.0.1",
        "port": port,
        "access_key": access_key,
    }
    path = os.path.join(tmp_root, "web", "config.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n")


def prepare_temp_root():
    tmp = tempfile.mkdtemp(prefix="qwensese-api-test-")
    for d in COPY_DIRS:
        src = os.path.join(REPO_ROOT, d)
        expect(os.path.isdir(src), "仓库缺少目录 %s" % d)
        shutil.copytree(src, os.path.join(tmp, d),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    return tmp


# ---------------------------------------------------------------- 服务进程
class Server:
    def __init__(self, root, port):
        self.root = root
        self.port = port
        self.proc = None
        self.log_path = os.path.join(root, "server-%d.log" % port)
        self._log = None

    def start(self):
        self._log = open(self.log_path, "wb")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env.pop("PYTHONSTARTUP", None)
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(self.root, "web", "app.py")],
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        return self

    def tail(self, n=40):
        try:
            if self._log is not None:
                self._log.flush()
        except Exception:
            pass
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            return "\n".join(lines[-n:]) if lines else "(日志为空)"
        except Exception as e:
            return "(读日志失败: %s)" % e

    def wait_ready(self, timeout=20.0, headers=None):
        """轮询 /api/health 直到 200（最多 timeout 秒）；失败时打印进程日志便于诊断。

        注意：/api/health 里会调 ollama_up()（它内部 urlopen timeout=2s）。在本机
        连一个"没人监听的回环端口"可能要等到超时才失败，所以单次请求超时给 8s，
        不能给小值（否则服务明明好了也会被客户端提前断开）。
        """
        deadline = time.time() + timeout
        last_code = None
        last_err = None
        time.sleep(0.3)  # 给刚 Popen 的服务一点绑定端口的时间
        while time.time() < deadline:
            rc = self.proc.poll()
            if rc is not None:
                raise RuntimeError(
                    "服务进程启动后立即退出 rc=%s\n--- 进程输出（尾部）---\n%s"
                    % (rc, self.tail()))
            try:
                code, obj, _, _ = get("/api/health", headers=headers, timeout=8.0)
                last_code = code
                if code == 200:
                    return obj
            except Exception as e:
                last_err = e
            time.sleep(0.2)
        raise RuntimeError(
            "等待 /api/health 超时 %ss（最后 status=%r, 最后错误=%r）\n"
            "--- 进程输出（尾部）---\n%s" % (timeout, last_code, last_err, self.tail()))

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        if self._log is not None:
            try:
                self._log.close()
            except Exception:
                pass
            self._log = None


# ---------------------------------------------------------------- 用例框架
def _register(bucket, name):
    def deco(fn):
        bucket.append((name, fn))
        return fn

    return deco


def c1(name):
    return _register(CASES_P1, name)


def c2(name):
    return _register(CASES_P2, name)


def run_cases(cases):
    passed = failed = 0
    for name, fn in cases:
        try:
            detail = fn() or ""
            passed += 1
            log("[PASS] %s%s" % (name, ("  --  " + detail) if detail else ""))
        except Exception as e:
            failed += 1
            log("[FAIL] %s\n        %s: %s" % (name, type(e).__name__, e))
    return passed, failed


# ================================================================ 第一阶段（access_key 为空）
@c1("/api/health: 200 且含 ok / ollama / personas")
def t_health():
    code, obj, _, _ = get("/api/health")
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(isinstance(obj, dict), "返回不是 JSON 对象")
    expect(obj.get("ok") is True, "ok 不是 true: %r" % (obj.get("ok"),))
    expect(isinstance(obj.get("ollama"), bool), "ollama 不是 bool: %r" % (obj.get("ollama"),))
    expect(isinstance(obj.get("personas"), int) and obj["personas"] >= 2,
           "personas 数量异常: %r" % (obj.get("personas"),))
    return "ok=%r ollama=%r personas=%r" % (obj["ok"], obj["ollama"], obj["personas"])


@c1("/health: 与 /api/health 同一处理")
def t_health_alias():
    code, obj, _, _ = get("/health")
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(isinstance(obj, dict) and obj.get("ok") is True, "返回形状不对: %r" % (obj,))
    return "ok=true"


@c1("/api/version: 未知路径返回 404（本服务没有该路由）")
def t_version_404():
    code, obj, _, _ = get("/api/version")
    # app.py 里 /api/version 只是它调用 Ollama 上游的 URL，不是本服务路由
    expect(code == 404, "期望 404（app.py 无此路由），实际 %s" % code)
    expect(isinstance(obj, dict) and obj.get("error") == "not found",
           "404 返回体不对: %r" % (obj,))
    return "404 not found"


@c1("/api/state: 角色卡列表 + model + root 推导正确")
def t_state():
    tmp = STATE["tmp"]
    code, obj, _, _ = get("/api/state")
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(isinstance(obj.get("personas"), list), "personas 不是列表")
    expect(len(obj["personas"]) >= 2, "角色卡数量 %d < 2" % len(obj["personas"]))
    files = [p.get("file") for p in obj["personas"]]
    expect("persona.md" in files, "personas 列表里没有 persona.md: %r" % files)
    for p in obj["personas"]:
        for key in ("id", "name", "file", "level"):
            expect(key in p, "角色卡项缺字段 %r: %r" % (key, p))
    expect(obj.get("model") == "test-model", "model 没读到临时配置: %r" % (obj.get("model"),))
    expect(isinstance(obj.get("levels"), dict) and len(obj["levels"]) == 4,
           "levels 不是 4 档: %r" % (obj.get("levels"),))
    expect(isinstance(obj.get("server_ok"), bool), "server_ok 不是 bool")
    # ROOT 必须从 app.py 文件位置推导，= 临时根（而不是仓库 / cwd / 硬编码）
    root = os.path.normcase(os.path.normpath(obj.get("root") or ""))
    expect(root == os.path.normcase(os.path.normpath(tmp)),
           "root 不是临时根: %r vs %r" % (obj.get("root"), tmp))
    expect(root != os.path.normcase(os.path.normpath(REPO_ROOT)),
           "root 指向了真实仓库，测试没有隔离！")
    return "personas=%d model=test-model root=临时根" % len(obj["personas"])


@c1("/api/persona?id=persona.md: 返回 fields 且含 名字")
def t_persona_get():
    code, obj, _, _ = get("/api/persona?id=persona.md")
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(obj.get("file") == "persona.md", "file 不对: %r" % (obj.get("file"),))
    fields = obj.get("fields")
    expect(isinstance(fields, dict) and fields, "fields 不是非空对象")
    expect(fields.get("名字", "").strip(), "fields 里 名字 为空")
    for key in ("外貌", "性格"):
        expect(key in fields, "fields 缺 %r" % key)
    return "名字=%s, %d 个字段" % (fields["名字"], len(fields))


@c1("/api/persona?id=../web/app.py: 目录穿越需 400")
def t_persona_traversal():
    path = "/api/persona?id=" + urllib.parse.quote("../web/app.py", safe="")
    code, obj, _, _ = get(path)
    expect(code == 400, "期望 400（只允许 personas/ 下的 .md），实际 %s" % code)
    expect(isinstance(obj, dict) and obj.get("error"), "400 返回体缺 error")
    return "400 %s" % obj.get("error")


@c1("/api/persona?id=不存在.md: 404")
def t_persona_missing():
    code, obj, _, _ = get("/api/persona?id=no-such-card.md")
    expect(code == 404, "期望 404，实际 %s" % code)
    expect(isinstance(obj, dict) and obj.get("error"), "404 返回体缺 error")
    return "404"


@c1("/api/models: 200 且 models 列表 / ollama_ok 为 bool（不要求 Ollama 在线）")
def t_models():
    code, obj, _, _ = get("/api/models")
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(isinstance(obj.get("models"), list) and len(obj["models"]) >= 2,
           "models 列表异常: %r" % (obj.get("models"),))
    expect(isinstance(obj.get("ollama_ok"), bool), "ollama_ok 不是 bool: %r" % (obj.get("ollama_ok"),))
    for m in obj["models"]:
        for key in ("id", "name", "label", "num_ctx"):
            expect(key in m, "模型项缺字段 %r: %r" % (key, m))
    # 预设模型即使没安装、Ollama 离线也要列出来（前端下拉用）
    names = [m["name"] for m in obj["models"]]
    expect(any("qwensese" in n for n in names), "预设模型没列出: %r" % names)
    return "models=%d ollama_ok=%r" % (len(obj["models"]), obj["ollama_ok"])


@c1("/v1/models: OpenAI 兼容模型目录 200")
def t_v1_models():
    code, obj, _, _ = get("/v1/models")
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(obj.get("object") == "list", "object 不是 list: %r" % (obj.get("object"),))
    expect(isinstance(obj.get("data"), list) and len(obj["data"]) >= 1, "data 列表为空")
    for m in obj["data"]:
        expect(m.get("id") and m.get("object") == "model", "模型项形状不对: %r" % (m,))
    return "data=%d" % len(obj["data"])


@c1("/api/me/list: 200 且含 default.md")
def t_me_list():
    code, obj, _, _ = get("/api/me/list")
    expect(code == 200, "期望 200，实际 %s" % code)
    lst = obj.get("list")
    expect(isinstance(lst, list) and lst, "list 不是非空列表: %r" % (lst,))
    files = [x.get("file") for x in lst]
    expect("default.md" in files, "用户角色卡列表里没有 default.md: %r" % files)
    expect(isinstance(obj.get("file"), str) and obj["file"], "file 缺失")
    return "list=%d file=%s" % (len(lst), obj["file"])


@c1("/api/me: 200 且 fields / path 落在临时目录")
def t_me_get():
    tmp = STATE["tmp"]
    code, obj, _, _ = get("/api/me")
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(isinstance(obj.get("fields"), dict) and obj["fields"], "fields 不是非空对象")
    expect("名字" in obj["fields"], "用户设定缺 名字: %r" % list(obj["fields"]))
    path = os.path.normcase(os.path.normpath(obj.get("path") or ""))
    me_dir = os.path.normcase(os.path.normpath(os.path.join(tmp, "config", "user_personas")))
    expect(path.startswith(me_dir), "path 不在临时 config/user_personas 下: %r" % path)
    return "名字=%s" % obj["fields"]["名字"]


@c1("/api/persona/save: 只提交一个字段，其它字段保留（合并写）")
def t_persona_merge():
    _, before, _, _ = get("/api/persona?id=persona.md")
    old_fields = before["fields"]
    code, obj, _, _ = post("/api/persona/save",
                           {"file": "persona.md", "fields": {"外貌": "测试用外貌（临时副本）"}})
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(obj.get("ok") is True, "ok 不是 true: %r" % (obj,))
    expect(obj.get("file") == "persona.md", "file 不对: %r" % (obj.get("file"),))
    expect("persona.md" in [p.get("file") for p in obj.get("personas", [])],
           "返回的 personas 里没有 persona.md")
    _, after, _, _ = get("/api/persona?id=persona.md")
    new_fields = after["fields"]
    expect(new_fields.get("外貌") == "测试用外貌（临时副本）", "外貌没写进去: %r" % (new_fields.get("外貌"),))
    expect(new_fields.get("名字") == old_fields.get("名字"), "合并写把 名字 弄丢了")
    expect(new_fields.get("性格") == old_fields.get("性格"), "合并写把 性格 弄丢了")
    expect(new_fields.get("尺度") == old_fields.get("尺度"), "合并写把 尺度 弄丢了")
    return "外貌 已更新, 名字/性格/尺度 保留"


@c1("/api/persona/save: file=../../x.md 只会写进 personas/（路径收敛）")
def t_persona_save_traversal():
    tmp = STATE["tmp"]
    code, obj, _, _ = post("/api/persona/save",
                           {"file": "../../evil-persona.md", "fields": {"名字": "evil"}})
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(obj.get("file") == "evil-persona.md", "返回 file 不是 basename: %r" % (obj.get("file"),))
    expect(os.path.isfile(os.path.join(tmp, "personas", "evil-persona.md")),
           "没有写进临时 personas/")
    expect(not os.path.exists(os.path.join(tmp, "evil-persona.md")),
           "写到了临时根（目录穿越没收敛）")
    code, _, _, _ = post("/api/persona/delete", {"file": "evil-persona.md"})
    expect(code == 200, "清理测试卡失败: %s" % code)
    return "写入 personas/evil-persona.md 并已清理"


@c1("/api/persona/save: 新建角色卡后 /api/state 数量 +1")
def t_persona_create():
    tmp = STATE["tmp"]
    _, state0, _, _ = get("/api/state")
    count0 = len(state0["personas"])
    STATE["persona_count"] = count0
    code, obj, _, _ = post("/api/persona/save", {
        "file": "test-temp-card.md",
        "fields": {"名字": "临时测试角色", "外貌": "测试外貌", "性格": "测试性格", "尺度": "1"},
    })
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(os.path.isfile(os.path.join(tmp, "personas", "test-temp-card.md")),
           "新角色卡没写进临时 personas/")
    _, state1, _, _ = get("/api/state")
    expect(len(state1["personas"]) == count0 + 1,
           "数量没 +1: %d -> %d" % (count0, len(state1["personas"])))
    mine = [p for p in state1["personas"] if p.get("file") == "test-temp-card.md"]
    expect(mine, "新卡没出现在 personas 列表")
    expect(mine[0].get("name") == "临时测试角色", "name 不对: %r" % (mine[0].get("name"),))
    expect(mine[0].get("level") == 1, "level 不对: %r" % (mine[0].get("level"),))
    return "%d -> %d, name=临时测试角色, level=1" % (count0, len(state1["personas"]))


@c1("/api/persona/delete: 删除后角色卡数量恢复")
def t_persona_delete():
    count0 = STATE["persona_count"]
    expect(count0 is not None, "依赖 persona_create 用例先成功")
    code, obj, _, _ = post("/api/persona/delete", {"file": "test-temp-card.md"})
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(obj.get("ok") is True, "ok 不是 true: %r" % (obj,))
    _, state, _, _ = get("/api/state")
    expect(len(state["personas"]) == count0,
           "数量没恢复: 期望 %d，实际 %d" % (count0, len(state["personas"])))
    code, _, _, _ = get("/api/persona?id=test-temp-card.md")
    expect(code == 404, "删除后还能读到卡: %s" % code)
    return "数量恢复为 %d" % count0


@c1("/api/saves: 初始为空列表")
def t_saves_initial():
    code, obj, _, _ = get("/api/saves")
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(obj.get("saves") == [], "初始 saves 不是空列表: %r" % (obj.get("saves"),))
    return "saves=[]"


@c1("/api/saves POST: 写入 2 条 history -> 返回 id，标题回退到第一句 user 消息")
def t_saves_write():
    code, obj, _, _ = post("/api/saves", {
        "persona_id": "persona.md",
        "persona_name": "林晚",
        "level": 2,
        "mode": "dialogue",
        "model": "test-model",
        "num_ctx": 4096,
        "history": [
            {"role": "user", "content": "第一句用户消息：今晚还开门吗？"},
            {"role": "assistant", "content": "打烊了。……哦，是你。"},
        ],
    })
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(obj.get("ok") is True, "ok 不是 true: %r" % (obj,))
    sid = obj.get("id")
    expect(isinstance(sid, str) and sid, "没返回 id: %r" % (sid,))
    STATE["save_id"] = sid
    _, lst, _, _ = get("/api/saves")
    saves = lst.get("saves")
    expect(isinstance(saves, list) and len(saves) == 1, "saves 应该有 1 条，实际 %r" % (saves,))
    item = saves[0]
    expect(item.get("id") == sid, "id 不一致: %r vs %r" % (item.get("id"), sid))
    # 没传 title：回退成第一句 user 消息
    expect(item.get("title") == "第一句用户消息：今晚还开门吗？",
           "title 回退不对: %r" % (item.get("title"),))
    expect(item.get("msg_count") == 2, "msg_count 不对: %r" % (item.get("msg_count"),))
    expect(item.get("persona_name") == "林晚", "persona_name 不对: %r" % (item.get("persona_name"),))
    expect(item.get("last_line"), "last_line 为空")
    STATE["save_saved_at"] = item.get("saved_at")
    return "id=%s title=第一句用户消息 msg_count=2" % sid


@c1("/api/saves?id=: 读单条存档")
def t_saves_load():
    sid = STATE["save_id"]
    expect(sid, "依赖 saves_write 用例先成功")
    code, obj, _, _ = get("/api/saves?id=" + urllib.parse.quote(sid, safe=""))
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(obj.get("id") == sid, "id 不一致: %r" % (obj.get("id"),))
    hist = obj.get("history")
    expect(isinstance(hist, list) and len(hist) == 2, "history 长度不对: %r" % (hist,))
    expect(hist[0].get("role") == "user", "history[0] 角色不对")
    return "history=2"


@c1("/api/saves?id=不存在: 404")
def t_saves_load_missing():
    code, obj, _, _ = get("/api/saves?id=no-such-save-xyz")
    expect(code == 404, "期望 404，实际 %s" % code)
    expect(isinstance(obj, dict) and obj.get("error"), "404 返回体缺 error")
    return "404"


@c1("/api/saves POST: 带 id 更新 -> title 覆盖、saved_at 保留")
def t_saves_update():
    sid = STATE["save_id"]
    expect(sid, "依赖 saves_write 用例先成功")
    code, obj, _, _ = post("/api/saves", {
        "id": sid,
        "title": "自定义标题",
        "history": [
            {"role": "user", "content": "第一句"},
            {"role": "assistant", "content": "回复一"},
            {"role": "user", "content": "第二句"},
        ],
    })
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(obj.get("id") == sid, "更新后 id 变了: %r" % (obj.get("id"),))
    _, lst, _, _ = get("/api/saves")
    saves = lst.get("saves")
    expect(len(saves) == 1, "更新后应仍是 1 条，实际 %d" % len(saves))
    item = saves[0]
    expect(item.get("title") == "自定义标题", "title 没更新: %r" % (item.get("title"),))
    expect(item.get("msg_count") == 3, "msg_count 没更新: %r" % (item.get("msg_count"),))
    expect(item.get("saved_at") == STATE["save_saved_at"],
           "更新把 saved_at 覆盖了: %r -> %r" % (STATE["save_saved_at"], item.get("saved_at")))
    return "title=自定义标题 msg_count=3 saved_at 保留"


@c1("/api/saves POST: id 带路径字符时收敛成安全 id（落盘/响应/列表一致）")
def t_saves_id_sanitize():
    tmp = STATE["tmp"]
    code, obj, _, _ = post("/api/saves", {
        "id": "../../evil-save",
        "history": [{"role": "user", "content": "x"}],
    })
    expect(code == 200, "期望 200，实际 %s" % code)
    sid = obj.get("id")
    expect(sid == "evil-save", "id 没被收敛成 evil-save: %r" % (sid,))
    expect(os.path.isfile(os.path.join(tmp, "saves", "evil-save.json")),
           "存档没写进临时 saves/")
    expect(not os.path.exists(os.path.join(tmp, "evil-save.json")),
           "存档写到了临时根（目录穿越没收敛）")
    # 响应里的 id 和 /api/saves 列表里的 id 必须一致
    _, lst, _, _ = get("/api/saves")
    ids = [s.get("id") for s in lst.get("saves", [])]
    expect("evil-save" in ids, "列表里的 id 与响应不一致: %r" % (ids,))
    # 用收敛后的 id 仍能读回
    code, saved, _, _ = get("/api/saves?id=evil-save")
    expect(code == 200, "用收敛后的 id 读存档失败: %s" % code)
    expect(saved.get("id") == "evil-save", "读回的 id 不一致: %r" % (saved.get("id"),))
    STATE["evil_save_id"] = sid
    return "id ../../evil-save -> evil-save；落盘/响应/列表一致"


@c1("/api/saves POST: id 全是不合法字符 -> 400")
def t_saves_id_invalid():
    code, obj, _, _ = post("/api/saves", {
        "id": "!!!",
        "history": [{"role": "user", "content": "x"}],
    })
    expect(code == 400, "期望 400（非法 id），实际 %s" % code)
    expect(isinstance(obj, dict) and "存档 id" in str(obj.get("error", "")),
           "错误信息不对: %r" % (obj,))
    return "400 %s" % obj.get("error")


@c1("/api/saves/delete: 删除后列表归零")
def t_saves_delete():
    sid = STATE["save_id"]
    expect(sid, "依赖 saves_write 用例先成功")
    code, obj, _, _ = post("/api/saves/delete", {"id": sid})
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(obj.get("ok") is True, "ok 不是 true")
    if STATE["evil_save_id"]:
        code, _, _, _ = post("/api/saves/delete", {"id": STATE["evil_save_id"]})
        expect(code == 200, "清理路径收敛测试存档失败: %s" % code)
    _, lst, _, _ = get("/api/saves")
    expect(lst.get("saves") == [], "删除后 saves 不是空列表: %r" % (lst.get("saves"),))
    return "saves=[]"


@c1("/api/ctx: 200 且 token 统计字段齐全")
def t_ctx():
    code, obj, _, _ = get("/api/ctx")
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(isinstance(obj.get("system_chars"), int) and obj["system_chars"] > 0,
           "system_chars 异常: %r" % (obj.get("system_chars"),))
    expect(isinstance(obj.get("system_tokens"), int) and obj["system_tokens"] > 0,
           "system_tokens 异常: %r" % (obj.get("system_tokens"),))
    expect("chars_per_token" in obj, "缺 chars_per_token")
    STATE["ctx_base"] = obj["system_chars"]
    return "system_chars=%d system_tokens=%d" % (obj["system_chars"], obj["system_tokens"])


@c1("/api/ctx?persona_id=..&mode=narrator: 200 且 system 变长")
def t_ctx_variants():
    code, obj, _, _ = get("/api/ctx?persona_id=persona.md&level=1&mode=narrator")
    expect(code == 200, "期望 200，实际 %s" % code)
    base = STATE.get("ctx_base") or 0
    expect(obj.get("system_chars", 0) > base,
           "带角色卡+叙事模式的 system 没变长: %r vs %r" % (obj.get("system_chars"), base))
    return "system_chars=%d (默认 %d)" % (obj["system_chars"], base)


@c1("静态页: GET / 返回 index.html")
def t_static_index():
    code, raw, hdrs = http("GET", "/")
    expect(code == 200, "期望 200，实际 %s" % code)
    expect("text/html" in (hdrs.get("Content-Type") or ""), "Content-Type 不是 html")
    low = raw.lower()
    expect(b"<!doctype html" in low or b"<html" in low, "返回内容不是 HTML")
    return "%d 字节" % len(raw)


@c1("静态资源: GET /app.js 返回 JS")
def t_static_js():
    code, raw, hdrs = http("GET", "/app.js")
    expect(code == 200, "期望 200，实际 %s" % code)
    expect("javascript" in (hdrs.get("Content-Type") or ""), "Content-Type 不是 JS")
    expect(len(raw) > 10000, "app.js 太小: %d" % len(raw))
    return "%d 字节" % len(raw)


@c1("错误处理: 畸形 JSON -> 400「请求体不是合法 JSON」")
def t_bad_json():
    code, obj, _, _ = post("/api/persona/save", b'{"fields": ')
    expect(code == 400, "期望 400，实际 %s" % code)
    expect(isinstance(obj, dict) and "不是合法 JSON" in str(obj.get("error", "")),
           "错误信息不对: %r" % (obj,))
    return "400 %s" % obj.get("error")


@c1("错误处理: 非对象 JSON -> 400「请求体必须是 JSON 对象」")
def t_non_object_json():
    code, obj, _, _ = post("/api/persona/save", b"[1, 2, 3]")
    expect(code == 400, "期望 400，实际 %s" % code)
    expect(isinstance(obj, dict) and "必须是 JSON 对象" in str(obj.get("error", "")),
           "错误信息不对（应保留具体原因）: %r" % (obj,))
    return "400 %s" % obj.get("error")


@c1("/api/chat: messages 不是数组 -> 400")
def t_chat_bad_type():
    code, obj, _, _ = post("/api/chat", {"messages": "not-a-list"})
    expect(code == 400, "期望 400（messages 必须是数组），实际 %s" % code)
    expect(isinstance(obj, dict) and "数组" in str(obj.get("error", "")),
           "错误信息不对: %r" % (obj,))
    return "400 %s" % obj.get("error")


@c1("/api/chat: 空 messages 的真实行为（无 Ollama 时 503）")
def t_chat_empty():
    code, obj, _, _ = post("/api/chat", {"messages": []})
    # 空 messages 会走到 _chat -> ensure_ollama()；临时副本没有 ollama.exe
    # 且 config.ollama 指向空闲端口，所以确定性返回 503（Ollama 未启动），
    # 不会返回 4xx —— 空数组本身能通过 messages 的列表校验。
    expect(code == 503, "期望 503（无 Ollama 分支），实际 %s" % code)
    expect(isinstance(obj, dict) and "Ollama" in str(obj.get("error", "")),
           "503 错误信息不对: %r" % (obj,))
    return "503（空数组通过列表校验，先被 Ollama 未启动拦截）"


@c1("错误处理: 未知 GET 路径 -> 404")
def t_unknown_get():
    code, obj, _, _ = get("/api/definitely-not-a-route")
    expect(code == 404, "期望 404，实际 %s" % code)
    expect(isinstance(obj, dict) and obj.get("error") == "not found",
           "404 返回体不对: %r" % (obj,))
    return "404 not found"


@c1("错误处理: 未知 POST 路径 -> 404")
def t_unknown_post():
    code, obj, _, _ = post("/api/definitely-not-a-route", {"x": 1})
    expect(code == 404, "期望 404，实际 %s" % code)
    expect(isinstance(obj, dict) and obj.get("error") == "not found",
           "404 返回体不对: %r" % (obj,))
    return "404 not found"


# ================================================================ 安全加固（第一阶段，无口令）
def _base_host():
    return urllib.parse.urlparse(STATE["base"]).hostname


def _base_origin():
    u = urllib.parse.urlparse(STATE["base"])
    return "%s://%s:%d" % (u.scheme, u.hostname, u.port)


@c1("安全: 每个响应都带 CSP / nosniff / X-Frame-Options")
def t_security_headers():
    code, obj, _, hdrs = get("/api/health")
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(hdrs.get("X-Content-Type-Options") == "nosniff", "缺 nosniff: %r" % hdrs.get("X-Content-Type-Options"))
    expect(hdrs.get("X-Frame-Options") == "DENY", "缺 X-Frame-Options: %r" % hdrs.get("X-Frame-Options"))
    csp = hdrs.get("Content-Security-Policy") or ""
    expect("default-src 'self'" in csp, "缺 CSP: %r" % csp)
    expect(hdrs.get("Referrer-Policy") == "no-referrer", "缺 Referrer-Policy")
    return "CSP + nosniff + DENY + no-referrer"


@c1("安全: 静态页也带安全响应头")
def t_security_headers_static():
    code, raw, hdrs = http("GET", "/")
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(hdrs.get("X-Content-Type-Options") == "nosniff", "静态页缺 nosniff")
    expect("default-src 'self'" in (hdrs.get("Content-Security-Policy") or ""), "静态页缺 CSP")
    expect(b"extra.js" in raw, "index.html 没引入 extra.js（18+ 门与硬件面板）")
    return "index.html + extra.js + 安全头"


@c1("安全: Host 不在允许列表 -> 403（防 DNS rebinding）")
def t_host_check():
    code, obj, _, _ = get("/api/health", headers={"Host": "evil.example"})
    expect(code == 403, "伪造 Host 应变 403，实际 %s" % code)
    code2, _, _ = http("GET", "/")
    expect(code2 == 200, "正常 Host 不应被拦")
    return "Host=evil.example -> 403"


@c1("安全: 跨站 Origin 的 POST -> 403（防 CSRF）")
def t_origin_check():
    code, obj, _, _ = post("/api/health", {}, headers={"Origin": "https://evil.example"})
    expect(code == 403, "跨站 Origin 应 403，实际 %s" % code)
    return "跨站 POST -> 403"


@c1("安全: 同源 Origin 的 POST 放行（走到路由层 404）")
def t_origin_same():
    code, obj, _, _ = post("/api/health", {}, headers={"Origin": _base_origin()})
    expect(code == 404, "同源 Origin 应放行到路由层(404)，实际 %s" % code)
    return "同源 POST -> 404（已过 Origin 检查）"


@c1("安全: 非 JSON Content-Type 的 POST -> 415")
def t_content_type():
    code, raw, _ = http("POST", "/api/health", body=b"{}", headers={"Content-Type": "text/plain"})
    expect(code == 415, "text/plain POST 应 415，实际 %s" % code)
    return "text/plain -> 415"


@c1("/api/hardware: 200 且返回显卡/推荐结构")
def t_hardware():
    code, obj, _, _ = get("/api/hardware", timeout=180)
    expect(code == 200, "期望 200，实际 %s" % code)
    if not obj.get("ok"):
        raise AssertionError("硬件检测失败: %r" % (obj.get("error"),))
    hw = obj.get("hardware") or {}
    rec = obj.get("recommendation") or {}
    expect("class" in hw and "gpus" in hw, "hardware 结构不完整")
    primary = rec.get("primary") or {}
    expect(primary.get("file", "").endswith(".gguf"), "primary.file 不是 .gguf: %r" % primary.get("file"))
    expect("download_command" in rec and "model_advisor.py" in rec["download_command"], "缺下载命令")
    return "%s / vram=%s / %s %s" % (hw.get("class"), hw.get("vram_gb"), primary.get("id"), primary.get("quant"))


# ================================================================ 第二阶段（access_key = testkey）
@c2("鉴权: 无钥匙 GET /api/state -> 403")
def t_auth_gate_state():
    code, obj, _, hdrs = get("/api/state")
    expect(code == 403, "期望 403，实际 %s" % code)
    expect(isinstance(obj, dict) and "访问钥匙" in str(obj.get("error", "")),
           "403 返回体不对: %r" % (obj,))
    expect(not hdrs.get("Set-Cookie"), "403 不该下发 cookie: %r" % hdrs.get("Set-Cookie"))
    return "403"


@c2("鉴权: 无钥匙 GET / -> 403 口令页（HTML）")
def t_auth_gate_root():
    code, raw, hdrs = http("GET", "/")
    expect(code == 403, "期望 403，实际 %s" % code)
    expect("text/html" in (hdrs.get("Content-Type") or ""), "Content-Type 不是 html")
    expect("访问口令" in raw.decode("utf-8", "replace"), "口令页内容不对")
    return "403 HTML 口令页"


@c2("鉴权: 无钥匙 GET /api/health -> 403（所有 GET 都过门禁）")
def t_auth_gate_health():
    code, obj, _, _ = get("/api/health")
    expect(code == 403, "期望 403，实际 %s" % code)
    expect(isinstance(obj, dict) and obj.get("error"), "403 返回体缺 error")
    return "403"


@c2("鉴权: 无钥匙 POST /api/chat -> 403（POST 也过门禁）")
def t_auth_gate_post():
    code, obj, _, _ = post("/api/chat", {"messages": []})
    expect(code == 403, "期望 403（门禁先于参数校验），实际 %s" % code)
    expect(isinstance(obj, dict) and obj.get("error"), "403 返回体缺 error")
    return "403"


@c2("鉴权: 请求头 X-Access-Key=testkey -> 200")
def t_auth_header():
    code, obj, _, _ = get("/api/state", headers={"X-Access-Key": ACCESS_KEY})
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(obj.get("model") == "test-model", "state 内容不对: %r" % (obj.get("model"),))
    return "200"


@c2("鉴权: cookie qk=testkey -> 200")
def t_auth_cookie():
    code, obj, _, _ = get("/api/state", headers={"Cookie": "qk=" + ACCESS_KEY})
    expect(code == 200, "期望 200，实际 %s" % code)
    expect(isinstance(obj.get("personas"), list), "state 内容不对")
    return "200"


@c2("鉴权: ?k=testkey -> 200 且下发 cookie qk")
def t_auth_query():
    code, obj, _, hdrs = get("/api/state?k=" + ACCESS_KEY)
    expect(code == 200, "期望 200，实际 %s" % code)
    cookie = hdrs.get("Set-Cookie") or ""
    expect("qk=" + ACCESS_KEY in cookie, "没下发 cookie qk: %r" % cookie)
    expect("SameSite=Strict" in cookie, "cookie 必须是 SameSite=Strict: %r" % cookie)
    return "200, Set-Cookie=qk=testkey"


@c2("鉴权: 错误请求头钥匙 -> 403")
def t_auth_wrong_header():
    code, obj, _, _ = get("/api/state", headers={"X-Access-Key": "nope"})
    expect(code == 403, "期望 403，实际 %s" % code)
    expect(isinstance(obj, dict) and obj.get("error"), "403 返回体缺 error")
    return "403"


@c2("鉴权: 错误 ?k= -> 403 且不下发 cookie")
def t_auth_wrong_query():
    code, obj, _, hdrs = get("/api/state?k=nope")
    expect(code == 403, "期望 403，实际 %s" % code)
    expect(not hdrs.get("Set-Cookie"), "错误钥匙不该下发 cookie: %r" % hdrs.get("Set-Cookie"))
    return "403"


@c2("鉴权: 口令页种 cookie 后可直接访问 /api/state")
def t_auth_cookie_flow():
    code, raw, hdrs = http("GET", "/?k=" + ACCESS_KEY)
    expect(code == 200, "口令页期望 200，实际 %s" % code)
    cookie = hdrs.get("Set-Cookie") or ""
    expect("qk=" + ACCESS_KEY in cookie, "口令页没种 cookie: %r" % cookie)
    # 只带 cookie 再请求
    code2, obj, _, _ = get("/api/state", headers={"Cookie": "qk=" + ACCESS_KEY})
    expect(code2 == 200, "带 cookie 访问 /api/state 失败: %s" % code2)
    return "口令页 200 -> cookie -> /api/state 200"


# ---------------------------------------------------------------- 清理检查
def report_cleanup(servers, ports, tmp_root):
    problems = []

    alive = [s.port for s in servers if s.proc is not None and s.proc.poll() is None]
    if alive:
        problems.append("服务进程仍在运行: 端口 %s" % alive)

    busy = [p for p in ports if port_open(p)]
    if busy:
        problems.append("端口仍被占用: %s" % busy)

    if tmp_root and os.path.isdir(tmp_root):
        try:
            shutil.rmtree(tmp_root)
        except Exception as e:
            problems.append("临时目录删除失败: %s (%s)" % (tmp_root, e))
    if tmp_root and os.path.exists(tmp_root):
        problems.append("临时目录仍然存在: %s" % tmp_root)

    for label, probs in (("清理: 无残留进程/端口/临时目录", problems),):
        if probs:
            RESULTS.append(("FAIL", label, "; ".join(probs)))
            log("[FAIL] %s\n        %s" % (label, "; ".join(probs)))
            return 1
        RESULTS.append(("PASS", label, "进程已退出、端口已释放、临时目录已删除"))
        log("[PASS] %s  --  进程已退出、端口已释放、临时目录已删除" % label)
        return 0


# ---------------------------------------------------------------- 主流程
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    log("=" * 64)
    log("端到端 HTTP 测试（真实启动 web/app.py）")
    log("仓库根 : %s" % REPO_ROOT)
    log("Python : %s" % sys.executable)
    log("=" * 64)

    tmp = None
    servers = []
    ports = []
    passed = failed = 0
    exit_code = 0

    try:
        tmp = prepare_temp_root()
        STATE["tmp"] = tmp
        log("临时副本: %s" % tmp)

        # ---------------- 第一阶段：access_key 为空
        ollama_port = free_port()          # 只用来"指向一个没有服务的端口"
        port1 = free_port()
        write_config(tmp, port1, ollama_port, access_key="")
        for forbidden in FORBIDDEN_PORTS:
            expect(port1 != forbidden and ollama_port != forbidden,
                   "测试端口撞上了禁用端口 %s" % forbidden)
        s1 = Server(tmp, port1).start()
        servers.append(s1)
        ports.append(port1)
        STATE["base"] = "http://127.0.0.1:%d" % port1
        log("\n--- 阶段 1: access_key 为空，端口 %d；Ollama 指向 127.0.0.1:%d（无服务）---"
            % (port1, ollama_port))
        obj = s1.wait_ready(timeout=20.0)
        log("服务已就绪: /api/health -> %r" % (obj,))
        p, f = run_cases(CASES_P1)
        passed += p
        failed += f

        # ---------------- 第二阶段：access_key = testkey，重启服务
        s1.stop()
        servers.remove(s1)
        port2 = free_port()
        write_config(tmp, port2, ollama_port, access_key=ACCESS_KEY)
        s2 = Server(tmp, port2).start()
        servers.append(s2)
        ports.append(port2)
        STATE["base"] = "http://127.0.0.1:%d" % port2
        log("\n--- 阶段 2: access_key=%r，重启服务，端口 %d ---" % (ACCESS_KEY, port2))
        obj = s2.wait_ready(timeout=20.0, headers={"X-Access-Key": ACCESS_KEY})
        log("服务已就绪（带钥匙）: /api/health -> %r" % (obj,))
        p, f = run_cases(CASES_P2)
        passed += p
        failed += f
    except Exception as e:
        failed += 1
        exit_code = 1
        log("\n[FAIL] 测试环境启动/运行致命错误: %s: %s" % (type(e).__name__, e))
        traceback.print_exc()
    finally:
        for s in servers:
            try:
                s.stop()
            except Exception as e:
                log("停止服务失败: %s" % e)
        # 清理检查（会删临时目录）
        cleanup_fail = report_cleanup(servers, ports, tmp)
        failed += cleanup_fail

    log("-" * 64)
    log("SUMMARY pass=%d fail=%d xfail=0" % (passed, failed))
    if failed:
        log("RESULT: FAIL")
        return 1
    log("RESULT: PASS")
    return 0 if exit_code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
