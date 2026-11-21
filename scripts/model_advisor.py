#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qwensese · 硬件检测 + Qwen 模型选型建议（零第三方依赖）

用法（任意平台）：
    python scripts/model_advisor.py                 # 检测硬件并打印推荐
    python scripts/model_advisor.py --json          # 机器可读输出（Web 端 /api/hardware 用它）
    python scripts/model_advisor.py --list          # 列出目录里所有候选模型
    python scripts/model_advisor.py --check         # 检查目录里的 HuggingFace 文件是否存在（需要联网）
    python scripts/model_advisor.py --download qwen3-8b --quant Q4_K_M
    python scripts/model_advisor.py --download huihui-27b --dry-run

设计要点：
- 只使用 Python 标准库；检测失败时降级到 CPU 模式，不抛异常。
- 目录数据在 scripts/model_catalog.json，方便手动增删（不用改代码）。
- 推荐只是起点：真正速度取决于量化、上下文长度、驱动与 Ollama 版本。
"""

import argparse
import ctypes
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CATALOG_PATH = os.path.join(HERE, "model_catalog.json")
DOWNLOADER = os.path.join(HERE, "download_model.py")
IMPORTER = os.path.join(HERE, "import_model.py")
GGUF_DIR = os.path.join(ROOT, "models", "gguf")

# ---------------------------------------------------------------- 小工具

def _fix_stdout():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _run(cmd, timeout=10):
    """跑一个外部命令，返回 (ok, stdout_text)。失败/超时/找不到命令都只返回 (False, '')。"""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except Exception:
        return False, ""
    text = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode == 0, text


def _gib(nbytes):
    return round(float(nbytes) / (1024 ** 3), 1)


# ---------------------------------------------------------------- 硬件检测

def _ram_gb():
    system = platform.system()
    try:
        if system == "Windows":
            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            st = _MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return _gib(st.ullTotalPhys)
        elif system == "Linux":
            with open("/proc/meminfo", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = re.match(r"MemTotal:\s+(\d+)\s+kB", line)
                    if m:
                        return round(int(m.group(1)) / (1024 ** 2), 1)
        elif system == "Darwin":
            ok, out = _run(["sysctl", "-n", "hw.memsize"])
            if ok and out.strip().isdigit():
                return _gib(int(out.strip()))
    except Exception:
        pass
    return 0.0


def _cpu_name():
    system = platform.system()
    try:
        if system == "Windows":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        if system == "Linux":
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        if system == "Darwin":
            ok, out = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
            if ok:
                return out.strip()
    except Exception:
        pass
    return platform.processor() or platform.machine() or "未知 CPU"


def _kind_of(name):
    low = (name or "").lower()
    if any(k in low for k in ("nvidia", "geforce", "rtx", "gtx", "quadro", "tesla", "a100", "h100")):
        return "nvidia"
    if any(k in low for k in ("amd", "radeon", "rx ", "vega", "instinct")):
        return "amd"
    if any(k in low for k in ("intel", "arc ", "iris", "uhd graphics")):
        return "intel"
    if "apple" in low or "m1" in low or "m2" in low or "m3" in low or "m4" in low:
        return "apple"
    return "unknown"


def _gpus_nvidia_smi():
    ok, text = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    out = []
    if ok:
        for line in text.splitlines():
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 2 and parts[0]:
                try:
                    vram = round(float(parts[1]) / 1024.0, 1)
                except ValueError:
                    vram = 0.0
                out.append({"name": parts[0], "vram_gb": vram, "kind": "nvidia"})
    return out


def _gpus_windows():
    """优先 nvidia-smi（最准），其次注册表 qwMemorySize（字节），失败再退回 WMI。"""
    smi = _gpus_nvidia_smi()
    if smi:
        return smi
    out = []
    try:
        import winreg
        base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if not re.match(r"^\d{4}$", sub):
                    continue
                try:
                    with winreg.OpenKey(root, sub) as k:
                        name = str(winreg.QueryValueEx(k, "DriverDesc")[0])
                        if "microsoft basic" in name.lower():
                            continue
                        vram_bytes = 0
                        for value_name in ("HardwareInformation.qwMemorySize",
                                           "HardwareInformation.qw_memorySize"):
                            try:
                                raw = winreg.QueryValueEx(k, value_name)[0]
                                vram_bytes = int(raw if not isinstance(raw, (bytes, bytearray))
                                                 else int.from_bytes(raw, "little"))
                                break
                            except OSError:
                                continue
                        out.append({"name": name, "vram_gb": _gib(vram_bytes) if vram_bytes else 0.0,
                                    "kind": _kind_of(name)})
                except OSError:
                    continue
    except Exception:
        out = []
    if out:
        return out
    try:
        ok, text = _run(["powershell", "-NoProfile", "-Command",
                         "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json -Compress"],
                        timeout=20)
        if ok and text.strip().startswith(("{", "[")):
            data = json.loads(text.strip())
            if isinstance(data, dict):
                data = [data]
            for item in data:
                name = item.get("Name") or ""
                ram = item.get("AdapterRAM") or 0
                if "microsoft basic" in name.lower() or not name:
                    continue
                out.append({"name": name, "vram_gb": _gib(ram) if ram else 0.0, "kind": _kind_of(name)})
    except Exception:
        pass
    return out


def _gpus_linux():
    out = []
    # 1) NVIDIA
    smi = _gpus_nvidia_smi()
    if smi:
        return smi
    # 2) AMD / 其他：读 sysfs 的 VRAM 总量
    for path in sorted(glob.glob("/sys/class/drm/card[0-9]*/device/mem_info_vram_total")):
        try:
            with open(path, encoding="utf-8") as f:
                vram = _gib(int(f.read().strip()))
        except Exception:
            continue
        vendor = ""
        for vf in ("vendor", "uevent"):
            try:
                with open(os.path.join(os.path.dirname(path), vf), encoding="utf-8", errors="replace") as f:
                    vendor += f.read()
            except Exception:
                pass
        name = "AMD GPU" if "0x1002" in vendor else ("NVIDIA GPU" if "0x10de" in vendor else "GPU")
        out.append({"name": name, "vram_gb": vram, "kind": _kind_of(name)})
    if out:
        return out
    ok, text = _run(["lspci"])
    for line in text.splitlines():
        if re.search(r"(vga|3d|display)", line, re.I):
            name = line.split(":", 2)[-1].strip()
            out.append({"name": name, "vram_gb": 0.0, "kind": _kind_of(name)})
            break
    return out


def _gpus_macos():
    ok, text = _run(["system_profiler", "SPDisplaysDataType", "-json"], timeout=25)
    if not ok:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    out = []
    for item in data.get("SPDisplaysDataType", []) or []:
        name = item.get("sppci_model") or item.get("_name") or "GPU"
        vram = 0.0
        for key in ("spdisplays_vram", "spdisplays_vram_shared", "spdisplays_vram_total"):
            raw = item.get(key)
            if raw:
                m = re.search(r"([\d.]+)\s*(MB|GB)", str(raw), re.I)
                if m:
                    val = float(m.group(1))
                    vram = val / 1024.0 if m.group(2).lower() == "mb" else val
                    break
        out.append({"name": name, "vram_gb": round(vram, 1), "kind": _kind_of(name)})
    return out


def detect_hardware():
    """返回硬件信息；任何异常都不抛出，检测不到就降级。"""
    system = platform.system() or "未知系统"
    gpus = []
    try:
        if system == "Windows":
            gpus = _gpus_windows()
        elif system == "Linux":
            gpus = _gpus_linux()
        elif system == "Darwin":
            gpus = _gpus_macos()
    except Exception:
        gpus = []

    ram = _ram_gb()
    cpu = _cpu_name()

    # Apple Silicon：显存与内存统一，按可用内存估算
    for gpu in gpus:
        if gpu["kind"] == "apple" and gpu["vram_gb"] <= 0 and ram > 0:
            gpu["vram_gb"] = round(ram * 0.7, 1)
            gpu["unified"] = True

    gpus = [g for g in gpus if g.get("name")]
    if gpus:
        # 优先独显（显存最大），核显作为备选列在后面
        gpus.sort(key=lambda g: (g["kind"] not in ("nvidia", "amd", "apple"), g["vram_gb"]), reverse=False)
        best = max(gpus, key=lambda g: g["vram_gb"])
    else:
        best = {"name": "未检测到可用 GPU", "vram_gb": 0.0, "kind": "unknown"}

    kind = best["kind"]
    vram = float(best["vram_gb"] or 0.0)
    if kind in ("intel", "unknown") and vram <= 0:
        cls = "cpu"
    elif vram >= 20:
        cls = "flagship"
    elif vram >= 12:
        cls = "high"
    elif vram >= 8:
        cls = "mainstream"
    elif vram >= 4:
        cls = "entry"
    else:
        cls = "integrated"

    return {
        "os": system,
        "os_version": platform.version(),
        "cpu": cpu,
        "cores": os.cpu_count() or 0,
        "ram_gb": ram,
        "gpus": gpus,
        "primary_gpu": best,
        "vram_gb": vram,
        "unified_memory": bool(best.get("unified")),
        "class": cls,
    }


# ---------------------------------------------------------------- 目录与推荐

CLASS_LABEL = {
    "flagship": "旗舰（≥20GB 显存）",
    "high": "高性能（12–20GB 显存）",
    "mainstream": "主流（8–12GB 显存）",
    "entry": "入门（4–8GB 显存）",
    "integrated": "轻量/核显（<4GB 显存）",
    "cpu": "纯 CPU",
}

# 8B 模型在各档 GPU 上大致的生成速度（tok/s），按参数规模开方缩放
SPEED_BASE = {
    "flagship": (70, 110),
    "high": (45, 70),
    "mainstream": (30, 50),
    "entry": (18, 32),
    "integrated": (6, 12),
    "cpu": (3, 8),
}

# 显存（GB）→ 建议上下文。阈值略低于标称值，因为驱动上报的可用显存通常少一点。
CTX_BY_VRAM = [
    (31, 24576), (22, 16384), (15, 12288), (11, 8192),
    (7, 6144), (4, 4096), (0, 4096),
]


def load_catalog(path=None):
    with open(path or CATALOG_PATH, encoding="utf-8") as f:
        return json.load(f)


def catalog_candidates(catalog):
    """把目录展开成 (model, quant) 候选列表。"""
    out = []
    for model in catalog.get("models", []):
        for q in model.get("quants", []):
            out.append({
                "id": model["id"],
                "label": model.get("label") or model["id"],
                "family": model.get("family", "official"),
                "abliterated": bool(model.get("abliterated")),
                "repo": model["repo"],
                "params_b": float(model.get("params_b") or 0),
                "tested": bool(model.get("tested")),
                "unstable": bool(model.get("unstable")),
                "roleplay": model.get("roleplay") or "",
                "quant": q["quant"],
                "file": q["file"],
                "size_gb": float(q["size_gb"]),
                "min_vram_gb": float(q["min_vram_gb"]),
                "quality": float(q.get("quality") or 0.92),
            })
    return out


def _speed_class(hw):
    if hw["class"] == "cpu":
        return "cpu"
    if hw["class"] == "integrated":
        return "integrated"
    return hw["class"]


def recommend(hw, catalog=None, prefer_uncensored=True, reserve_gb=0.8):
    """根据硬件给出推荐（纯函数，方便测试）。返回 dict。"""
    catalog = catalog or load_catalog()
    cands = catalog_candidates(catalog)
    vram = float(hw.get("vram_gb") or 0.0)
    ram = float(hw.get("ram_gb") or 0.0)
    cls = hw.get("class") or "cpu"
    unified = bool(hw.get("unified_memory"))

    usable_vram = max(0.0, vram - (0.3 if cls != "cpu" else reserve_gb))
    usable_ram = max(0.0, ram * (0.6 if unified else 0.5))

    def fits(c):
        if cls == "cpu":
            # 纯 CPU：内存要装得下模型 + 系统开销；且 8B 以上在 CPU 上慢到不可用
            return c["size_gb"] + 4 <= ram and c["params_b"] <= 8
        if unified:
            return c["size_gb"] + 2 <= usable_ram
        # min_vram_gb 是"含默认上下文"的保守门槛，给 1GB 容差避免把刚好够用的卡判死
        return c["min_vram_gb"] <= usable_vram + 1.0

    speed_cls = _speed_class(hw)
    base_lo, base_hi = SPEED_BASE[speed_cls]

    def score(c):
        # 质量（参数规模 × 量化质量），扣一点大模型在弱卡上的速度惩罚
        s = (c["params_b"] ** 0.35) * c["quality"]
        if prefer_uncensored and c["abliterated"]:
            s *= 1.08
        if speed_cls in ("entry", "integrated"):
            s *= 1.0 / (1.0 + max(0.0, c["params_b"] - 8) / 10.0)
        elif speed_cls == "mainstream":
            s *= 1.0 / (1.0 + max(0.0, c["params_b"] - 14) / 18.0)
        elif speed_cls == "high":
            s *= 1.0 / (1.0 + max(0.0, c["params_b"] - 24) / 30.0)
        return s

    fitted = [c for c in cands if fits(c)]
    fallback = False
    if not fitted:
        # 一个都装不下：挑内存/显存装得下的最小模型，并标记降级
        fallback = True
        fitted = sorted(cands, key=lambda c: c["size_gb"])[:1]

    primary = max(fitted, key=score)

    def speed_text(c):
        factor = max(0.4, (8.0 / max(1.0, c["params_b"])) ** 0.55)
        lo = max(1, int(base_lo * factor))
        hi = max(lo + 1, int(base_hi * factor))
        return "%d–%d tok/s（粗估）" % (lo, hi)

    def brief(c):
        return {
            "id": c["id"],
            "label": c["label"],
            "quant": c["quant"],
            "file": c["file"],
            "repo": c["repo"],
            "size_gb": c["size_gb"],
            "min_vram_gb": c["min_vram_gb"],
            "abliterated": c["abliterated"],
            "tested": c["tested"],
            "unstable": c["unstable"],
            "params_b": c["params_b"],
            "speed_hint": speed_text(c),
            "roleplay": c["roleplay"],
        }

    # 备选：更快（同档里参数小一些但分数最高）与更强（参数更大）
    faster_pool = [c for c in fitted
                   if primary["params_b"] * 0.4 <= c["params_b"] <= primary["params_b"] * 0.85]
    faster = max(faster_pool, key=score) if faster_pool else None
    stronger_pool = [c for c in fitted if c["params_b"] > primary["params_b"]]
    stronger = max(stronger_pool, key=lambda c: (c["params_b"], c["quality"])) if stronger_pool else None
    ctx = 4096
    ctx_base = vram if cls != "cpu" and not unified else usable_ram
    for threshold, value in CTX_BY_VRAM:
        if ctx_base >= threshold:
            ctx = value
            break

    warnings = []
    if fallback:
        warnings.append("当前硬件可能装不下目录里任何一款模型，下面只是最小可用项；建议先加内存/换卡，或用 OLLAMA 的 CPU 模式跑 4B。")
    if cls == "cpu":
        warnings.append("未检测到独立显卡：CPU 推理只有 3–8 tok/s，建议选 Qwen3 4B 并拉长等待时间。")
    if primary["size_gb"] + 1.5 > usable_vram and cls != "cpu" and not unified:
        warnings.append("推荐模型基本占满显存：请把上下文降到 %d 或者换更小的量化，否则会掉速。" % max(4096, ctx // 2))

    def _alt(cand, prim, brief_fn):
        if not cand:
            return None
        if cand["id"] == prim["id"] and cand["quant"] == prim["quant"]:
            return None
        return brief_fn(cand)

    download_cmd = "python scripts/model_advisor.py --download %s --quant %s" % (primary["id"], primary["quant"])
    import_cmd = "python scripts/import_model.py --gguf models/gguf/%s --name qwensese-%s --num-ctx %d" % (
        primary["file"], re.sub(r"[^a-z0-9]+", "-", primary["id"]).strip("-"), ctx)

    return {
        "primary": brief(primary),
        "faster_alternative": _alt(faster, primary, brief),
        "quality_alternative": _alt(stronger, primary, brief),
        "suggested_num_ctx": ctx,
        "class_label": CLASS_LABEL.get(cls, cls),
        "usable_vram_gb": round(usable_vram, 1) if cls != "cpu" else 0.0,
        "fallback": fallback,
        "warnings": warnings,
        "download_command": download_cmd,
        "import_command": import_cmd,
        "windows_commands": [
            "powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\model-advisor.ps1 -Download %s -Quant %s"
            % (primary["id"], primary["quant"]),
            "powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\import-model.ps1 -Name qwensese-%s -GgufPath models\\gguf\\%s -NumCtx %d"
            % (re.sub(r"[^a-z0-9]+", "-", primary["id"]).strip("-"), primary["file"], ctx),
        ],
        "gpu": {
            "name": hw.get("primary_gpu", {}).get("name", ""),
            "vram_gb": vram,
            "class": cls,
        },
        "detected": {
            "os": hw.get("os"),
            "cpu": hw.get("cpu"),
            "ram_gb": ram,
            "cores": hw.get("cores"),
        },
    }


def advisor_payload():
    """给 Web 端用的默认载荷（检测 + 推荐）。"""
    hw = detect_hardware()
    rec = recommend(hw)
    return {"ok": True, "hardware": hw, "recommendation": rec,
            "catalog_version": load_catalog().get("schema")}


# ---------------------------------------------------------------- 联网检查

def _hf_siblings(repo, timeout=25):
    url = "https://huggingface.co/api/models/%s?blobs=true" % repo
    req = urllib.request.Request(url, headers={"User-Agent": "qwensese-model-advisor"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def check_catalog(catalog):
    rows = []
    cache = {}
    for model in catalog.get("models", []):
        repo = model["repo"]
        if repo not in cache:
            try:
                cache[repo] = _hf_siblings(repo)
            except Exception as e:
                cache[repo] = e
        meta = cache[repo]
        for q in model.get("quants", []):
            row = {"id": model["id"], "quant": q["quant"], "file": q["file"], "status": "", "sha256": "", "real_size_gb": None}
            if isinstance(meta, Exception):
                row["status"] = "无法检查（网络受限：%s）" % type(meta).__name__
            else:
                files = {s["rfilename"]: s for s in meta.get("siblings", [])}
                sib = files.get(q["file"])
                if sib:
                    row["status"] = "存在"
                    lfs = sib.get("lfs") or {}
                    row["sha256"] = lfs.get("oid") or sib.get("oid") or ""
                    size = lfs.get("size") or sib.get("size")
                    if size:
                        row["real_size_gb"] = _gib(size)
                else:
                    row["status"] = "文件不存在（目录名可能已变动）"
            rows.append(row)
    return rows


# ---------------------------------------------------------------- 输出

def format_report(hw, rec):
    lines = []
    gpu = hw.get("primary_gpu") or {}
    name = gpu.get("name") or "未检测到 GPU"
    vram = hw.get("vram_gb") or 0
    lines.append("=" * 62)
    lines.append("qwensese · 硬件检测与模型选型")
    lines.append("=" * 62)
    lines.append("系统   : %s %s" % (hw.get("os"), hw.get("os_version") or ""))
    lines.append("CPU    : %s（%d 核）" % (hw.get("cpu"), hw.get("cores") or 0))
    lines.append("内存   : %.1f GB" % (hw.get("ram_gb") or 0))
    if hw.get("gpus"):
        for g in hw["gpus"]:
            lines.append("GPU    : %s（%s，显存 %.1f GB）" % (g["name"], g["kind"], g.get("vram_gb") or 0))
    else:
        lines.append("GPU    : 未检测到")
    lines.append("档位   : %s" % rec["class_label"])
    if hw.get("unified_memory"):
        lines.append("备注   : 统一内存（显存=内存），推荐按可用内存折算")
    lines.append("")
    p = rec["primary"]
    lines.append("推荐   : %s · %s" % (p["label"], p["quant"]))
    lines.append("        约 %.1f GB 文件，最低 %.1f GB 显存；%s" % (p["size_gb"], p["min_vram_gb"], p["speed_hint"]))
    lines.append("        用途：%s" % (p["roleplay"] or "-"))
    if p["tested"]:
        lines.append("        本仓库作者实测过该组合")
    if p["unstable"]:
        lines.append("        注意：上游仓库/文件名可能变动，下载前先跑 --check")
    lines.append("上下文 : 建议 num_ctx = %d" % rec["suggested_num_ctx"])
    if rec.get("faster_alternative"):
        a = rec["faster_alternative"]
        lines.append("更快备选: %s · %s（%.1f GB，%s）" % (a["label"], a["quant"], a["size_gb"], a["speed_hint"]))
    if rec.get("quality_alternative"):
        a = rec["quality_alternative"]
        lines.append("更强备选: %s · %s（%.1f GB，%s）" % (a["label"], a["quant"], a["size_gb"], a["speed_hint"]))
    for w in rec.get("warnings") or []:
        lines.append("注意   : %s" % w)
    lines.append("")
    lines.append("下载   : %s" % rec["download_command"])
    lines.append("导入   : %s" % rec["import_command"])
    lines.append("Windows: %s" % rec["windows_commands"][0])
    lines.append("")
    lines.append("说明   : 速度为粗略估算；实际受量化、上下文、驱动、Ollama 版本影响。")
    lines.append("         先 --check 联网确认文件存在，再下载（默认带 SHA256 校验）。")
    return "\n".join(lines)


def format_list(catalog):
    lines = ["目录里的候选模型（tested=作者实测过，abl=无审查/abliterated）：", ""]
    for model in catalog.get("models", []):
        lines.append("%-12s %-28s abliterated=%-5s tested=%s" % (
            model["id"], (model.get("label") or "")[:28], str(model.get("abliterated")), str(model.get("tested"))))
        for q in model.get("quants", []):
            lines.append("    %-10s %-58s %5.1f GB  需≥%4.1f GB 显存" % (
                q["quant"], q["file"], q["size_gb"], q["min_vram_gb"]))
    lines.append("")
    lines.append("手动增删：编辑 scripts/model_catalog.json，然后 --check 验证文件是否存在。")
    return "\n".join(lines)


def format_check(rows):
    lines = ["HuggingFace 文件检查结果：", ""]
    ok = 0
    for r in rows:
        status = r["status"]
        extra = ""
        if r["real_size_gb"]:
            extra = "  实际 %.1f GB  sha256=%s…" % (r["real_size_gb"], r["sha256"][:12])
        lines.append("[%s] %-12s %-10s %s%s" % ("OK " if status == "存在" else "!! ", r["id"], r["quant"], status, extra))
        if status == "存在":
            ok += 1
    lines.append("")
    lines.append("存在 %d / %d 个文件" % (ok, len(rows)))
    if ok < len(rows):
        lines.append("提示：不存在的条目请更新 scripts/model_catalog.json 里的 repo / file。")
    return "\n".join(lines)


def main(argv=None):
    _fix_stdout()
    ap = argparse.ArgumentParser(description="根据显存与显卡性能推荐 Qwen GGUF 模型")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--list", action="store_true", help="列出目录候选模型")
    ap.add_argument("--check", action="store_true", help="联网检查目录里的文件是否存在")
    ap.add_argument("--download", metavar="ID", help="下载推荐/指定模型（调用 download_model.py）")
    ap.add_argument("--quant", default="", help="与 --download 搭配的量化名")
    ap.add_argument("--dest", default=GGUF_DIR, help="GGUF 保存目录（默认 models/gguf）")
    ap.add_argument("--no-uncensored", action="store_true", help="优先官方有审查模型")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要执行的下载命令")
    args = ap.parse_args(argv)

    catalog = load_catalog()

    if args.list:
        print(format_list(catalog))
        return 0

    if args.check:
        print(format_check(check_catalog(catalog)))
        return 0

    hw = detect_hardware()
    rec = recommend(hw, catalog, prefer_uncensored=not args.no_uncensored)

    if args.download:
        cands = [c for c in catalog_candidates(catalog) if c["id"] == args.download]
        if not cands:
            print("目录里没有 id=%r 的模型，先 --list 看看可选值" % args.download, file=sys.stderr)
            print(format_list(catalog), file=sys.stderr)
            return 2
        if args.quant:
            cands = [c for c in cands if c["quant"].lower() == args.quant.lower()]
            if not cands:
                print("该模型没有量化 %r，可用：%s" % (
                    args.quant, ", ".join(c["quant"] for c in catalog_candidates(catalog) if c["id"] == args.download)),
                    file=sys.stderr)
                return 2
        chosen = cands[0] if not args.quant else cands[0]
        cmd = [sys.executable or "python3", DOWNLOADER, "--repo", chosen["repo"], "--file", chosen["file"],
               "--dest", args.dest]
        print("下载 %s · %s → %s" % (chosen["label"], chosen["quant"], os.path.join(args.dest, chosen["file"])))
        print("命令: %s" % " ".join(cmd))
        if args.dry_run:
            print("[dry-run] 不实际执行；下载器默认带 SHA256 校验与断点续传。")
            return 0
        rc = subprocess.call(cmd)
        if rc != 0:
            print("下载失败（rc=%d）。若仓库/文件名已变动，编辑 scripts/model_catalog.json 后重试。" % rc, file=sys.stderr)
            return rc
        print("")
        print("下一步导入 Ollama：")
        print("  python scripts/import_model.py --gguf %s --name qwensese-%s --num-ctx %d" % (
            os.path.join(args.dest, chosen["file"]),
            re.sub(r"[^a-z0-9]+", "-", chosen["id"]).strip("-"), rec["suggested_num_ctx"]))
        print("  Windows: powershell -File scripts\\import-model.ps1 -Name qwensese-%s -GgufPath %s" % (
            re.sub(r"[^a-z0-9]+", "-", chosen["id"]).strip("-"), os.path.join(args.dest, chosen["file"])))
        return 0

    if args.json:
        print(json.dumps({"ok": True, "hardware": hw, "recommendation": rec}, ensure_ascii=False, indent=2))
        return 0

    print(format_report(hw, rec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
