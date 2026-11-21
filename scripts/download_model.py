#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qwensese · 跨平台 GGUF 下载器（断点续传 + SHA256 校验）

用法：
    python scripts/download_model.py --repo Qwen/Qwen3-8B-GGUF --file Qwen3-8B-Q4_K_M.gguf
    python scripts/download_model.py --repo huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF \
        --file Huihui-Qwen3.8-27B-abliterated-UD-IQ4_XS.gguf --dest models/gguf
    python scripts/download_model.py ... --sha256 <64位hex>     # 手工指定校验值
    python scripts/download_model.py ... --no-verify             # 跳过校验（不推荐）
    python scripts/download_model.py ... --dry-run

校验来源优先级：
1) --sha256 显式给出；
2) 从 HuggingFace 模型元数据读取 LFS 的 sha256（联网可用时）；
3) 都没有 → 打印警告并跳过（仍会在文件旁写 .sha256 供复查）。
"""

import argparse
import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_DEST = os.path.join(ROOT, "models", "gguf")
CHUNK = 1024 * 1024
UA = "qwensese-download/1.0"


def _fix_stdout():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _ssl_ctx(insecure=False):
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    try:
        import certifi  # 可选：装了就用 Mozilla 证书包，没装就走系统默认
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _gb(nbytes):
    return float(nbytes) / (1024 ** 3)


def hf_file_info(repo, filename, ctx, timeout=30):
    """从 HuggingFace 元数据里取文件的 sha256 与大小；网络不通时返回 (None, None)。"""
    url = "https://huggingface.co/api/models/%s?blobs=true" % urllib.parse.quote(repo, safe="/")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            meta = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        print("[warn] 无法读取 HuggingFace 元数据（%s），将跳过自动校验" % type(e).__name__)
        return None, None
    for sib in meta.get("siblings", []) or []:
        if sib.get("rfilename") != filename:
            continue
        lfs = sib.get("lfs") or {}
        sha = lfs.get("oid") or sib.get("oid") or ""
        size = lfs.get("size") or sib.get("size") or 0
        return (sha.lower() or None), (int(size) or None)
    print("[warn] 元数据里没有找到 %s（仓库里可能没这个文件名）" % filename)
    return None, None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def download(url, out_path, ctx, expected_size=None, retries=5):
    """带断点续传的下载；返回 final path。"""
    part = out_path + ".part"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    for attempt in range(1, retries + 1):
        start = os.path.getsize(part) if os.path.exists(part) else 0
        if expected_size and start >= expected_size:
            break
        headers = {"User-Agent": UA}
        if start:
            headers["Range"] = "bytes=%d-" % start
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                if start and r.status != 206:
                    # 服务器不支持 Range：从头来过
                    start = 0
                    os.remove(part)
                mode = "ab" if start else "wb"
                total = expected_size
                if not total:
                    m = re.search(r"/(\d+)$", r.headers.get("Content-Range") or "")
                    if m:
                        total = int(m.group(1))
                    elif r.headers.get("Content-Length"):
                        total = start + int(r.headers["Content-Length"])
                done = start
                last_report = 0.0
                with open(part, mode) as f:
                    while True:
                        block = r.read(CHUNK)
                        if not block:
                            break
                        f.write(block)
                        done += len(block)
                        now = time.time()
                        if total and (now - last_report > 1.0 or done >= total):
                            last_report = now
                            pct = min(100.0, done * 100.0 / total)
                            sys.stdout.write("\r[download] %5.1f%%  %.2f / %.2f GB   " % (
                                pct, _gb(done), _gb(total)))
                            sys.stdout.flush()
                if total:
                    sys.stdout.write("\r[download] 100.0%%  %.2f / %.2f GB\n" % (_gb(done), _gb(total)))
                    sys.stdout.flush()
                break
        except Exception as e:
            print("\n[warn] 下载中断（第 %d/%d 次）：%s" % (attempt, retries, type(e).__name__))
            if attempt >= retries:
                raise
            time.sleep(min(30, 3 * attempt))
    if not os.path.exists(part):
        raise RuntimeError("下载结束但临时文件不存在")
    if expected_size and os.path.getsize(part) < expected_size:
        raise RuntimeError("文件不完整：%d / %d 字节，重跑本命令可续传" % (os.path.getsize(part), expected_size))
    os.replace(part, out_path)
    return out_path


def main(argv=None):
    _fix_stdout()
    ap = argparse.ArgumentParser(description="下载 HuggingFace 上的 GGUF，并做 SHA256 校验")
    ap.add_argument("--repo", required=True, help="HuggingFace 仓库，如 owner/repo")
    ap.add_argument("--file", required=True, help="仓库里的 .gguf 文件名")
    ap.add_argument("--dest", default=DEFAULT_DEST, help="保存目录（默认 <repo>/models/gguf）")
    ap.add_argument("--sha256", default="", help="手工指定期望的 SHA256")
    ap.add_argument("--no-verify", action="store_true", help="跳过校验（不推荐）")
    ap.add_argument("--insecure", action="store_true", help="跳过 TLS 证书校验（仅当本机证书链有问题时）")
    ap.add_argument("--retries", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true", help="只打印解析结果，不下载")
    args = ap.parse_args(argv)

    if not re.match(r"^[^/\s]+/[^/\s]+$", args.repo):
        print("[error] --repo 应为 owner/repo 形式", file=sys.stderr)
        return 2
    if not args.file.lower().endswith(".gguf"):
        print("[error] --file 应以 .gguf 结尾", file=sys.stderr)
        return 2

    url = "https://huggingface.co/%s/resolve/main/%s?download=true" % (
        urllib.parse.quote(args.repo, safe="/"), urllib.parse.quote(args.file))
    out_path = os.path.join(args.dest, args.file)
    print("[info] repo   : %s" % args.repo)
    print("[info] file   : %s" % args.file)
    print("[info] target : %s" % out_path)
    print("[info] url    : %s" % url)

    if args.dry_run:
        print("[dry-run] 不下载。")
        return 0

    ctx = _ssl_ctx(args.insecure)
    meta_sha, meta_size = (None, None)
    if not args.no_verify:
        meta_sha, meta_size = hf_file_info(args.repo, args.file, ctx)
    if args.no_verify:
        print("[warn] --no-verify：跳过校验")

    download(url, out_path, ctx, expected_size=meta_size, retries=args.retries)

    actual = sha256_file(out_path)
    sidecar = out_path + ".sha256"
    with open(sidecar, "w", encoding="utf-8", newline="\n") as f:
        f.write(actual + "  " + os.path.basename(out_path) + "\n")
    print("[info] sha256 : %s" % actual)
    print("[info] 校验值已记录: %s" % sidecar)

    expected = (args.sha256 or meta_sha or "").lower()
    if expected:
        if actual == expected:
            print("[ok] SHA256 校验通过")
        else:
            bad = out_path + ".bad"
            try:
                os.replace(out_path, bad)
            except OSError:
                pass
            print("[error] SHA256 不匹配！\n  expected: %s\n  actual  : %s" % (expected, actual), file=sys.stderr)
            print("[error] 文件已改名为 %s，请重下或检查上游仓库是否被替换。" % bad, file=sys.stderr)
            return 3
    elif not args.no_verify:
        print("[warn] 没有拿到期望的 SHA256（离线/元数据不可用）。已记录实际值到 .sha256，"
              "建议稍后联网重跑或用 --sha256 手动核对。")

    print("")
    print("下一步导入 Ollama：")
    print("  python scripts/import_model.py --gguf %s --name qwensese-%s" % (
        out_path, re.sub(r"[^a-z0-9]+", "-", os.path.basename(args.file).split("-")[0].lower())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
