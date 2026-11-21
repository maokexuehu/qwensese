#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""硬件选型工具链测试（纯标准库，不联网、不需要 GPU）。

覆盖：
  * scripts/model_catalog.json 结构合法、id 唯一、量化文件命名与大小合理
  * scripts/model_advisor.py 的 recommend() 纯函数：
      - 纯 CPU / 4GB / 8GB / 12GB / 16GB / 24GB 各档推荐的参数规模合理
      - 16GB 档优先本仓库实测的 27B UD-IQ4_XS（回归保护：不能退回小模型）
      - prefer_uncensored=True 时优先 abliterated；False 时优先官方模型
      - 装不下任何模型时 fallback=True 且有 warning
      - num_ctx 随显存单调不减
  * detect_hardware() 返回结构合法（本机可能没有 GPU，允许 vram=0）
  * CLI：--json / --list 输出合法且退出码 0；--download 的 --dry-run 不落盘
  * scripts/download_model.py：sha256_file() 与 hashlib 结果一致；--dry-run 退出码 0
  * scripts/import_model.py：--dry-run 能生成含 FROM / SYSTEM / PARAMETER 的 Modelfile
  * scripts/model-advisor.ps1 是纯 ASCII（PowerShell 脚本跨代码页可读）

用法：python tests/test_advisor.py   输出 SUMMARY，FAIL>0 退出码 1。
"""

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO_ROOT, "scripts")
CATALOG_PATH = os.path.join(SCRIPTS, "model_catalog.json")
sys.path.insert(0, SCRIPTS)

RESULTS = []
CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def run_cli(args, timeout=180):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run([sys.executable] + args, cwd=REPO_ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


@case("目录: model_catalog.json 结构合法")
def t_catalog_structure():
    expect(os.path.isfile(CATALOG_PATH), "缺少 scripts/model_catalog.json")
    with io.open(CATALOG_PATH, encoding="utf-8") as f:
        cat = json.load(f)
    models = cat.get("models")
    expect(isinstance(models, list) and len(models) >= 6, "models 数量不足: %r" % type(models))
    ids = set()
    for m in models:
        for key in ("id", "label", "repo", "params_b", "quants"):
            expect(key in m, "模型缺字段 %s: %r" % (key, m.get("id")))
        expect(m["id"] not in ids, "重复 id: %r" % m["id"])
        ids.add(m["id"])
        expect(re.match(r"^[\w.-]+/[\w.-]+$", m["repo"]), "repo 不是 owner/name: %r" % m["repo"])
        quants = m["quants"]
        expect(isinstance(quants, list) and quants, "模型 %s 没有 quants" % m["id"])
        for q in quants:
            for key in ("quant", "file", "size_gb", "min_vram_gb"):
                expect(key in q, "%s 的量缺字段 %s" % (m["id"], key))
            expect(q["file"].lower().endswith(".gguf"), "量化文件不是 .gguf: %r" % q["file"])
            expect(0 < float(q["size_gb"]) < 100, "size_gb 不合理: %r" % q["size_gb"])
            expect(0 < float(q["min_vram_gb"]) < 100, "min_vram_gb 不合理: %r" % q["min_vram_gb"])
    return "%d 个模型 / %d 个量化" % (len(models), sum(len(m["quants"]) for m in models))


@case("推荐: 纯 CPU 兜底给小模型")
def t_recommend_cpu():
    import model_advisor

    hw = {"os": "Linux", "cpu": "test", "cores": 8, "ram_gb": 16.0,
          "gpus": [], "primary_gpu": {"name": "none", "vram_gb": 0, "kind": "unknown"},
          "vram_gb": 0.0, "unified_memory": False, "class": "cpu"}
    rec = model_advisor.recommend(hw)
    p = rec["primary"]
    expect(p["params_b"] <= 8, "CPU 档推荐了过大的模型: %r" % p["params_b"])
    expect(rec["warnings"], "CPU 档应有 warning")
    return "CPU -> %s %s" % (p["id"], p["quant"])


@case("推荐: 4GB 显存 -> ≤8B")
def t_recommend_4gb():
    import model_advisor

    hw = {"os": "Windows", "cpu": "test", "cores": 8, "ram_gb": 16.0,
          "gpus": [{"name": "GeForce GTX 1650", "vram_gb": 4.0, "kind": "nvidia"}],
          "primary_gpu": {"name": "GeForce GTX 1650", "vram_gb": 4.0, "kind": "nvidia"},
          "vram_gb": 4.0, "unified_memory": False, "class": "entry"}
    p = model_advisor.recommend(hw)["primary"]
    expect(p["params_b"] <= 8, "4GB 档推荐了 %rB 模型" % p["params_b"])
    expect(p["min_vram_gb"] <= 5.5, "4GB 档推荐了需要 %rGB 显存的模型" % p["min_vram_gb"])
    return "4GB -> %s %s" % (p["id"], p["quant"])


@case("推荐: 8GB 显存 -> 8B 级")
def t_recommend_8gb():
    import model_advisor

    hw = {"os": "Windows", "cpu": "test", "cores": 8, "ram_gb": 32.0,
          "gpus": [{"name": "NVIDIA GeForce RTX 4060", "vram_gb": 8.0, "kind": "nvidia"}],
          "primary_gpu": {"name": "NVIDIA GeForce RTX 4060", "vram_gb": 8.0, "kind": "nvidia"},
          "vram_gb": 8.0, "unified_memory": False, "class": "mainstream"}
    p = model_advisor.recommend(hw)["primary"]
    expect(7 <= p["params_b"] <= 14, "8GB 档推荐参数规模异常: %rB" % p["params_b"])
    expect(p["min_vram_gb"] <= 9.5, "8GB 档超了显存: %rGB" % p["min_vram_gb"])
    return "8GB -> %s %s" % (p["id"], p["quant"])


@case("推荐: 16GB 显存 -> 本仓库实测的 27B UD-IQ4_XS")
def t_recommend_16gb_regression():
    import model_advisor

    hw = {"os": "Windows", "cpu": "test", "cores": 16, "ram_gb": 32.0,
          "gpus": [{"name": "NVIDIA GeForce RTX 5080", "vram_gb": 16.0, "kind": "nvidia"}],
          "primary_gpu": {"name": "NVIDIA GeForce RTX 5080", "vram_gb": 16.0, "kind": "nvidia"},
          "vram_gb": 16.0, "unified_memory": False, "class": "high"}
    rec = model_advisor.recommend(hw)
    p = rec["primary"]
    expect(p["id"] == "huihui-27b" and p["quant"] == "UD-IQ4_XS",
           "16GB 档没有回到作者实测组合: %r %r" % (p["id"], p["quant"]))
    expect(p["tested"] is True, "16GB 档首选应是 tested=true 的模型")
    expect(rec["suggested_num_ctx"] >= 12288, "16GB 档 ctx 建议过小: %r" % rec["suggested_num_ctx"])
    expect("model_advisor.py --download" in rec["download_command"], "下载命令不对")
    return "16GB -> %s %s ctx=%d" % (p["id"], p["quant"], rec["suggested_num_ctx"])


@case("推荐: 24GB+ 显存 -> 27B/32B 级")
def t_recommend_24gb():
    import model_advisor

    hw = {"os": "Linux", "cpu": "test", "cores": 16, "ram_gb": 64.0,
          "gpus": [{"name": "NVIDIA GeForce RTX 4090", "vram_gb": 24.0, "kind": "nvidia"}],
          "primary_gpu": {"name": "NVIDIA GeForce RTX 4090", "vram_gb": 24.0, "kind": "nvidia"},
          "vram_gb": 24.0, "unified_memory": False, "class": "flagship"}
    p = model_advisor.recommend(hw)["primary"]
    expect(p["params_b"] >= 27, "24GB 档推荐了太小模型: %rB" % p["params_b"])
    return "24GB -> %s %s" % (p["id"], p["quant"])


@case("推荐: prefer_uncensored 开关生效")
def t_prefer_uncensored():
    import model_advisor

    hw = {"os": "Windows", "cpu": "test", "cores": 8, "ram_gb": 32.0,
          "gpus": [{"name": "NVIDIA GeForce RTX 4060", "vram_gb": 8.0, "kind": "nvidia"}],
          "primary_gpu": {"name": "NVIDIA GeForce RTX 4060", "vram_gb": 8.0, "kind": "nvidia"},
          "vram_gb": 8.0, "unified_memory": False, "class": "mainstream"}
    on = model_advisor.recommend(hw, prefer_uncensored=True)["primary"]
    off = model_advisor.recommend(hw, prefer_uncensored=False)["primary"]
    expect(on["abliterated"] is True, "prefer_uncensored=True 时没优先 abliterated: %r" % on["id"])
    expect(off["abliterated"] is False, "prefer_uncensored=False 时没优先官方模型: %r" % off["id"])
    return "on=%s off=%s" % (on["id"], off["id"])


@case("推荐: 装不下时 fallback 不崩溃且有 warning")
def t_fallback():
    import model_advisor

    hw = {"os": "Windows", "cpu": "test", "cores": 2, "ram_gb": 4.0,
          "gpus": [], "primary_gpu": {"name": "none", "vram_gb": 0, "kind": "unknown"},
          "vram_gb": 0.0, "unified_memory": False, "class": "cpu"}
    rec = model_advisor.recommend(hw)
    expect(rec["fallback"] is True, "极小内存应触发 fallback")
    expect(rec["warnings"], "fallback 时应有 warning")
    expect(rec["primary"]["file"].endswith(".gguf"), "fallback 的 primary 不完整")
    return "fallback -> %s" % rec["primary"]["id"]


@case("推荐: num_ctx 随显存单调不减")
def t_ctx_monotonic():
    import model_advisor

    last = 0
    for vram in (4.0, 8.0, 12.0, 16.0, 24.0, 48.0):
        hw = {"os": "Linux", "cpu": "test", "cores": 8, "ram_gb": 64.0,
              "gpus": [{"name": "NVIDIA GPU", "vram_gb": vram, "kind": "nvidia"}],
              "primary_gpu": {"name": "NVIDIA GPU", "vram_gb": vram, "kind": "nvidia"},
              "vram_gb": vram, "unified_memory": False,
              "class": "flagship" if vram >= 24 else "high"}
        ctx = model_advisor.recommend(hw)["suggested_num_ctx"]
        expect(ctx >= last, "ctx 随显存下降: %rGB -> %r（上一个 %r）" % (vram, ctx, last))
        last = ctx
    return "ctx 单调：4GB..48GB 最后一档 %d" % last


@case("检测: detect_hardware 返回结构合法（无 GPU 也允许）")
def t_detect():
    import model_advisor

    hw = model_advisor.detect_hardware()
    expect(isinstance(hw, dict), "detect_hardware 没返回 dict")
    for key in ("os", "cpu", "ram_gb", "gpus", "primary_gpu", "vram_gb", "class"):
        expect(key in hw, "缺字段 %r" % key)
    expect(isinstance(hw["gpus"], list), "gpus 不是 list")
    expect(isinstance(hw["ram_gb"], (int, float)), "ram_gb 不是数字")
    expect(hw["class"] in ("flagship", "high", "mainstream", "entry", "integrated", "cpu"),
           "class 取值异常: %r" % hw["class"])
    return "%s / %s / %.1fGB RAM / %s" % (hw["os"], hw["cpu"][:32], hw["ram_gb"], hw["class"])


@case("CLI: --json 输出合法 JSON 且 ok=true")
def t_cli_json():
    rc, out = run_cli([os.path.join(SCRIPTS, "model_advisor.py"), "--json"])
    expect(rc == 0, "--json 退出码 %r\n%s" % (rc, out[-800:]))
    try:
        data = json.loads(out)
    except Exception:
        raise AssertionError("--json 输出不是合法 JSON:\n%s" % out[-800:])
    expect(data.get("ok") is True, "ok 不是 true")
    expect("primary" in data.get("recommendation", {}), "recommendation.primary 缺失")
    expect("hardware" in data and "class" in data["hardware"], "hardware 缺失")
    return "ok=true, primary=%s" % data["recommendation"]["primary"]["id"]


@case("CLI: --list 列出全部候选")
def t_cli_list():
    rc, out = run_cli([os.path.join(SCRIPTS, "model_advisor.py"), "--list"])
    expect(rc == 0, "--list 退出码 %r" % rc)
    expect("huihui-27b" in out and "Qwen3 8B" in out, "--list 输出缺模型")
    expect("model_catalog.json" in out, "--list 没提示可编辑目录")
    return "列出候选 OK"


@case("CLI: --download --dry-run 不联网不落盘")
def t_cli_download_dryrun():
    rc, out = run_cli([os.path.join(SCRIPTS, "model_advisor.py"),
                       "--download", "huihui-27b", "--quant", "UD-IQ4_XS", "--dry-run"])
    expect(rc == 0, "dry-run 退出码 %r\n%s" % (rc, out[-800:]))
    expect("download_model.py" in out, "没调用下载器")
    expect("dry-run" in out.lower(), "没有 dry-run 提示")
    return "dry-run OK"


@case("下载器: sha256_file 与 hashlib 一致；--dry-run 退出码 0")
def t_downloader():
    import download_model

    payload = b"qwensese" * 1000
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(payload)
        path = f.name
    try:
        got = download_model.sha256_file(path)
        want = hashlib.sha256(payload).hexdigest()
        expect(got == want, "sha256 不一致: %r != %r" % (got, want))
    finally:
        os.remove(path)
    rc, out = run_cli([os.path.join(SCRIPTS, "download_model.py"),
                       "--repo", "Qwen/Qwen3-8B-GGUF", "--file", "Qwen3-8B-Q4_K_M.gguf",
                       "--dry-run"])
    expect(rc == 0, "下载器 dry-run 退出码 %r\n%s" % (rc, out[-800:]))
    expect("huggingface.co" in out, "dry-run 没打印 URL")
    return "sha256 + dry-run OK"


@case("导入器: --dry-run 生成含 FROM/SYSTEM/PARAMETER 的 Modelfile")
def t_importer_dryrun():
    fake = os.path.join(tempfile.gettempdir(), "qwensese-fake-model.gguf")
    with open(fake, "wb") as f:
        f.write(b"not a real gguf, only for dry-run")
    try:
        rc, out = run_cli([os.path.join(SCRIPTS, "import_model.py"),
                           "--gguf", fake, "--name", "qwensese-test", "--dry-run"])
    finally:
        try:
            os.remove(fake)
        except OSError:
            pass
    expect(rc == 0, "导入器 dry-run 退出码 %r\n%s" % (rc, out[-1200:]))
    for token in ("FROM", "SYSTEM", "PARAMETER num_ctx", "PARAMETER temperature"):
        expect(token in out, "Modelfile 预览缺 %r:\n%s" % (token, out[-800:]))
    expect("ollama create" not in out.lower() or "没有执行" in out, "dry-run 不应真的执行 ollama create")
    return "Modelfile 预览 OK"


@case("脚本: model-advisor.ps1 是纯 ASCII")
def t_ps1_ascii():
    path = os.path.join(SCRIPTS, "model-advisor.ps1")
    expect(os.path.isfile(path), "缺少 scripts/model-advisor.ps1")
    raw = open(path, "rb").read()
    bad = [b for b in raw if b > 127]
    expect(not bad, "model-advisor.ps1 含非 ASCII 字节 %d 个（PowerShell 跨代码页会乱码）" % len(bad))
    text = raw.decode("ascii")
    expect("model_advisor.py" in text, "wrapper 没调用 model_advisor.py")
    return "%d 字节，纯 ASCII" % len(raw)


# ---------------------------------------------------------------- 主流程
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=" * 64)
    print("硬件选型 / 模型工具链测试")
    print("仓库根: %s" % REPO_ROOT)
    print("=" * 64)
    passed = failed = 0
    for name, fn in CASES:
        try:
            detail = fn() or ""
            passed += 1
            print("[PASS] %s" % name)
            if detail:
                print("        %s" % detail)
        except Exception as e:
            failed += 1
            print("[FAIL] %s" % name)
            print("        %s: %s" % (type(e).__name__, e))
    print("-" * 64)
    print("SUMMARY pass=%d fail=%d xfail=0" % (passed, failed))
    if failed:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
