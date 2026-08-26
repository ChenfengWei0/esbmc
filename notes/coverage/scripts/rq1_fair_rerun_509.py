#!/usr/bin/env python3
"""Freeze and optionally launch the fair 509-case VeriPUT rerun.

The experimental unit is one target contract, not one historical CE. Every
target receives one fresh 600-second wall-clock budget shared by discovery,
region certification, PUT synthesis/proof, and Foundry replay. Historical RQ1
results and concrete replays are deliberately not consumed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))

import rq1_veriput_run  # noqa: E402
from solidity_path_generalise import k_induction_proof_args as region_proof_args  # noqa: E402
from solidity_path_put import k_induction_proof_args as oracle_proof_args  # noqa: E402

DEFAULT_VERIPUT_ROOT = Path("/home/samson/workspace/VeriPUT")
DEFAULT_OUTPUT_ROOT = DEFAULT_VERIPUT_ROOT / "Results" / "RQ1_KInduction_Fair600"
BENCHMARKS = ("peer182", "bugfix124", "real203")
# 2026-08-26: real203 is 202 (compound-finance__comet__CometStorage excluded, see target_manifest.py)
EXPECTED_COUNTS = {"peer182": 182, "bugfix124": 124, "real203": 202}
EXPECTED_TOTAL = sum(EXPECTED_COUNTS.values())  # 508
TARGET_UNIVERSE_AUDIT = REPO / "notes" / "coverage" / "rq1_artifact_audit.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def common_runner_args(veriput_root: Path, output_root: Path, esbmc: Path,
                       jobs: int) -> list[str]:
    """One immutable policy shared by all three dataset invocations."""
    return [
        "--veriput-root", str(veriput_root),
        "--result-root", str(output_root),
        "--ast-cache-root", "/tmp/veriput_rq1_fair600_ast_cache",
        "--esbmc", str(esbmc),
        "--order", "dataset",
        "--strict-case-wall-budget",
        "--timeout", "600",
        "--esbmc-run-timeout", "120",
        "--stage2-unit-timeout-cap-s", "0",
        "--adaptive-stage2-unit-timeout-cap-s", "120",
        "--stage2-stage4-reserve-s", "120",
        "--wrapper-grace", "0",
        "--min-remaining-s", "20",
        "--no-output-stage2-stop-s", "0",
        "--no-candidate-stage2-unit-stop-n", "0",
        "--zero-output-stage4-stop-s", "0",
        "--min-concrete-only-stage4-s", "90",
        "--min-timeout-only-stage4-s", "90",
        "--skip-concrete-only-after-put-valid", "0",
        "--no-skip-concrete-only-after-any-valid",
        "--concrete-only-stage4-timeout-cap-s", "0",
        "--memlimit-gib", "4",
        "--jobs", str(jobs),
        "--mem-fraction", "0.95",
        "--stage-mem-fraction", "0.60",
        "--forge-timeout", "180",
    ]


def frozen_targets(veriput_root: Path) -> tuple[list[dict], dict[str, int]]:
    try:
        audit = json.loads(TARGET_UNIVERSE_AUDIT.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise rq1_veriput_run.RQ1RunError(f"cannot read frozen RQ1 target audit: {exc}") from exc
    audit_rows = audit.get("rows") if isinstance(audit, dict) else None
    if not isinstance(audit_rows, list) or len(audit_rows) != EXPECTED_TOTAL:
        raise rq1_veriput_run.RQ1RunError(f"frozen RQ1 target audit is not exactly {EXPECTED_TOTAL} rows")
    targets = []
    counts = {}
    for benchmark in BENCHMARKS:
        selected_audit_rows = [
            row for row in audit_rows
            if isinstance(row, dict) and row.get("bench") == benchmark
        ]
        _dataset, current_rows = rq1_veriput_run.target_rows(
            veriput_root, benchmark, [], 0, order="dataset")
        current_by_safe_id = {}
        for current in current_rows:
            safe_id = rq1_veriput_run._safe_name(str(current.get("subject_id") or ""))
            if safe_id in current_by_safe_id:
                raise rq1_veriput_run.RQ1RunError(
                    f"current {benchmark} manifest has colliding safe subject id {safe_id}")
            current_by_safe_id[safe_id] = current
        subject_ids = []
        for audit_row in selected_audit_rows:
            current = current_by_safe_id.get(str(audit_row.get("subject") or ""))
            candidates = [str(current["subject_id"])] if current is not None else []
            try:
                prior = json.loads(Path(str(audit_row["result_json"])).read_text())
                prior_id = str((prior.get("target") or {}).get("subject_id") or "")
                if prior_id:
                    candidates.append(prior_id)
            except (KeyError, OSError, json.JSONDecodeError):
                pass
            candidates.append(str(audit_row.get("subject") or ""))
            resolved_id = None
            for candidate in dict.fromkeys(candidates):
                try:
                    rq1_veriput_run.resolve_subject(
                        candidate,
                        benchmark=rq1_veriput_run.TARGET_BENCHMARK_ARG[benchmark],
                        require_unit=False)
                except rq1_veriput_run.SubjectError:
                    continue
                resolved_id = candidate
                break
            if resolved_id is None:
                raise rq1_veriput_run.RQ1RunError(
                    f"prepared corpus cannot resolve frozen target {audit_row.get('subject')}")
            subject_ids.append(resolved_id)
        if not all(subject_ids) or len(subject_ids) != EXPECTED_COUNTS[benchmark]:
            raise rq1_veriput_run.RQ1RunError(
                f"frozen target audit has wrong {benchmark} identities: {len(subject_ids)}")
        dataset, rows = rq1_veriput_run.target_rows(
            veriput_root, benchmark, subject_ids, 0, order="dataset")
        counts[benchmark] = len(rows)
        if [row.get("subject_id") for row in rows] != subject_ids:
            available = {str(row.get("subject_id")) for row in rows}
            missing = [subject_id for subject_id in subject_ids if subject_id not in available]
            raise rq1_veriput_run.RQ1RunError(
                f"current {benchmark} target manifest cannot resolve frozen identities: {missing}")
        for ordinal, row in enumerate(rows, 1):
            targets.append({
                "global_ordinal": len(targets) + 1,
                "dataset_ordinal": ordinal,
                "dataset": dataset,
                "benchmark": row.get("benchmark"),
                "subject_id": row.get("subject_id"),
                "contract": row.get("contract"),
                "units_hint": row.get("units_hint") or [],
            })
    if counts != EXPECTED_COUNTS or len(targets) != EXPECTED_TOTAL:
        raise rq1_veriput_run.RQ1RunError(
            f"target universe drift: expected {EXPECTED_COUNTS}/{EXPECTED_TOTAL}, "
            f"got {counts}/{len(targets)}")
    identities = [(row["dataset"], row["subject_id"], row["contract"]) for row in targets]
    if len(set(identities)) != EXPECTED_TOTAL:
        raise rq1_veriput_run.RQ1RunError("509 target universe contains duplicate identities")
    return targets, counts


def build_freeze(veriput_root: Path, output_root: Path, esbmc: Path, jobs: int) -> dict:
    targets, counts = frozen_targets(veriput_root)
    runner = HERE / "rq1_veriput_run.py"
    common = common_runner_args(veriput_root, output_root, esbmc, jobs)
    commands = []
    for benchmark in BENCHMARKS:
        subject_args = []
        for target in targets:
            if target["dataset"] == benchmark:
                subject_args += ["--subject-id", target["subject_id"]]
        argv = ([sys.executable, str(runner), "--benchmark", benchmark] + subject_args + common)
        commands.append({"benchmark": benchmark, "argv": argv, "shell": shlex.join(argv)})
    proof_expected = ["--k-induction", "--enable-forward-condition", "--max-k-step", "30"]
    region_actual = region_proof_args([
        "--unwind", "8", "--incremental-bmc", "--overflow-check",
        "--div-by-zero-check", "--solidity-max-tx", "1",
    ])
    oracle_actual = oracle_proof_args([
        "--unwind", "8", "--incremental-bmc", "--overflow-check",
        "--div-by-zero-check", "--solidity-max-tx", "1",
    ])
    if region_actual[-4:] != proof_expected or oracle_actual[-4:] != proof_expected:
        raise rq1_veriput_run.RQ1RunError("k-induction proof profile drift")
    if region_actual.count("--solidity-max-tx") != 1 or oracle_actual.count(
            "--solidity-max-tx") != 1:
        raise rq1_veriput_run.RQ1RunError("proof max-tx profile drift")
    return {
        "schema": "veriput-rq1-fair-rerun-freeze/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experimental_unit": "target-contract-case",
        "target_count": len(targets),
        "dataset_counts": counts,
        "targets": targets,
        "policy": {
            "case_wall_budget_s": 600,
            "budget_scope": [
                "subject preparation after runner entry",
                "Stage 1/2 path discovery and region certification",
                "Stage 4 PUT materialization and R1/R2 proof",
                "Foundry replay and fallback execution",
            ],
            "deadline_enforcement": "one monotonic deadline per target; subprocess process-group kill",
            "historical_ce_as_generalization_input": False,
            "historical_rq1_adoption": False,
            "resume": False,
            "region_proof": region_actual,
            "r1_r2_proof": oracle_actual,
            "solidity_max_tx": 1,
            "max_k_step": 30,
            "memlimit_gib_per_esbmc": 4,
            "concurrent_cases": jobs,
            "order": "dataset",
            "recipe_version": rq1_veriput_run.STRONG_RECIPE_VERSION,
        },
        "isolation": {
            "result_root": str(output_root),
            "canonical_rq1_root": str(veriput_root / "Results" / "RQ1" / "VeriPUT"),
            "canonical_rq1_writable": False,
        },
        "inputs": {
            "target_universe_audit": str(TARGET_UNIVERSE_AUDIT),
            "target_universe_audit_sha256": sha256_file(TARGET_UNIVERSE_AUDIT),
            "runner": str(runner),
            "runner_sha256": sha256_file(runner),
            "esbmc": str(esbmc),
            "esbmc_sha256": sha256_file(esbmc),
        },
        "commands": commands,
    }


def write_freeze(output_root: Path, freeze: dict) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    manifest = output_root / "fair-rerun-509.json"
    temporary = output_root / f".{manifest.name}.tmp.{os.getpid()}"
    temporary.write_text(json.dumps(freeze, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, manifest)
    run_script = output_root / "run-fair-rerun-509.sh"
    lines = ["#!/bin/sh", "set -eu"]
    lines.extend(command["shell"] for command in freeze["commands"])
    run_script.write_text("\n".join(lines) + "\n")
    run_script.chmod(0o755)


def refuse_nonfresh_execution(output_root: Path) -> None:
    occupied = []
    for dataset in ("peer182", "bugfix124", "real203"):
        root = output_root / dataset
        if root.exists() and any(root.iterdir()):
            occupied.append(str(root))
    if occupied:
        raise rq1_veriput_run.RQ1RunError(
            "fair rerun requires fresh dataset roots; occupied: " + ", ".join(occupied))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--veriput-root", default=str(DEFAULT_VERIPUT_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--esbmc", default=str(rq1_veriput_run.DEFAULT_ESBMC))
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--execute", action="store_true",
                        help="run the three frozen dataset commands; omitted means prepare only")
    args = parser.parse_args(argv)
    try:
        veriput_root = Path(args.veriput_root).expanduser().resolve()
        output_root = Path(args.output_root).expanduser().resolve()
        esbmc = Path(args.esbmc).expanduser().resolve()
        expected_root = veriput_root / "Results" / "RQ1_KInduction_Fair600"
        if output_root != expected_root:
            raise rq1_veriput_run.RQ1RunError(
                f"--output-root must be the isolated frozen root {expected_root}")
        if args.jobs <= 0:
            raise rq1_veriput_run.RQ1RunError("--jobs must be positive")
        if not esbmc.is_file():
            raise rq1_veriput_run.RQ1RunError(f"ESBMC binary is absent: {esbmc}")
        freeze = build_freeze(veriput_root, output_root, esbmc, args.jobs)
        if args.execute:
            refuse_nonfresh_execution(output_root)
        write_freeze(output_root, freeze)
        print(json.dumps({
            "manifest": str(output_root / "fair-rerun-509.json"),
            "run_script": str(output_root / "run-fair-rerun-509.sh"),
            "target_count": freeze["target_count"],
            "dataset_counts": freeze["dataset_counts"],
            "executed": bool(args.execute),
        }, indent=2, sort_keys=True))
        if not args.execute:
            return 0
        for command in freeze["commands"]:
            completed = subprocess.run(command["argv"], check=False)
            if completed.returncode != 0:
                return completed.returncode
        return 0
    except (OSError, rq1_veriput_run.RQ1RunError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
