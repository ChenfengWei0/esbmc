#!/usr/bin/env python3
"""Standalone VeriPUT entry point."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

CERTIFY_ALL = HERE / "certify_all.py"
PUT_ALL = HERE / "put_all.py"


def certify_command(args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        str(CERTIFY_ALL),
        "--esbmc",
        str(args.esbmc),
        "--sol",
        str(args.sol),
        "--ast",
        str(args.ast),
        "--contract",
        args.contract,
        "--out",
        str(args.cert_out),
        "--timeout",
        str(args.timeout),
        "--memlimit-gib",
        str(args.memlimit_gib),
    ]
    if args.focus_function:
        cmd.extend(["--focus-function", args.focus_function])
    if args.extra_certify_arg:
        cmd.extend(args.extra_certify_arg)
    return cmd


def put_command(args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        str(PUT_ALL),
        "--esbmc",
        str(args.esbmc),
        "--sol",
        str(args.sol),
        "--ast",
        str(args.ast),
        "--contract",
        args.contract,
        "--cert",
        str(args.cert_out),
        "--out",
        str(args.put_out),
        "--timeout",
        str(args.timeout),
        "--memlimit-gib",
        str(args.memlimit_gib),
    ]
    if args.focus_function:
        cmd.extend(["--focus-function", args.focus_function])
    if args.extra_put_arg:
        cmd.extend(args.extra_put_arg)
    return cmd


def run(args: argparse.Namespace) -> int:
    args.cert_out = args.out / "certify-results.jsonl"
    args.put_out = args.out / "put"

    certify = certify_command(args)
    put = put_command(args)
    if args.dry_run:
        print(" ".join(certify))
        print(" ".join(put))
        return 0

    first = subprocess.run(certify, check=False)
    if first.returncode != 0:
        return first.returncode
    second = subprocess.run(put, check=False)
    return second.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run VeriPUT on one Solidity target")
    parser.add_argument("--esbmc", required=True)
    parser.add_argument("--sol", required=True)
    parser.add_argument("--ast", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--focus-function")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--memlimit-gib", type=int, default=8)
    parser.add_argument("--extra-certify-arg", action="append", default=[])
    parser.add_argument("--extra-put-arg", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except OSError as exc:
        print(f"VeriPUT: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
