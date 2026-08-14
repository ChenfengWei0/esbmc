#!/usr/bin/env python3
"""Run the RQ3 No_Cer_Reg concrete-replay-only ablation."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "rq1_veriput_run.py"
DEFAULT_VERIPUT_ROOT = Path("/home/samson/workspace/VeriPUT")
DEFAULT_RESULT_ROOT = DEFAULT_VERIPUT_ROOT / "Results" / "RQ3" / "VeriExploit" / "No_Cer_Reg"
BENCHMARKS = ("peer182", "bugfix124", "real203")
TIMEOUT_S = 600
TEST_DECL_RE = re.compile(r"\bfunction\s+(test\w*)\s*\(([^)]*)\)", re.MULTILINE)
ASSERT_RE = re.compile(r"\b(?:assert\w*|vm\.expectRevert|vm\.expectEmit)\s*\(")


def mem_available_gib() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / (1024 * 1024)
    raise RuntimeError("/proc/meminfo has no MemAvailable")


def safe_total_jobs(memlimit_gib: int, mem_fraction: float, requested: int) -> int:
    memory_jobs = max(1, int(mem_available_gib() * mem_fraction // memlimit_gib))
    cpu_jobs = max(1, os.cpu_count() or 1)
    ceiling = min(memory_jobs, cpu_jobs)
    return min(requested, ceiling) if requested > 0 else ceiling


def distribute_jobs(total: int) -> dict[str, int]:
    active = min(total, len(BENCHMARKS))
    jobs = {benchmark: 0 for benchmark in BENCHMARKS}
    for benchmark in BENCHMARKS[:active]:
        jobs[benchmark] = 1
    remaining = total - active
    order = ("real203", "peer182", "bugfix124")
    index = 0
    while remaining > 0:
        jobs[order[index % len(order)]] += 1
        remaining -= 1
        index += 1
    return jobs


def runner_command(args: argparse.Namespace, benchmark: str, jobs: int) -> list[str]:
    cmd = [
        sys.executable,
        str(RUNNER),
        "--veriput-root",
        str(args.veriput_root),
        "--benchmark",
        benchmark,
        "--result-root",
        str(args.result_root),
        "--ast-cache-root",
        str(args.ast_cache_root),
        "--timeout",
        str(TIMEOUT_S),
        "--esbmc-run-timeout",
        str(TIMEOUT_S),
        "--memlimit-gib",
        str(args.memlimit_gib),
        "--jobs",
        str(jobs),
        "--mem-fraction",
        str(args.mem_fraction),
        "--stage-mem-fraction",
        str(args.stage_mem_fraction),
        "--forge-timeout",
        str(args.forge_timeout),
        "--concrete-replay-only-ablation",
        "--no-final-deploy-concrete-fallback",
        "--no-skip-concrete-only-after-any-valid",
        "--skip-concrete-only-after-put-valid",
        "0",
        "--concrete-only-stage4-timeout-cap-s",
        "0",
        "--redo",
    ]
    if args.limit:
        cmd.extend(["--limit", str(args.limit)])
    if args.esbmc:
        cmd.extend(["--esbmc", str(args.esbmc)])
    return cmd


def audit_output(result_root: Path) -> dict:
    put_records = []
    concrete_records = []
    invalid_tests = []
    result_files = []
    for path in result_root.glob("*/subjects/**/put.json"):
        if not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        kind = str(record.get("kind") or "")
        if kind == "put":
            put_records.append(str(path))
        elif kind == "concrete":
            concrete_records.append(str(path))
    for result_json in result_root.glob("*/subjects/*/result.json"):
        if not result_json.is_file() or ".redo." in str(result_json):
            continue
        try:
            result = json.loads(result_json.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        result_files.append(str(result_json))
        for artifact in (result.get("put") or {}).get("valid_artifacts") or []:
            if not isinstance(artifact, dict):
                continue
            if artifact.get("kind") != "concrete":
                continue
            concrete_records.append(artifact)
            errors = []
            if artifact.get("is_put") or not artifact.get("is_concrete"):
                errors.append("not-concrete-only")
            if not artifact.get("concrete_oracles"):
                errors.append("missing-structured-concrete-oracle")
            test_file = Path(str(artifact.get("file") or ""))
            test_name = str(artifact.get("test") or "")
            if not test_file.is_file() or not test_name:
                errors.append("missing-test-file-or-name")
            else:
                source = test_file.read_text(errors="replace")
                match = re.search(rf"\bfunction\s+{re.escape(test_name)}\s*\(([^)]*)\)",
                                  source)
                if not match:
                    errors.append("missing-selected-test-function")
                else:
                    params = match.group(1).strip()
                    tail = source[match.end():]
                    next_test = TEST_DECL_RE.search(tail)
                    body = tail[:next_test.start()] if next_test else tail
                    if params:
                        errors.append("fuzz-parameters")
                    if not ASSERT_RE.search(body):
                        errors.append("missing-execution-assertion")
            if errors:
                invalid_tests.append({
                    "file": str(test_file),
                    "test": test_name,
                    "errors": errors,
                })
    return {
        "schema": "veriput-rq3-no-cer-reg-audit/1",
        "result_root": str(result_root),
        "result_files": len(result_files),
        "concrete_records": len(concrete_records),
        "put_leaks": len(put_records),
        "invalid_tests": len(invalid_tests),
        "put_leak_files": put_records,
        "invalid_test_details": invalid_tests,
        "ok": not put_records and not invalid_tests,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--veriput-root", type=Path, default=DEFAULT_VERIPUT_ROOT)
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--ast-cache-root",
                        type=Path,
                        default=Path("/tmp/veriput_rq3_no_cer_reg_ast_cache"))
    parser.add_argument("--esbmc", type=Path, default=None)
    parser.add_argument("--memlimit-gib", type=int, default=4)
    parser.add_argument("--jobs",
                        type=int,
                        default=0,
                        help="total subject concurrency; 0 uses the largest safe value")
    parser.add_argument("--mem-fraction", type=float, default=0.65)
    parser.add_argument("--stage-mem-fraction", type=float, default=0.60)
    parser.add_argument("--forge-timeout", type=int, default=180)
    parser.add_argument("--limit",
                        type=int,
                        default=0,
                        help="smoke-test limit per benchmark; 0 means the full corpus")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args(argv)
    if args.memlimit_gib <= 0 or not 0 < args.mem_fraction <= 1:
        parser.error("memory limits must be positive and --mem-fraction must be in (0, 1]")
    args.result_root = args.result_root.expanduser().resolve()
    args.veriput_root = args.veriput_root.expanduser().resolve()
    args.ast_cache_root = args.ast_cache_root.expanduser().resolve()
    if args.audit_only:
        audit = audit_output(args.result_root)
        print(json.dumps(audit, indent=2, sort_keys=True))
        return 0 if audit["ok"] else 1

    total_jobs = safe_total_jobs(args.memlimit_gib, args.mem_fraction, args.jobs)
    allocation = distribute_jobs(total_jobs)
    commands = [(benchmark, runner_command(args, benchmark, jobs))
                for benchmark, jobs in allocation.items() if jobs]
    launch = {
        "schema": "veriput-rq3-no-cer-reg-launch/1",
        "created_ts": time.time(),
        "result_root": str(args.result_root),
        "timeout_s": TIMEOUT_S,
        "mem_available_gib": round(mem_available_gib(), 3),
        "memlimit_gib_per_job": args.memlimit_gib,
        "total_jobs": total_jobs,
        "allocation": allocation,
        "commands": [cmd for _benchmark, cmd in commands],
        "output_contract": "zero-parameter concrete replay tests with execution assertions only",
    }
    print(json.dumps(launch, indent=2, sort_keys=True), flush=True)
    if args.dry_run:
        return 0
    args.result_root.mkdir(parents=True, exist_ok=True)
    (args.result_root /
     "launch.json").write_text(json.dumps(launch, indent=2, sort_keys=True) + "\n")
    processes = []
    for benchmark, cmd in commands:
        log_path = args.result_root / f"{benchmark}.runner.log"
        stream = log_path.open("a")
        processes.append((benchmark, subprocess.Popen(cmd, stdout=stream,
                                                      stderr=subprocess.STDOUT), stream))
    returncodes = {}
    for benchmark, process, stream in processes:
        returncodes[benchmark] = process.wait()
        stream.close()
    audit = audit_output(args.result_root)
    audit["runner_returncodes"] = returncodes
    (args.result_root / "audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    print(json.dumps(audit, indent=2, sort_keys=True))
    return 0 if audit["ok"] and all(code == 0 for code in returncodes.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
