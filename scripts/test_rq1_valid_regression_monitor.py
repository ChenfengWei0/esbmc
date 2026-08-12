#!/usr/bin/env python3
"""Focused tests for the continuous RQ1 valid regression monitor."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from argparse import Namespace
from pathlib import Path


SCRIPT = (Path(__file__).parents[1] / "notes" / "coverage" / "scripts" /
          "rq1_valid_regression_monitor.py")
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("rq1_valid_regression_monitor", SCRIPT)
MONITOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MONITOR)


def check(condition: bool, message: str) -> int:
    if condition:
        print(f"PASS: {message}")
        return 0
    print(f"FAIL: {message}")
    return 1


def test_permission_evidence() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        result = root / "case" / "result.json"
        log = root / "sample.log"
        evidence = result.parent / "cert" / "work" / "driver.log"
        evidence.parent.mkdir(parents=True)
        result.write_text("{}\n")
        log.write_text("runner completed\n")
        evidence.write_text("PermissionError: [Errno 13] Permission denied: esbmc\n")
        infra, reasons = MONITOR.classify_infrastructure(result, log)
        return check(infra and any("permissionerror" in item for item in reasons),
                     "PermissionError in cert evidence is infrastructure")


def test_binary_identity() -> int:
    with tempfile.TemporaryDirectory() as raw:
        binary = Path(raw) / "esbmc"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        before = MONITOR.binary_identity(binary)
        stat = binary.stat()
        os.utime(binary, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        after = MONITOR.binary_identity(binary)
        stable = MONITOR.wait_for_stable_binary(binary, 0.002, 0.001)
        bad = 0
        bad += check(MONITOR.binary_changed(before, after),
                     "mtime change alters binary identity")
        bad += check(stable["executable"], "stable binary must be executable")
        return bad


def test_history_reconciliation() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        permission_result = root / "permission" / "result.json"
        permission_log = root / "permission.log"
        evidence = permission_result.parent / "cert" / "driver.log"
        evidence.parent.mkdir(parents=True)
        permission_result.write_text("{}\n")
        permission_log.write_text("runner\n")
        evidence.write_text("OSError: [Errno 13] Permission denied\n")
        defect_result = root / "defect" / "result.json"
        defect_log = root / "defect.log"
        defect_result.parent.mkdir(parents=True)
        defect_result.write_text("{}\n")
        defect_log.write_text("INTERNAL DEFECT: missing enumerated path\n")
        common = {
            "dataset": "peer182",
            "unit": "f",
            "old_quality": "valid-PUT-no-R1R2",
            "old_result": "/canonical/result.json",
            "historical_generation_wall_s": 2.0,
            "regressed": True,
        }
        rows = [
            {**common, "sequence": 1, "subject_id": "permission",
             "run_result": str(permission_result), "log": str(permission_log)},
            {**common, "sequence": 2, "subject_id": "defect",
             "run_result": str(defect_result), "log": str(defect_log)},
        ]
        alerts = root / "regressions.jsonl"
        infrastructure = root / "infrastructure.jsonl"
        corrected, pending = MONITOR.reconcile_history(rows, alerts, infrastructure)
        bad = 0
        bad += check(corrected[0]["classification"] == "infrastructure-error",
                     "permission sample is reclassified")
        bad += check(corrected[1]["classification"] == "regression",
                     "semantic defect remains regression")
        bad += check(len(MONITOR.load_history(alerts)) == 1,
                     "infrastructure sample is absent from regressions")
        bad += check(pending is not None and pending["subject_id"] == "permission",
                     "infrastructure sample is pending retry")
        return bad


def test_false_valid_history_becomes_contamination() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        result = root / "result.json"
        log = root / "sample.log"
        result.write_text("{}\n")
        log.write_text("0 tests passed\n")
        rows = [{
            "sequence": 182,
            "dataset": "peer182",
            "subject_id": "deploy-only",
            "unit": "__deploy__",
            "old_quality": "valid-no-PUT",
            "run_result": str(result),
            "log": str(log),
            "regressed": True,
        }]
        alerts = root / "regressions.jsonl"
        infrastructure = root / "infrastructure.jsonl"
        corrected, pending = MONITOR.reconcile_history(
            rows, alerts, infrastructure, {("peer182", "strict-valid")})
        bad = 0
        bad += check(corrected[0]["classification"] == "ledger-contamination",
                     "false-valid historical sample is ledger contamination")
        bad += check(not MONITOR.load_history(alerts),
                     "ledger contamination is absent from valid regressions")
        bad += check(pending is None, "ledger contamination is not retried")
        return bad


def test_retained_replay_reclassifies_generation_failure() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        result = root / "result.json"
        log = root / "runner.log"
        result.write_text("{}\n")
        log.write_text("certification timed out\n")
        row = {
            "sequence": 186, "dataset": "real203", "subject_id": "liquidation",
            "unit": "checkLiquidation", "old_quality": "valid-no-PUT",
            "validation_mode": "runner", "run_result": str(result), "log": str(log),
            "regressed": True,
        }
        resolution = {
            "resolves_sequence": 186, "valid_now": True,
            "classification": "runner-generation-regression",
            "validation_mode": "concrete-replay",
        }
        alerts = root / "regressions.jsonl"
        infrastructure = root / "infrastructure.jsonl"
        corrected, _ = MONITOR.reconcile_history(
            [row], alerts, infrastructure, {("real203", "liquidation")}, [resolution])
        bad = 0
        bad += check(corrected[0]["classification"] == "runner-generation-regression",
                     "green retained replay preserves valid while recording generation timeout")
        bad += check(corrected[0]["valid_now"] and not corrected[0]["regressed"]
                     and not MONITOR.load_history(alerts),
                     "resolved generation timeout is absent from valid regressions")
        return bad


def _write_result(root: Path, dataset: str, subject: str, doc: dict) -> Path:
    path = root / dataset / "subjects" / subject / "result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc) + "\n")
    return path


def _artifact(project: Path, unit: str, *, is_put: bool) -> dict:
    test_file = project / "test" / f"{unit}.t.sol"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    (project / "foundry.toml").write_text("[profile.default]\n")
    test_file.write_text("contract T {}\n")
    return {
        "file": str(test_file),
        "test": f"test_{unit}",
        "unit": unit,
        "is_put": is_put,
        "kind": "put" if is_put else "concrete",
        "valid_reference_test": True,
    }


def test_candidate_pools() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        fast_project = root / "fast-project"
        slow_project = root / "slow-project"
        deploy_project = root / "deploy-project"
        put_artifact = _artifact(fast_project, "putUnit", is_put=True)
        concrete_artifact = _artifact(slow_project, "slowUnit", is_put=False)
        deploy_artifact = _artifact(deploy_project, "__deploy__", is_put=False)
        deploy_artifact.update({
            "stage2_source": "no_unit_deploy_fallback",
            "stage4_kind": "deploy-only",
        })
        source_artifact = {
            **concrete_artifact,
            "unit": "sourceGuard",
            "stage2_source": "source_function_revert_fallback",
            "stage4_kind": "function-revert-only",
        }
        _write_result(root, "peer182", "put", {
            "row": {"quality_bucket": "valid-PUT-no-R1R2",
                    "completion_status": "ok", "generation_wall_s": 2,
                    "raw_artifacts": [put_artifact]}})
        # Adopted probes can retain tests outside row.raw_artifacts.
        _write_result(root, "peer182", "slow-concrete", {
            "quality_bucket": "valid-no-PUT", "raw_tests": [concrete_artifact],
            "row": {"quality_bucket": "valid-no-PUT",
                    "completion_status": "adopted-probe", "generation_wall_s": 200}})
        _write_result(root, "bugfix124", "deploy-concrete", {
            "row": {"quality_bucket": "valid-no-PUT",
                    "completion_status": "no-units", "generation_wall_s": 0,
                    "valid_tests": [deploy_artifact]}})
        _write_result(root, "real203", "fast-concrete", {
            "put": {"valid_tests": [{**concrete_artifact, "unit": "fastUnit"}]},
            "row": {"quality_bucket": "valid-no-PUT",
                    "completion_status": "ok", "generation_wall_s": 3}})
        _write_result(root, "real203", "source-concrete", {
            "row": {"quality_bucket": "valid-no-PUT", "completion_status": "ok",
                    "generation_wall_s": 0, "raw_artifacts": [source_artifact]}})
        _write_result(root, "peer182", "ignored.redo.1", {
            "row": {"quality_bucket": "valid-PUT-no-R1R2",
                    "completion_status": "ok", "generation_wall_s": 1,
                    "raw_artifacts": [put_artifact]}})
        _write_result(root, "real203", "out-of-rq1", {
            "row": {"quality_bucket": "valid-PUT-no-R1R2",
                    "completion_status": "ok", "generation_wall_s": 1,
                    "raw_artifacts": [put_artifact]}})
        case_state = root / "case-state.json"
        case_state.write_text(json.dumps({"cases": {
            "peer182/put": {}, "peer182/slow-concrete": {},
            "bugfix124/deploy-concrete": {}, "real203/fast-concrete": {},
            "real203/source-concrete": {},
        }}) + "\n")

        pools = MONITOR.canonical_candidates(root, case_state)
        by_subject = {item["subject_id"]: item for pool in pools.values() for item in pool}
        bad = 0
        bad += check(set(by_subject) == {"put", "slow-concrete", "fast-concrete",
                                        "source-concrete"},
                     "candidate pools include only strict-valid PUT and concrete subjects")
        bad += check(by_subject["put"]["quality_class"] == "put"
                     and by_subject["put"]["validation_mode"] == "runner",
                     "PUT candidates exercise the current runner")
        bad += check(by_subject["fast-concrete"]["validation_mode"] == "runner",
                     "fast callable concrete candidates exercise the current runner")
        bad += check(by_subject["slow-concrete"]["validation_mode"] == "concrete-replay",
                     "slow concrete candidates use bounded retained replay")
        bad += check(by_subject["source-concrete"]["validation_mode"] == "concrete-replay",
                     "source-grounded concrete candidates replay retained evidence")
        getter_doc = {"row": {"raw_artifacts": [{
            **concrete_artifact,
            "stage2_source": "structural_getter_only",
            "stage4_kind": "getter-only",
        }]}}
        bad += check(MONITOR._concrete_candidate(getter_doc, 1)["validation_mode"] ==
                     "concrete-replay",
                     "structural getters replay retained evidence")
        bad += check("deploy-concrete" not in by_subject,
                     "deploy-only false-valid records are excluded")
        bad += check("out-of-rq1" not in by_subject,
                     "candidate pools are restricted to the fixed RQ1 identity set")
        bad += check(Path(by_subject["slow-concrete"]["forge_root"]) == slow_project,
                     "concrete replay discovers its Foundry project")
        run_root = root / "isolated-run"
        command = MONITOR.build_replay_command(
            Namespace(forge=Path("/usr/bin/forge")), by_subject["slow-concrete"], run_root)
        bad += check(command[command.index("--out") + 1] == str(run_root / "forge-out")
                     and "--no-cache" in command,
                     "concrete replay redirects Foundry outputs outside canonical artifacts")
        bad += check("--match-test" not in command,
                     "replay runs the exact file without fragile function-signature matching")
        return bad


def test_forge_replay_requires_a_test() -> int:
    with tempfile.TemporaryDirectory() as raw:
        log = Path(raw) / "forge.log"
        log.write_text("No tests found in project!\n")
        bad = check(not MONITOR.forge_replay_succeeded(0, False, log),
                    "empty successful Forge invocation is rejected")
        log.write_text("Suite result: ok. 1 passed; 0 failed; 0 skipped\n")
        bad += check(MONITOR.forge_replay_succeeded(0, False, log),
                     "Forge replay requires at least one passing test")
        log.write_text("Suite result: ok. 2 passed; 0 failed; 0 skipped\n")
        bad += check(MONITOR.forge_replay_passed_count(log) == 2,
                     "Forge replay records the executed passing-test count")
        bad += check(not MONITOR.forge_replay_succeeded(1, False, log),
                     "nonzero Forge replay is rejected")
        log.write_text("Suite result: ok. 0 passed; 0 failed; 0 skipped\n")
        bad += check(not MONITOR.forge_replay_succeeded(0, False, log),
                     "rc0 Forge summary with zero executed tests is rejected")
        return bad


def test_runner_forge_budget_stays_inside_case_cap() -> int:
    args = Namespace(
        runner=Path("/runner.py"), memlimit_gib=6, esbmc=Path("/esbmc"),
        case_timeout=120)
    sample = {
        "dataset": "real203", "subject_id": "cold-forge", "unit": "verify",
    }
    command = MONITOR.build_runner_command(args, sample, Path("/isolated-run"))
    forge_timeout = int(command[command.index("--forge-timeout") + 1])
    runner_timeout = int(command[command.index("--timeout") + 1])
    wrapper_grace = int(command[command.index("--wrapper-grace") + 1])
    bad = 0
    bad += check(forge_timeout == 20,
                 "runner gives cold Forge self-check a 20-second budget")
    bad += check(runner_timeout + wrapper_grace + 2 * forge_timeout <=
                 args.case_timeout,
                 "generation plus Forge wrappers fit the 120-second hard cap")
    return bad


def test_startup_rejects_historical_zero_test_pass() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        result = root / "result.json"
        log = root / "forge.log"
        result.write_text("{}\n")
        log.write_text("No tests found in project!\n")
        row = {
            "sequence": 9, "dataset": "peer182", "subject_id": "strict-valid",
            "unit": "f", "old_quality": "valid-no-PUT",
            "validation_mode": "concrete-replay", "returncode": 0,
            "timed_out": False, "valid_now": True, "regressed": False,
            "classification": "pass", "run_result": str(result), "log": str(log),
        }
        alerts = root / "regressions.jsonl"
        infrastructure = root / "infrastructure.jsonl"
        corrected, _ = MONITOR.reconcile_history(
            [row], alerts, infrastructure, {("peer182", "strict-valid")})
        bad = 0
        bad += check(corrected[0]["classification"] == "regression"
                     and corrected[0]["replay_tests_passed"] == 0,
                     "startup reconciliation rejects historical rc0 zero-test pass")
        bad += check(len(MONITOR.load_history(alerts)) == 1,
                     "historical zero-test pass is written to regression alerts")
        return bad


def main() -> int:
    bad = 0
    bad += test_permission_evidence()
    bad += test_binary_identity()
    bad += test_history_reconciliation()
    bad += test_false_valid_history_becomes_contamination()
    bad += test_retained_replay_reclassifies_generation_failure()
    bad += test_candidate_pools()
    bad += test_forge_replay_requires_a_test()
    bad += test_runner_forge_budget_stays_inside_case_cap()
    bad += test_startup_rejects_historical_zero_test_pass()
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
