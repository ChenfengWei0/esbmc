#!/usr/bin/env python3
"""Freeze the current no-PUT obligations into fair600 case jobs.

The default action only writes and validates manifests and launch commands. It
never starts ESBMC or Forge. One logical shard runs at most one case at a time;
the generated top-level launcher may run all shards concurrently after a
single fresh-root and identity preflight.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import stat
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))

import rq1_not_generalized_ce_freeze as source_inventory  # noqa: E402
import rq1_veriput_run  # noqa: E402
from rq1_final_test_inventory import obligations as current_obligations  # noqa: E402
from solidity_path_generalise import (  # pylint: disable=import-error  # noqa: E402
    k_induction_proof_args as region_args,
)
from solidity_path_put import (  # pylint: disable=import-error  # noqa: E402
    k_induction_proof_args as oracle_args,
)

VERIPUT_ROOT = Path("/home/samson/workspace/VeriPUT")
DEFAULT_OUTPUT = (VERIPUT_ROOT / "Results" / "RQ1_KInduction_NoPUT600"
                  / "fair600-cases202-freeze-20260815")
CURRENT_ESBMC = REPO / "build" / "src" / "esbmc" / "esbmc"
EXPECTED_OBLIGATIONS = 526
EXPECTED_CASES = 201
EXPECTED_CASE_COUNTS = {"bugfix124": 47, "peer182": 63, "real203": 91}
DEFAULT_SHARDS = 7
FORBIDDEN_FLAGS = {
    "--adopt-only", "--ce-collection-only", "--ce-replay-manifest",
    "--ce-replay-only", "--fallback-only", "--resume", "--redo",
}


def _compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash_value(value: Any) -> str:
    return hashlib.sha256(_compact(value).encode("ascii")).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _option(argv: list[str], name: str) -> str | None:
    positions = [index for index, value in enumerate(argv) if value == name]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        return None
    return argv[positions[0] + 1]


def _proof_profiles() -> tuple[list[str], list[str]]:
    base = [
        "--unwind", "8", "--incremental-bmc", "--overflow-check",
        "--div-by-zero-check", "--solidity-max-tx", "1",
    ]
    region = region_args(base)
    oracle = oracle_args(base)
    expected_tail = ["--k-induction", "--enable-forward-condition", "--max-k-step", "30"]
    for name, argv in (("region", region), ("oracle", oracle)):
        if argv[-4:] != expected_tail:
            raise RuntimeError(f"{name} k-induction profile drift")
        if _option(argv, "--max-k-step") != "30":
            raise RuntimeError(f"{name} max-k is not exactly 30")
        if _option(argv, "--solidity-max-tx") != "1":
            raise RuntimeError(f"{name} max-tx is not exactly 1")
    return region, oracle


def _readonly_binary(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise RuntimeError("frozen ESBMC must be a non-symlink regular file")
    if info.st_mode & 0o222:
        raise RuntimeError("frozen ESBMC has a writable mode")
    if not os.access(path, os.X_OK):
        raise RuntimeError("frozen ESBMC is not executable")
    return {
        "path": str(path.resolve()),
        "sha256": _hash_file(path),
        "mode": stat.S_IMODE(info.st_mode),
        "size": info.st_size,
    }


def _assert_current_binary_snapshot(frozen: dict[str, Any]) -> None:
    if not CURRENT_ESBMC.is_file():
        raise RuntimeError(f"current ESBMC build is missing: {CURRENT_ESBMC}")
    current = {
        "sha256": _hash_file(CURRENT_ESBMC),
        "size": CURRENT_ESBMC.stat().st_size,
    }
    if frozen.get("sha256") != current["sha256"] or frozen.get("size") != current["size"]:
        raise RuntimeError(
            "read-only ESBMC snapshot is stale: "
            f"frozen sha={frozen.get('sha256')} size={frozen.get('size')}; "
            f"current sha={current['sha256']} size={current['size']}")


def _common_args(output: Path, binary: Path) -> list[str]:
    return [
        "--veriput-root", str(VERIPUT_ROOT),
        "--esbmc", str(binary),
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
        "--jobs", "1",
        "--mem-fraction", "0.95",
        "--stage-mem-fraction", "0.60",
        "--forge-timeout", "180",
    ]


def _authoritative_cases() -> tuple[list[dict[str, Any]], list[list[str]]]:
    rows = source_inventory.rows(source_inventory.DEFAULT_RESULT_ROOT)
    obligations = sorted([list(map(str, row["identity"])) for row in rows])
    authoritative = sorted([list(map(str, identity)) for identity in current_obligations(
        source_inventory.DEFAULT_RESULT_ROOT)[2]])
    if obligations != authoritative:
        raise RuntimeError("evidence sources differ from authoritative not-generalized identities")
    if len(obligations) != EXPECTED_OBLIGATIONS or len({tuple(row) for row in obligations}) != len(
            obligations):
        raise RuntimeError(f"expected {EXPECTED_OBLIGATIONS} unique obligations")
    grouped: dict[str, list[list[str]]] = defaultdict(list)
    for identity in obligations:
        grouped[identity[0]].append(identity)
    if len(grouped) != EXPECTED_CASES:
        raise RuntimeError(f"expected {EXPECTED_CASES} unique cases, got {len(grouped)}")
    cases = []
    for dataset in sorted(EXPECTED_CASE_COUNTS):
        subjects = sorted(case.split("/", 1)[1] for case in grouped if case.startswith(dataset + "/"))
        label, targets = rq1_veriput_run.target_rows(
            VERIPUT_ROOT, dataset, subjects, 0, order="dataset")
        by_subject = {str(target["subject_id"]): target for target in targets}
        if label != dataset or set(by_subject) != set(subjects):
            raise RuntimeError(f"prepared target resolution drift for {dataset}")
        for subject in subjects:
            case = f"{dataset}/{subject}"
            target = by_subject[subject]
            cases.append({
                "case": case,
                "dataset": dataset,
                "subject_id": subject,
                "contract": str(target.get("contract") or ""),
                "obligation_count": len(grouped[case]),
                "obligations": grouped[case],
            })
    counts = Counter(case["dataset"] for case in cases)
    if dict(counts) != EXPECTED_CASE_COUNTS:
        raise RuntimeError(f"case distribution drift: {dict(counts)}")
    return cases, obligations


def _commands(output: Path, binary: Path, shards: list[list[dict[str, Any]]]) -> list[dict]:
    runner = HERE / "rq1_veriput_run.py"
    common = _common_args(output, binary)
    commands = []
    for shard_id, cases in enumerate(shards):
        for dataset in sorted(EXPECTED_CASE_COUNTS):
            selected = [case for case in cases if case["dataset"] == dataset]
            if not selected:
                continue
            result_root = output / "runs" / f"shard-{shard_id:02d}"
            ast_root = Path("/tmp") / f"veriput_noput_fair600_202_shard_{shard_id:02d}"
            argv = [
                sys.executable, str(runner), "--benchmark", dataset,
                "--result-root", str(result_root),
                "--ast-cache-root", str(ast_root),
            ]
            for case in selected:
                argv += ["--subject-id", case["subject_id"]]
            argv += common
            commands.append({
                "shard_id": shard_id,
                "dataset": dataset,
                "case_count": len(selected),
                "case_identities": [case["case"] for case in selected],
                "argv": argv,
                "shell": shlex.join(argv),
            })
    return commands


def _validate_manifest(document: dict[str, Any], binary: Path) -> None:
    if document.get("schema") != "veriput-rq1-noput-fair600-case-freeze/v1":
        raise RuntimeError("unexpected freeze schema")
    cases = document.get("cases") or []
    obligations = document.get("obligations") or []
    shards = document.get("shards") or []
    commands = document.get("commands") or []
    if len(cases) != EXPECTED_CASES or len(obligations) != EXPECTED_OBLIGATIONS:
        raise RuntimeError("freeze population count mismatch")
    authoritative = sorted([list(map(str, identity)) for identity in current_obligations(
        source_inventory.DEFAULT_RESULT_ROOT)[2]])
    if obligations != authoritative:
        raise RuntimeError("freeze differs from current authoritative not-generalized identities")
    if sorted(case["case"] for case in cases) != sorted({identity[0]
                                                         for identity in authoritative}):
        raise RuntimeError("freeze case projection differs from authoritative identities")
    if _hash_value(obligations) != document.get("obligation_identity_sha256"):
        raise RuntimeError("obligation identity SHA mismatch")
    case_identities = [[case["case"], case["subject_id"], case["contract"]] for case in cases]
    if _hash_value(sorted(case["case"] for case in cases)) != document.get(
            "case_identity_sha256"):
        raise RuntimeError("case-name identity SHA mismatch")
    if _hash_value(case_identities) != document.get("resolved_case_identity_sha256"):
        raise RuntimeError("resolved case identity SHA mismatch")
    members = [case for shard in shards for case in shard.get("case_identities") or []]
    wanted = [case["case"] for case in cases]
    if len(members) != len(set(members)) or sorted(members) != sorted(wanted):
        raise RuntimeError("shards are not mutually exclusive and exhaustive")
    command_members = [case for command in commands for case in command["case_identities"]]
    if len(command_members) != len(set(command_members)) or sorted(command_members) != sorted(wanted):
        raise RuntimeError("commands do not schedule every case exactly once")
    for command in commands:
        argv = command["argv"]
        if FORBIDDEN_FLAGS & set(argv):
            raise RuntimeError("command contains history/adopt/resume/redo flag")
        required = {
            "--strict-case-wall-budget", "--no-skip-concrete-only-after-any-valid",
        }
        if not required <= set(argv):
            raise RuntimeError("command lacks strict fair-budget flags")
        for name, value in (("--timeout", "600"), ("--memlimit-gib", "4"),
                            ("--jobs", "1")):
            if _option(argv, name) != value:
                raise RuntimeError(f"command has wrong {name}")
        subjects = [argv[index + 1] for index, value in enumerate(argv[:-1])
                    if value == "--subject-id"]
        if len(subjects) != command["case_count"] or len(subjects) != len(set(subjects)):
            raise RuntimeError("command subject selection is not exact")
    frozen_binary = _readonly_binary(binary)
    _assert_current_binary_snapshot(frozen_binary)
    if frozen_binary != document.get("inputs", {}).get("esbmc"):
        raise RuntimeError("read-only ESBMC snapshot drift")
    inputs = document.get("inputs", {})
    for field in ("authoritative_api", "evidence_source", "runner"):
        path = Path(str(inputs.get(field) or ""))
        if not path.is_file() or _hash_file(path) != inputs.get(field + "_sha256"):
            raise RuntimeError(f"{field} input drift")


def _write_scripts(output: Path, document: dict[str, Any]) -> None:
    binary = Path(document["inputs"]["esbmc"]["path"])
    digest = document["inputs"]["esbmc"]["sha256"]
    mode_check = '[ "$(stat -c %a "$ESBMC")" = 555 ]'
    commands_by_shard: dict[int, list[str]] = defaultdict(list)
    for command in document["commands"]:
        commands_by_shard[int(command["shard_id"])].append(command["shell"])
    for shard_id, commands in sorted(commands_by_shard.items()):
        path = output / f"run-shard-{shard_id:02d}.sh"
        lines = [
            "#!/bin/sh", "set -eu", f"ESBMC={shlex.quote(str(binary))}",
            f"echo {digest}  \"$ESBMC\" | sha256sum -c -", mode_check,
            *commands,
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        path.chmod(0o755)
    launcher = output / "run-all-shards.sh"
    validator = HERE / Path(__file__).name
    lines = [
        "#!/bin/sh", "set -eu",
        f"python3 {shlex.quote(str(validator))} --output {shlex.quote(str(output))} "
        "--validate-only",
        f"RUNS={shlex.quote(str(output / 'runs'))}",
        "if [ -e \"$RUNS\" ] && [ -n \"$(find \"$RUNS\" -mindepth 1 -print -quit)\" ]; then",
        "  echo 'REFUSED: fair600 runs root is not fresh' >&2", "  exit 1", "fi",
        "mkdir -p \"$RUNS\"", "pids=''",
    ]
    for shard in document["shards"]:
        shard_id = int(shard["shard_id"])
        lines += [
            f"{shlex.quote(str(output / f'run-shard-{shard_id:02d}.sh'))} &",
            "pids=\"$pids $!\"",
        ]
    lines += ["status=0", "for pid in $pids; do", "  wait \"$pid\" || status=1", "done",
              "exit \"$status\""]
    launcher.write_text("\n".join(lines) + "\n", encoding="utf-8")
    launcher.chmod(0o755)


def build(output: Path, shard_count: int) -> dict[str, Any]:
    binary = output / "bin" / "esbmc"
    binary_record = _readonly_binary(binary)
    _assert_current_binary_snapshot(binary_record)
    cases, obligations = _authoritative_cases()
    shards = [[] for _ in range(shard_count)]
    for index, case in enumerate(cases):
        shards[index % shard_count].append(case)
    region, oracle = _proof_profiles()
    commands = _commands(output, binary, shards)
    document = {
        "schema": "veriput-rq1-noput-fair600-case-freeze/v1",
        "grain": "unique-case",
        "obligation_count": len(obligations),
        "case_count": len(cases),
        "case_counts": EXPECTED_CASE_COUNTS,
        "obligation_identity_sha256": _hash_value(obligations),
        "case_identity_sha256": _hash_value(sorted(case["case"] for case in cases)),
        "resolved_case_identity_sha256": _hash_value(
            [[case["case"], case["subject_id"], case["contract"]] for case in cases]),
        "estimated_jobs": {
            "case_jobs": len(cases),
            "runner_invocations": len(commands),
            "logical_shards": shard_count,
            "maximum_concurrent_cases": shard_count,
            "maximum_esbmc_memory_gib": shard_count * 4,
        },
        "policy": {
            "case_wall_budget_s": 600,
            "one_complete_pipeline_per_case": True,
            "k_induction_max_k": 30,
            "solidity_max_tx": 1,
            "memlimit_gib_per_esbmc": 4,
            "historical_ce_direction": False,
            "historical_adoption": False,
            "resume": False,
            "redo": False,
            "region_proof": region,
            "r1_r2_proof": oracle,
        },
        "inputs": {
            "authoritative_api": str(HERE / "rq1_final_test_inventory.py"),
            "authoritative_api_sha256": _hash_file(HERE / "rq1_final_test_inventory.py"),
            "evidence_source": str(HERE / "rq1_not_generalized_ce_freeze.py"),
            "evidence_source_sha256": _hash_file(
                HERE / "rq1_not_generalized_ce_freeze.py"),
            "runner": str(HERE / "rq1_veriput_run.py"),
            "runner_sha256": _hash_file(HERE / "rq1_veriput_run.py"),
            "esbmc": binary_record,
        },
        "obligations": obligations,
        "cases": cases,
        "shards": [{
            "shard_id": index,
            "case_count": len(values),
            "case_identities": [case["case"] for case in values],
            "identity_sha256": _hash_value([case["case"] for case in values]),
        } for index, values in enumerate(shards)],
        "commands": commands,
    }
    _validate_manifest(document, binary)
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--shards", type=int, default=DEFAULT_SHARDS)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        output = args.output.expanduser().resolve()
        if output != DEFAULT_OUTPUT.resolve():
            raise RuntimeError(f"output must be the isolated freeze root {DEFAULT_OUTPUT}")
        manifest = output / "fair600-case-freeze.json"
        if args.validate_only:
            document = json.loads(manifest.read_text(encoding="utf-8"))
            _validate_manifest(document, output / "bin" / "esbmc")
        else:
            if args.shards <= 0:
                raise RuntimeError("shard count must be positive")
            output.mkdir(parents=True, exist_ok=True)
            document = build(output, args.shards)
            manifest.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
            _write_scripts(output, document)
        print(json.dumps({
            "manifest": str(manifest),
            "obligation_identity_sha256": document["obligation_identity_sha256"],
            "case_identity_sha256": document["case_identity_sha256"],
            "resolved_case_identity_sha256": document["resolved_case_identity_sha256"],
            "estimated_jobs": document["estimated_jobs"],
            "validated_only": args.validate_only,
            "executed": False,
        }, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
