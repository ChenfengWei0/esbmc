#!/usr/bin/env python3
"""Tests for the frozen fair RQ1 rerun protocol."""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "notes" / "coverage" / "scripts"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


rq1 = load("rq1_veriput_run", SCRIPTS / "rq1_veriput_run.py")
fair = load("rq1_fair_rerun_509", SCRIPTS / "rq1_fair_rerun_509.py")


def test_strict_wrapper_never_extends_case_deadline():
    deadline = time.monotonic() + 0.2
    capped = rq1._case_wrapper_timeout(900, deadline, True)
    assert 0 < capped <= 0.2
    assert rq1._case_wrapper_timeout(900, deadline, False) == 900


def test_run_command_timeout_kills_descendant_process_group(tmp_path):
    child_pid_file = tmp_path / "child.pid"
    code = (
        "import pathlib,subprocess,time; "
        "p=subprocess.Popen(['sleep','30']); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(p.pid)); "
        "time.sleep(30)"
    )
    result = rq1.run_command([sys.executable, "-c", code], 0.1, tmp_path / "tree")
    assert result["status"] == "timeout"
    child_pid = int(child_pid_file.read_text())
    for _attempt in range(20):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"timed-out descendant {child_pid} survived process-group kill")


def test_strict_root_is_isolated(tmp_path):
    veriput = tmp_path / "VeriPUT"
    cache = tmp_path / "cache"
    canonical = veriput / "Results" / "RQ1" / "VeriPUT"
    isolated = veriput / "Results" / "RQ1_KInduction_Fair600"
    rq1.validate_roots(veriput, isolated, cache, strict_case_wall_budget=True)
    try:
        rq1.validate_roots(veriput, canonical, cache, strict_case_wall_budget=True)
    except rq1.RQ1RunError as exc:
        assert "must not write canonical RQ1" in str(exc)
    else:
        raise AssertionError("strict fair rerun accepted canonical RQ1 output")


def test_fair_command_has_one_case_budget_and_no_history_modes(tmp_path):
    args = fair.common_runner_args(tmp_path / "VeriPUT", tmp_path / "out",
                                   tmp_path / "esbmc", 2)
    assert args[args.index("--timeout") + 1] == "600"
    assert "--strict-case-wall-budget" in args
    assert args[args.index("--memlimit-gib") + 1] == "4"
    assert args[args.index("--jobs") + 1] == "2"
    for forbidden in ("--resume", "--adopt-only", "--ce-replay-manifest",
                      "--ce-replay-only", "--subject-id", "--redo"):
        assert forbidden not in args


def test_proof_profile_removes_bounded_and_safety_noise():
    noisy = [
        "--unwind", "8", "--incremental-bmc", "--overflow-check",
        "--div-by-zero-check", "--solidity-max-tx", "1",
    ]
    expected_tail = ["--k-induction", "--enable-forward-condition", "--max-k-step", "30"]
    for builder in (fair.region_proof_args, fair.oracle_proof_args):
        actual = builder(noisy)
        assert actual[-4:] == expected_tail
        assert actual[:2] == ["--solidity-max-tx", "1"]
        assert "--unwind" not in actual
        assert "--incremental-bmc" not in actual
        assert "--overflow-check" not in actual
        assert "--div-by-zero-check" not in actual
