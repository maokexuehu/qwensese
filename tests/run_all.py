#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次跑完 tests/ 下的全部测试，汇总 PASS/FAIL 并给出退出码。

用法：
    python tests/run_all.py                 # 布局检查 + 端到端 API 测试
    python tests/run_all.py --skip-api      # 只跑布局/合规检查（不启动服务）
    python tests/run_all.py --strict        # 布局检查把"发布元数据缺失"也算 FAIL
    python tests/run_all.py --only test_api # 只跑某一个（test_layout / test_api）

退出码：0 = 全部通过且 FAIL 数为 0；1 = 有失败。
"""

import argparse
import os
import re
import subprocess
import sys
import time

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
SUMMARY_RE = re.compile(r"SUMMARY\s+pass=(\d+)\s+fail=(\d+)\s+xfail=(\d+)")


def run_one(script, strict=False):
    """用同一 Python 解释器跑一个测试文件，实时打印并返回 (rc, pass, fail, xfail)。"""
    cmd = [sys.executable, os.path.join(TESTS_DIR, script)]
    if strict and script == "test_layout.py":
        cmd.append("--strict")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    print("=" * 70)
    print(">> %s" % " ".join([os.path.basename(sys.executable)] + cmd[1:]))
    print("=" * 70)
    start = time.time()
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.time() - start
    out = (proc.stdout or "") + (proc.stderr or "")
    print(out.rstrip())
    print("-" * 70)
    print("<< %s 退出码=%d 用时=%.1fs" % (script, proc.returncode, elapsed))

    m = None
    for m in SUMMARY_RE.finditer(out):
        pass  # 取最后一处 SUMMARY
    if m:
        return proc.returncode, int(m.group(1)), int(m.group(2)), int(m.group(3))
    return proc.returncode, 0, 1 if proc.returncode else 0, 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="跑 qwensese 的全部测试")
    parser.add_argument("--skip-api", action="store_true",
                        help="跳过 test_api.py（不启动真实服务，只跑布局检查）")
    parser.add_argument("--strict", action="store_true",
                        help="传给 test_layout.py：发布元数据文件缺失也算 FAIL")
    parser.add_argument("--only", choices=["test_layout", "test_advisor", "test_api"],
                        help="只跑指定的一个测试文件")
    args = parser.parse_args()

    scripts = ["test_layout.py", "test_advisor.py", "test_api.py"]
    if args.skip_api or args.only == "test_layout":
        scripts = ["test_layout.py", "test_advisor.py"]
    if args.only:
        scripts = [args.only + ".py"]

    total_pass = total_fail = total_xfail = 0
    failed_suites = []
    started = time.time()

    for script in scripts:
        rc, p, f, x = run_one(script, strict=args.strict)
        total_pass += p
        total_fail += f
        total_xfail += x
        if rc != 0 or f > 0:
            failed_suites.append("%s(退出码=%d, FAIL=%d)" % (script, rc, f))
        print()

    print("=" * 70)
    print("run_all 汇总")
    print("  测试文件 : %s" % ", ".join(scripts))
    print("  PASS     : %d" % total_pass)
    print("  FAIL     : %d" % total_fail)
    print("  XFAIL    : %d（已知偏差/缺陷，不计 FAIL）" % total_xfail)
    print("  总用时   : %.1fs" % (time.time() - started))
    if failed_suites:
        print("  RESULT   : FAIL  -> %s" % "; ".join(failed_suites))
        return 1
    print("  RESULT   : ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
