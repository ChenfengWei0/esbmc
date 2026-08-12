#!/usr/bin/env python3
import subprocess
import sys
import importlib.util

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CERTIFY_ALL = ROOT / "notes" / "coverage" / "scripts" / "certify_all.py"

spec = importlib.util.spec_from_file_location("certify_all_for_test", CERTIFY_ALL)
certify_all = importlib.util.module_from_spec(spec)
spec.loader.exec_module(certify_all)


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def run_with(*extra):
    return subprocess.run([
        sys.executable,
        str(CERTIFY_ALL),
        "--subject-dir",
        "/tmp/does-not-exist",
        "--subject-benchmark",
        "peer182",
        "--unit",
        "f",
        *extra,
    ],
                          capture_output=True,
                          text=True)


def test_job_memlimit_gib_does_not_shrink_to_fractional_budget():
    old_available = certify_all.available_gib
    certify_all.available_gib = lambda: 14.0
    try:
        memlimit, refusal = certify_all.job_memlimit_gib(
            1, reserve_frac=0.60, want_gib=8)
    finally:
        certify_all.available_gib = old_available
    return check(
        memlimit == 8 and refusal is None,
        f"certify_all returns requested 8GiB instead of shrinking to "
        f"floor(MemAvailable*fraction): memlimit={memlimit}, refusal={refusal}")


def test_job_memlimit_gib_refuses_instead_of_shrinking_when_budget_too_small():
    old_available = certify_all.available_gib
    certify_all.available_gib = lambda: 12.0
    try:
        memlimit, refusal = certify_all.job_memlimit_gib(
            1, reserve_frac=0.60, want_gib=8)
    finally:
        certify_all.available_gib = old_available
    return check(
        memlimit is None and refusal and "Refusing rather than shrinking" in refusal,
        f"certify_all refuses instead of silently lowering 8GiB: "
        f"memlimit={memlimit}, refusal={refusal}")


def main():
    root = "/home/samson/workspace/VeriPUT/Results"
    cases = [
        ("--out", run_with("--out", root + "/certify.jsonl")),
        ("--workdir", run_with("--workdir", root + "/certify-work")),
        ("--ast-cache-root", run_with("--ast-cache-root", root + "/ast-cache")),
    ]
    bad = 0
    bad += test_job_memlimit_gib_does_not_shrink_to_fractional_budget()
    bad += test_job_memlimit_gib_refuses_instead_of_shrinking_when_budget_too_small()
    for label, cp in cases:
        bad += check(cp.returncode == 1 and f"{label} must not be under" in cp.stderr,
                     f"protected certify_all {label} is refused: {cp.stderr.strip()}")
        bad += check("could not resolve prepared subject" not in cp.stdout + cp.stderr,
                     f"{label} refusal happens before subject resolution")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
