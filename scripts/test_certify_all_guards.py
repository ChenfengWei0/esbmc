#!/usr/bin/env python3
import subprocess
import sys

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CERTIFY_ALL = ROOT / "notes" / "coverage" / "scripts" / "certify_all.py"


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


def main():
    root = "/home/samson/workspace/VeriPUT/Results"
    cases = [
        ("--out", run_with("--out", root + "/certify.jsonl")),
        ("--workdir", run_with("--workdir", root + "/certify-work")),
        ("--ast-cache-root", run_with("--ast-cache-root", root + "/ast-cache")),
    ]
    bad = 0
    for label, cp in cases:
        bad += check(cp.returncode == 1 and f"{label} must not be under" in cp.stderr,
                     f"protected certify_all {label} is refused: {cp.stderr.strip()}")
        bad += check("could not resolve prepared subject" not in cp.stdout + cp.stderr,
                     f"{label} refusal happens before subject resolution")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
