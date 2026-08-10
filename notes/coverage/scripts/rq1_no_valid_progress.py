#!/usr/bin/env python3
"""RQ1 no-valid progress ledger.

This script is the single source for N/204 progress reporting.  It reads the
per-case root-cause TSV and computes category counts plus patch coverage from
hard-coded patch batches.  Do not report N/204 from memory.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re


DENOMINATOR = 204
DEFAULT_TSV = Path("/tmp/veriput_no_valid_root_causes.tsv")
DEFAULT_STATE = Path("/tmp/veriput_rq1_progress_state.json")
DEFAULT_SUBAGENTS = Path("/tmp/veriput_rq1_subagents.json")
DEFAULT_EXTRA_SUBAGENTS = Path("/tmp/veriput_rq1_extra_subagents.json")
DEFAULT_REMOTE_STATE = Path("/tmp/veriput_rq1_remote_state.json")
DEFAULT_LOCAL_STATE = Path("/tmp/veriput_rq1_local_state.json")
DEFAULT_RESULTS_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_PEER_DATASET_ROOT = Path(
    "/home/samson/workspace/VeriPUT/Datasets/Peer-Reviewed-Contracts")
DEFAULT_DEADLINE_HOURS = 16.0
DEFAULT_REMOTE_HOST = "invmut-w2"
DEFAULT_REQUIRED_SUBAGENTS = 24

HARD_REQUIREMENTS = (
    "Do not report N/204 from memory; run this script or quote its last output.",
    "Every user-facing progress update must include theoretical_progress=N/204.",
    "Every user-facing progress update must explain why current resources are "
    "maximized; if they are not maximized, admit the error explicitly.",
    "Every user-facing progress update must include an accurate countdown from "
    "the persisted deadline state, subagent status, remote host status, "
    "theoretical repair N/204, and actual RQ1 valid/PUT/R1R2 progress.",
    "Local machine role: edit ESBMC/VeriPUT code and coordinate subagents.",
    "Remote invmut-w2 role: continuously run ESBMC/RQ1 validation jobs after "
    "code sync and update RQ1 artifacts; the local agent must not wait idle for "
    "remote results.",
    "Do not run broad local ESBMC/ctest/pytest while the user has requested "
    "code-level fixes first.",
    "Do not modify /home/samson/workspace/VeriPUT/Datasets.",
    "Subagents are not read-only by default.  They may edit only their exclusive "
    "write_scope.  If a subagent is exploratory-only, mark mode=readonly in "
    "the subagent status file.",
    "A subagent completion counts toward theoretical_progress only when its "
    "patch_id is recorded in the subagent status file or passed via --applied; "
    "free-form claims do not count.",
    "Subagents must analyze prior failure artifacts and the concrete owning "
    "source code before editing.  A subagent is not allowed to use a fresh "
    "ESBMC/RQ1 run as a substitute for root-cause analysis.  Every subagent "
    "final report must include failure-record paths inspected, source files "
    "inspected, code-level root cause, fix target, and theoretical coverage.",
    "Every write-mode subagent patch must be cross-reviewed by an independent "
    "review/integration agent before it is treated as fully integrated.  The "
    "review must inspect overlapping call paths and adjacent patch diffs, "
    "report conflicts or soundness risks, and either accept the theoretical "
    "coverage claim or leave the patch in pending-review status.",
)

RESOURCE_PLAN = (
    "main-agent: runner 119/204 code path",
    "24-subagent pool: split independent root-cause buckets and source slices; "
    "do not duplicate write ownership for the same file",
    "remote invmut-w2: background RQ1/ESBMC validation and Results update, "
    "without blocking local code repair",
)

SUBAGENT_PLAN = (
    ("A01", "goto_coverage no-claims/cov-report emission",
     "src/goto-programs/goto_coverage.cpp", "3/204 micro patch"),
    ("A02", "Solidity frontend tuple/member/type fallback",
     "src/solidity-frontend/solidity_convert_tuple.cpp,"
     "src/solidity-frontend/solidity_convert_ref.cpp", "ESBMC no-cov slice"),
    ("A03", "Solidity frontend call/selector/modifier fallback",
     "src/solidity-frontend/solidity_convert_call.cpp,"
     "src/solidity-frontend/solidity_convert_modifier.cpp",
     "ESBMC no-cov slice"),
    ("A04", "Solidity frontend bytes/string/mapping fallback",
     "src/solidity-frontend/solidity_convert_expr.cpp,"
     "src/solidity-frontend/solidity_convert_mapping.cpp", "ESBMC no-cov slice"),
    ("A05", "Solidity constructor/decl state init gaps",
     "src/solidity-frontend/solidity_convert_constructor.cpp,"
     "src/solidity-frontend/solidity_convert_decl.cpp", "ESBMC no-cov slice"),
    ("A06", "migrate/value_set/solver tuple residual crashes",
     "src/util/migrate.cpp,src/pointer-analysis/value_set.cpp,"
     "src/solvers/smt/tuple/smt_tuple_node.cpp", "ESBMC no-cov slice"),
    ("A07", "NOT-CERTIFIED CE-to-region mismatch",
     "scripts/solidity_path_generalise.py", "3/204 micro patch"),
    ("A08", "certify_all witness extraction and partial journals",
     "notes/coverage/scripts/certify_all.py", "NOT-CERTIFIED slice"),
    ("A09", "path-cov-max-goals split/fallback",
     "notes/coverage/scripts/certify_all.py", "2/204 small bucket"),
    ("A10", "NO-PATH bounded-holds retry/unwind",
     "notes/coverage/scripts/certify_all.py", "3/204 small bucket"),
    ("A11", "NO-COORDINATE concrete/deploy fallback",
     "notes/coverage/scripts/put_all.py,notes/coverage/scripts/veriput_subjects.py",
     "7/204 small bucket"),
    ("A12", "Stage4 PUT materialization/accounting",
     "notes/coverage/scripts/put_all.py,scripts/solidity_path_put.py",
     "no-PUT/no-R1R2 support"),
    ("A13", "R1 oracle ladder strengthening",
     "scripts/solidity_path_put.py", "PUT quality support"),
    ("A14", "R2 oracle ladder and fuzz-refute integration",
     "scripts/solidity_path_put.py", "PUT quality support"),
    ("A15", "unit scheduling priority and state/env coords",
     "notes/coverage/scripts/unit_schedule.py", "runner support"),
    ("A16", "runner Stage2 budget/Stage4 reserve",
     "notes/coverage/scripts/rq1_veriput_run.py", "56/204"),
    ("A17", "runner no-output/no-candidate continuation",
     "notes/coverage/scripts/rq1_veriput_run.py", "63/204"),
    ("A18", "runner result summarizer and stale artifact adoption",
     "notes/coverage/scripts/rq1_veriput_run.py", "actual RQ1 accounting"),
    ("A19", "remote worker/results sync automation",
     "notes/coverage/scripts/rq1_remote_pump.py", "resource utilization"),
    ("A20", "RQ1 progress ledger/audit script",
     "notes/coverage/scripts/rq1_no_valid_progress.py", "reporting integrity"),
    ("A21", "target manifest / subject discovery",
     "notes/coverage/scripts/target_manifest.py,"
     "notes/coverage/scripts/veriput_subjects.py", "schedule small bucket"),
    ("A22", "VeriPUT recipe option modeling",
     "notes/coverage/scripts/veriput_recipe.py", "PUT strength support"),
    ("A23", "Solidity AST dependency/path function resolution",
     "scripts/solidity_ast_dependencies.py,"
     "notes/coverage/scripts/unit_campaign_plan.py", "focus support"),
    ("A24", "final integration reviewer",
     "all touched files, no independent writes unless resolving conflicts",
     "integration"),
    ("A25", "historical result adoption canonicalization",
     "notes/coverage/scripts/rq1_results_adopt.py",
     "peer historical adoption"),
    ("A26", "remote pump build/sync stability",
     "notes/coverage/scripts/rq1_remote_pump.py",
     "resource utilization"),
    ("A27", "certify CE/region mismatch retry",
     "notes/coverage/scripts/certify_all.py",
     "certify-not-certified"),
    ("A28", "PUT R1/R2 quality strengthening",
     "scripts/solidity_path_put.py",
     "PUT/R1R2 quality support"),
    ("A29", "remaining ESBMC no-cov frontend/coverage fixes",
     "src/solidity-frontend/*.cpp,src/solidity-frontend/*.h,"
     "src/goto-programs/goto_coverage.cpp", "2/204 micro patch"),
    ("A30", "small no-valid runner/scheduler/materialization fixes",
     "notes/coverage/scripts/rq1_veriput_run.py,"
     "notes/coverage/scripts/certify_all.py,"
     "notes/coverage/scripts/put_all.py,"
     "notes/coverage/scripts/veriput_subjects.py", "5/204 micro patch"),
)

FIXED_MICRO_PATCH_COVERAGE = {
    "goto-coverage-no-claims-report": (
        3,
        "src/goto-programs/goto_coverage.cpp: no-claims entry-liveness audit "
        "continues to cov-report.json instead of aborting before emission",
    ),
    "not-certified-ce-pin-repair": (
        3,
        "scripts/solidity_path_generalise.py: incomplete/contaminated "
        "refutation payloads can degrade missing in-box coordinates to point "
        "pins instead of rejecting the path",
    ),
    "constructor-decl-duplicate-state-name": (
        1,
        "src/solidity-frontend/solidity_convert_decl.cpp: duplicate inherited "
        "state-variable source names get declaration-id-qualified component "
        "names so flattened contract structs do not collide",
    ),
    "a03-modifier-selector-call-fallback": (
        1,
        "src/solidity-frontend/solidity_convert_modifier.cpp and "
        "solidity_convert_call.cpp: selector/modifier formal type mismatches "
        "and low-level call parent-shape gaps degrade to typed nondet/tuple "
        "fallback instead of aborting before cov-report emission",
    ),
    "solidity-bytes-string-mapping-fallback-a04": (
        8,
        "src/solidity-frontend/solidity_convert_expr.cpp and "
        "solidity_convert_mapping.cpp: bytes/string literal/comparison and "
        "mapping helper gaps degrade to typed nondet/code_skip fallbacks "
        "instead of hard frontend failures",
    ),
    "a06-migrate-value-set-tuple-residual": (
        1,
        "src/util/migrate.cpp, src/pointer-analysis/value_set.cpp, and "
        "src/solvers/smt/tuple/smt_tuple_node.cpp: residual migrate/value-set/"
        "tuple hardfail fallbacks for the remaining migrate-expr no-cov case",
    ),
    "solidity-tuple-ref-fallbacks": (
        6,
        "src/solidity-frontend/solidity_convert_tuple.cpp and "
        "solidity_convert_ref.cpp: tuple/member/destructuring shape mismatches, "
        "selector shape, and missing aux ref symbols degrade to typed Solidity "
        "fallbacks instead of aborting before cov-report emission",
    ),
    "a29-solidity-type-meta-param-context": (
        2,
        "src/solidity-frontend/solidity_convert_stmt.cpp and "
        "solidity_convert_expr.cpp: empty parameter-conversion contexts and "
        "type(C).creationCode/runtimeCode/codehash meta-property accesses "
        "degrade to stable parameter ids / typed nondet values instead of "
        "asserting before cov-report emission",
    ),
    "a15-unit-schedule-cheap-first-state-coords": (
        17,
        "notes/coverage/scripts/unit_schedule.py: cheap state/env-coordinate "
        "units are scheduled before heavy units so early subject budget is not "
        "spent before reachable candidates are attempted",
    ),
    "a11-no-coordinate-subject-fallback": (
        7,
        "notes/coverage/scripts/put_all.py and veriput_subjects.py: "
        "parameterized state getters stay schedulable with calldata "
        "coordinates, while zero-parameter getter/deploy-only subjects get "
        "target-contract scoped concrete fallback materialization",
    ),
    "a09-path-cov-goal-cap-split-fallback": (
        2,
        "notes/coverage/scripts/certify_all.py: path-cov goal cap refusals "
        "retry with lower probe pressure and preserve replayable witness "
        "journal entries as concrete fallback without claiming certification",
    ),
    "a21-target-manifest-subject-discovery": (
        6,
        "notes/coverage/scripts/target_manifest.py and veriput_subjects.py: "
        "target contract discovery enforces peer contract080 and turns empty "
        "target-unit schedules into actionable retry classifications",
    ),
    "a10-no-path-bounded-holds-retry": (
        3,
        "notes/coverage/scripts/certify_all.py: no-path and empty "
        "bounded-holds results retry with a narrower bounded configuration and "
        "surface replayable witness journals as concrete fallback without "
        "claiming proof",
    ),
    "a30-small-no-valid-runner-materialization": (
        5,
        "notes/coverage/scripts/rq1_veriput_run.py and certify_all.py: "
        "canonical Stage4 summaries relocate moved local/remote artifact files, "
        "and empty bounded-holds retries consume driver retry_hint max-tx/scope/"
        "unwind instead of re-running the same empty bounded configuration",
    ),
}

FIXED_MICRO_PATCH_CATEGORIES = {
    "goto-coverage-no-claims-report": "ESBMC_NO_COV_REPORT_FRONTEND_OR_COVERAGE",
    "not-certified-ce-pin-repair": "CERTIFY_COUNTEREXAMPLE_REJECTED_NOT_CERTIFIED",
    "constructor-decl-duplicate-state-name":
        "ESBMC_NO_COV_REPORT_FRONTEND_OR_COVERAGE",
    "a03-modifier-selector-call-fallback":
        "ESBMC_NO_COV_REPORT_FRONTEND_OR_COVERAGE",
    "solidity-bytes-string-mapping-fallback-a04":
        "ESBMC_NO_COV_REPORT_FRONTEND_OR_COVERAGE",
    "a06-migrate-value-set-tuple-residual":
        "ESBMC_NO_COV_REPORT_FRONTEND_OR_COVERAGE",
    "solidity-tuple-ref-fallbacks": "ESBMC_NO_COV_REPORT_FRONTEND_OR_COVERAGE",
    "a29-solidity-type-meta-param-context":
        "ESBMC_NO_COV_REPORT_FRONTEND_OR_COVERAGE",
    "a15-unit-schedule-cheap-first-state-coords":
        "RUNNER_FIRST_UNITS_CONSUMED_SUBJECT_BUDGET",
    "a11-no-coordinate-subject-fallback": "CERTIFY_NO_COORDINATE_NO_PUT_REGION",
    "a09-path-cov-goal-cap-split-fallback":
        "CERTIFY_PATH_COV_GOAL_CAP_REFUSAL",
    "a21-target-manifest-subject-discovery": "SCHEDULE_NO_TARGET_UNITS",
    "a10-no-path-bounded-holds-retry":
        "CERTIFY_NO_PATH_OR_BOUNDED_HOLDS_EMPTY",
    "a30-small-no-valid-runner-materialization":
        "UNCLASSIFIED_RESULT_SCHEMA_OR_ARTIFACT_MISSING",
}


@dataclass(frozen=True)
class PatchBatch:
    patch_id: str
    categories: tuple[str, ...]
    deadline_hours: float
    owner: str
    fix_target: str


PATCH_BATCHES = (
    PatchBatch(
        patch_id="runner-budget-stage4",
        categories=(
            "RUNNER_STAGE2_CONSUMED_STAGE4_BUDGET",
            "STAGE2_TIMEOUT_NO_STAGE4_MATERIALIZATION",
        ),
        deadline_hours=2.0,
        owner="main-agent",
        fix_target=(
            "notes/coverage/scripts/rq1_veriput_run.py: Stage2 cap, "
            "Stage4 reserve, timeout/partial witness materialization"),
    ),
    PatchBatch(
        patch_id="runner-no-output-continuation",
        categories=(
            "RUNNER_STAGE2_NO_OUTPUT_EARLY_STOP",
            "RUNNER_EARLY_STOP_AFTER_NO_CANDIDATE_PREFIX",
        ),
        deadline_hours=3.0,
        owner="main-agent",
        fix_target=(
            "notes/coverage/scripts/rq1_veriput_run.py: no-output and "
            "no-candidate early-stop policy"),
    ),
    PatchBatch(
        patch_id="esbmc-no-cov-report",
        categories=("ESBMC_NO_COV_REPORT_FRONTEND_OR_COVERAGE",),
        deadline_hours=6.0,
        owner="subagents+main-review",
        fix_target=(
            "src/solidity-frontend/* and src/goto-programs/goto_coverage.cpp: "
            "frontend fallback and cov-report emission"),
    ),
    PatchBatch(
        patch_id="certify-not-certified",
        categories=("CERTIFY_COUNTEREXAMPLE_REJECTED_NOT_CERTIFIED",),
        deadline_hours=8.0,
        owner="subagents+main-review",
        fix_target=(
            "notes/coverage/scripts/certify_all.py and "
            "scripts/solidity_path_generalise.py: witness-to-region mismatch"),
    ),
    PatchBatch(
        patch_id="small-buckets",
        categories=(
            "SCHEDULE_NO_TARGET_UNITS",
            "CERTIFY_NO_PATH_OR_BOUNDED_HOLDS_EMPTY",
            "UNCLASSIFIED_RESULT_SCHEMA_OR_ARTIFACT_MISSING",
            "CERTIFY_NO_WITNESS_UNDECIDED",
            "CERTIFY_PATH_COV_GOAL_CAP_REFUSAL",
            "CERTIFY_NO_COORDINATE_NO_PUT_REGION",
            "CERTIFY_NO_WITNESS_UNKNOWN_NO_CE",
        ),
        deadline_hours=10.0,
        owner="subagents+main-review",
        fix_target=(
            "veriput_subjects.py, certify_all.py, put_all.py, and "
            "rq1_veriput_run.py: small bucket fallbacks"),
    ),
)


def load_counts(tsv_path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    with tsv_path.open(newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        for row in reader:
            category = (row.get("category") or "").strip()
            if category:
                counts[category] += 1
    return counts


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def load_or_init_state(state_path: Path, deadline_hours: float) -> dict:
    now = time.time()
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except json.JSONDecodeError:
            state = {}
    else:
        state = {}
    if "start_ts" not in state or "deadline_ts" not in state:
        state = {
            "schema": "veriput-rq1-progress-state/v1",
            "start_ts": now,
            "deadline_ts": now + float(deadline_hours) * 3600.0,
            "deadline_hours": float(deadline_hours),
        }
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    return state


def countdown_fields(state: dict) -> dict:
    now = time.time()
    start = float(state.get("start_ts") or now)
    deadline = float(state.get("deadline_ts") or now)
    total = max(1.0, deadline - start)
    elapsed = max(0.0, now - start)
    remaining = max(0.0, deadline - now)
    overdue = max(0.0, now - deadline)
    return {
        "start_utc": _utc(start),
        "deadline_utc": _utc(deadline),
        "now_utc": _utc(now),
        "elapsed_s": round(elapsed, 3),
        "elapsed_h": round(elapsed / 3600.0, 3),
        "remaining_s": round(remaining, 3),
        "remaining_h": round(remaining / 3600.0, 3),
        "elapsed_fraction": round(elapsed / total, 4),
        "overdue_s": round(overdue, 3),
        "expired": remaining <= 0.0,
    }


def schedule_status(countdown: dict, applied: set[str]) -> dict:
    elapsed_h = float(countdown.get("elapsed_h") or 0.0)
    required_done = []
    missed = []
    upcoming = []
    for batch in PATCH_BATCHES:
        done = batch.patch_id in applied
        row = {
            "patch_id": batch.patch_id,
            "deadline_h": batch.deadline_hours,
            "done": done,
        }
        if elapsed_h >= batch.deadline_hours:
            required_done.append(row)
            if not done:
                missed.append(row)
        else:
            upcoming.append({
                **row,
                "remaining_to_deadline_h": round(
                    batch.deadline_hours - elapsed_h, 3),
            })
    status = "on_schedule"
    if countdown.get("expired"):
        status = "expired"
    elif missed:
        status = "behind_schedule"
    return {
        "status": status,
        "elapsed_h": round(elapsed_h, 3),
        "missed_patch_deadlines": missed,
        "required_done_by_now": required_done,
        "next_patch_deadlines": upcoming[:3],
    }


def load_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _subagent_template() -> list[dict]:
    agents = []
    for agent_id, task, write_scope, coverage in SUBAGENT_PLAN:
        agents.append({
            "slot": agent_id,
            "task": task,
            "write_scope": [
                item.strip()
                for item in write_scope.split(",")
                if item.strip()
            ],
            "expected_coverage": coverage,
            "mode": "write",
            "status": "queued",
            "patch_id": "",
        })
    return agents


def init_subagent_state(path: Path, force: bool = False) -> dict:
    existing = load_json_file(path, {"agents": []})
    existing_by_slot = {
        str(agent.get("slot")): agent
        for agent in existing.get("agents") or []
        if isinstance(agent, dict)
    }
    agents = []
    for template in _subagent_template():
        current = existing_by_slot.get(template["slot"])
        if current and not force:
            merged = dict(template)
            merged.update(current)
            agents.append(merged)
        else:
            agents.append(template)
    doc = {
        "schema": "veriput-rq1-subagents/v1",
        "status": "active",
        "max_concurrent_threads_per_session": DEFAULT_REQUIRED_SUBAGENTS,
        "agents": agents,
        "rules": {
            "write_scope": "subagents may edit only their exclusive write_scope",
            "progress": "only completed entries with non-empty patch_id count",
            "cross_review": (
                "Every write-mode completed patch starts as pending review and "
                "must be inspected by an independent review/integration agent "
                "before it is treated as fully integrated."),
            "analysis_required": (
                "Before editing, inspect prior failure artifacts plus owning "
                "source code. Do not run ESBMC/RQ1 to discover the bug. "
                "Completion must report inspected artifacts, inspected code, "
                "code-level root cause, fix target, and theoretical coverage."),
        },
    }
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


def record_subagent(
    path: Path,
    slot: str,
    status: str,
    patch_id: str,
    agent_id: str,
    note: str,
) -> dict:
    doc = init_subagent_state(path)
    now = time.time()
    for agent in doc.get("agents") or []:
        if agent.get("slot") != slot:
            continue
        agent["status"] = status
        if patch_id:
            agent["patch_id"] = patch_id
        if agent_id:
            agent["agent_id"] = agent_id
        if note:
            agent["note"] = note
        if status == "completed":
            agent["completed_ts"] = now
        else:
            agent["updated_ts"] = now
        break
    else:
        raise SystemExit(f"unknown subagent slot: {slot}")
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc


def applied_from_subagents(subagents: dict) -> set[str]:
    applied = set()
    for agent in subagents.get("agents") or []:
        if not isinstance(agent, dict):
            continue
        if agent.get("status") != "completed":
            continue
        patch_id = str(agent.get("patch_id") or "").strip()
        if patch_id:
            applied.add(patch_id)
    return applied


def merge_subagent_docs(primary: dict, extra: dict) -> dict:
    merged = dict(primary)
    merged["agents"] = list(primary.get("agents") or []) + list(
        extra.get("agents") or [])
    return merged


def subagent_summary(subagents: dict) -> dict:
    agents = [
        agent for agent in subagents.get("agents") or []
        if isinstance(agent, dict)
    ]
    by_status = Counter(str(agent.get("status") or "unknown") for agent in agents)
    return {
        "required_slots": DEFAULT_REQUIRED_SUBAGENTS,
        "defined_slots": len(agents),
        "active_or_completed": sum(
            1 for agent in agents
            if agent.get("status") in {"running", "completed"}),
        "completed": by_status.get("completed", 0),
        "running": by_status.get("running", 0),
        "queued": by_status.get("queued", 0),
        "by_status": dict(sorted(by_status.items())),
        "capacity_configured": int(
            subagents.get("max_concurrent_threads_per_session") or 0),
    }


def remote_probe(host: str, timeout_s: int = 5) -> dict:
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={timeout_s}",
        host,
        (
            "hostname; nproc; "
            "free -m | awk 'NR==2{print $7}'; "
            "pgrep -af 'rq1_veriput_run.py|certify_all.py|put_all.py|esbmc' "
            "| head -20 || true"
        ),
    ]
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s + 2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "host": host,
            "reachable": False,
            "error": str(exc),
            "probe_wall_s": round(time.time() - started, 3),
            "running_processes": [],
        }
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    running = lines[3:] if len(lines) > 3 else []
    return {
        "host": host,
        "reachable": proc.returncode == 0,
        "hostname": lines[0] if len(lines) > 0 else None,
        "nproc": lines[1] if len(lines) > 1 else None,
        "mem_available_mb": lines[2] if len(lines) > 2 else None,
        "running_processes": running,
        "rq1_or_esbmc_running": bool(running),
        "stderr": proc.stderr.strip(),
        "probe_wall_s": round(time.time() - started, 3),
    }


def local_mem_available_gib() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return round(int(line.split()[1]) / (1024.0 * 1024.0), 3)
    except (OSError, ValueError):
        return None
    return None


def remote_mem_available_gib(remote_live: dict) -> float | None:
    try:
        mb = float(remote_live.get("mem_available_mb"))
    except (TypeError, ValueError):
        return None
    return round(mb / 1024.0, 3)


HISTORICAL_RESULT_MARKERS = (
    ".redo.",
    ".superseded.",
    ".adopted_from_",
    ".incomplete.",
)


def is_historical_result_path(path: Path) -> bool:
    return any(
        marker in part
        for part in path.parts
        for marker in HISTORICAL_RESULT_MARKERS)


def rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def actual_rq1_progress(results_root: Path) -> dict:
    total = valid = put = r1r2 = 0
    by_dataset: dict[str, Counter[str]] = {}
    skipped = Counter()
    peer_expected = peer_contract080_subjects(DEFAULT_PEER_DATASET_ROOT)
    seen: dict[tuple[str, str], Path] = {}
    peer_observed_expected: set[str] = set()
    peer_unexpected: set[str] = set()
    for result in sorted(results_root.glob("*/subjects/*/result.json")):
        if is_historical_result_path(result):
            skipped["historical_subject_dir"] += 1
            continue
        try:
            rel_parts = result.relative_to(results_root).parts
        except ValueError:
            skipped["outside_results_root"] += 1
            continue
        if len(rel_parts) < 4 or rel_parts[1] != "subjects":
            skipped["unexpected_result_path"] += 1
            continue
        dataset = rel_parts[0]
        subject = rel_parts[2]
        if dataset == "peer182" and peer_expected and subject not in peer_expected:
            skipped["peer_not_contract080_expected"] += 1
            peer_unexpected.add(subject)
            continue
        key = (dataset, subject)
        previous = seen.get(key)
        if previous is not None:
            skipped["duplicate_canonical_result"] += 1
            try:
                if result.stat().st_mtime <= previous.stat().st_mtime:
                    continue
            except OSError:
                continue
        seen[key] = result
    for (dataset, subject), result in sorted(seen.items()):
        try:
            doc = json.loads(result.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            skipped["unreadable_or_invalid_result_json"] += 1
            continue
        row = doc.get("row") or doc
        put_doc = doc.get("put") or {}

        def as_int(obj: dict, key: str) -> int:
            try:
                return int(obj.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        v = max(as_int(row, "valid"), as_int(put_doc, "valid"))
        pv = max(as_int(row, "put_valid"), as_int(put_doc, "put_valid"))
        rv = max(
            as_int(row, "valid_put_with_R1_or_R2"),
            as_int(put_doc, "valid_put_with_R1_or_R2"),
        )
        bucket = by_dataset.setdefault(dataset, Counter())
        total += 1
        bucket["total"] += 1
        if dataset == "peer182":
            peer_observed_expected.add(subject)
        if v > 0:
            valid += 1
            bucket["valid"] += 1
        if pv > 0:
            put += 1
            bucket["put"] += 1
        if rv > 0:
            r1r2 += 1
            bucket["r1r2"] += 1
    return {
        "results_root": str(results_root),
        "subjects": total,
        "valid_cases": valid,
        "no_valid_cases": total - valid,
        "put_cases": put,
        "no_put_cases": total - put,
        "r1r2_cases": r1r2,
        "no_r1r2_cases": total - r1r2,
        "valid_rate": rate(valid, total),
        "put_rate_all": rate(put, total),
        "r1r2_rate_all": rate(r1r2, total),
        "put_rate_among_valid": rate(put, valid),
        "r1r2_rate_among_valid": rate(r1r2, valid),
        "valid_needed_for_100pct": total - valid,
        "put_needed_for_100pct_all": total - put,
        "put_needed_for_100pct_valid": max(0, valid - put),
        "r1r2_needed_for_100pct_all": total - r1r2,
        "r1r2_needed_for_100pct_valid": max(0, valid - r1r2),
        "skipped_subject_dirs": dict(sorted(skipped.items())),
        "peer_contract080_expected": len(peer_expected),
        "peer182_expected_subjects": len(peer_expected),
        "peer182_observed_expected_subjects": len(peer_observed_expected),
        "peer182_missing_expected_subjects":
            sorted(peer_expected - peer_observed_expected),
        "peer182_missing_expected_count":
            len(peer_expected - peer_observed_expected),
        "peer182_unexpected_subjects": sorted(peer_unexpected),
        "peer182_unexpected_subject_count": len(peer_unexpected),
        "by_dataset": {
            key: dict(value) for key, value in sorted(by_dataset.items())
        },
    }


def peer_contract080_subjects(peer_root: Path) -> set[str]:
    prefixes = {
        "CC-SolCBMC": "peer_ccsolbmc__",
        "SolTG": "peer_soltg__",
        "SynTest": "peer_syntest__",
        "SolAR": "peer_solar__",
    }
    out = set()
    for tool, prefix in prefixes.items():
        directory = peer_root / tool / "contracts_080"
        if not directory.is_dir():
            continue
        for sol in directory.glob("*.sol"):
            stem = sol.stem
            if tool == "SolAR":
                match = re.match(r"^(.*)_(\d+)$", stem)
                if match:
                    stem = f"{match.group(1)}__{match.group(2)}"
            out.add(prefix + stem)
    return out


def pid_alive(pid_value) -> bool:
    try:
        pid = int(pid_value)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def resource_maximization(
    subagents: dict,
    remote_state: dict,
    remote_live: dict,
    local_state: dict,
) -> dict:
    reasons = []
    sub_summary = subagent_summary(subagents)
    if sub_summary["defined_slots"] < DEFAULT_REQUIRED_SUBAGENTS:
        reasons.append(
            f"subagent_plan_incomplete:{sub_summary['defined_slots']}/"
            f"{DEFAULT_REQUIRED_SUBAGENTS}")
    if sub_summary["capacity_configured"] < DEFAULT_REQUIRED_SUBAGENTS:
        reasons.append(
            "subagent_capacity_below_24:"
            f"{sub_summary['capacity_configured']}")
    if sub_summary["running"] + sub_summary["queued"] + sub_summary[
            "completed"] < DEFAULT_REQUIRED_SUBAGENTS:
        reasons.append("subagent_slots_not_fully_accounted")

    worker = remote_state.get("worker") or {}
    worker_watchdog = worker.get("remote_watchdog") or {}
    if not worker.get("started"):
        reasons.append("remote_worker_not_started")
    if worker.get("started") and not worker.get("memory_watchdog"):
        reasons.append("remote_memory_watchdog_missing")
    if worker.get("started") and not worker.get("progress_watchdog"):
        reasons.append("remote_progress_watchdog_missing")
    if worker.get("started") and not worker_watchdog:
        reasons.append("remote_process_watchdog_missing")
    if remote_state.get("dry_run"):
        reasons.append("remote_worker_dry_run")
    if remote_live.get("skipped"):
        reasons.append("remote_probe_skipped")
    elif not remote_live.get("reachable"):
        reasons.append("remote_host_unreachable")
    elif not remote_live.get("rq1_or_esbmc_running"):
        reasons.append("remote_has_no_rq1_or_esbmc_process")
    case_parallel = int(worker.get("case_parallel") or 0)
    capacity_parallel = int(worker.get("capacity_case_parallel")
                            or (worker.get("capacity") or {}).get("case_parallel")
                            or 0)
    if worker.get("started") and capacity_parallel > 0 \
            and case_parallel < capacity_parallel:
        reasons.append(
            f"remote_case_parallel_below_capacity:{case_parallel}/"
            f"{capacity_parallel}")

    if not local_state.get("started"):
        reasons.append("local_worker_not_started")
    if local_state.get("started") and not local_state.get("memory_watchdog"):
        reasons.append("local_memory_watchdog_missing")
    if local_state.get("started") and not local_state.get("progress_watchdog"):
        reasons.append("local_progress_watchdog_missing")
    if local_state.get("started") and not local_state.get("process_watchdog"):
        reasons.append("local_process_watchdog_missing")
    local_pid_alive = pid_alive(local_state.get("pid"))
    if local_state.get("started") and local_state.get("pid") \
            and not local_pid_alive:
        reasons.append(f"local_worker_pid_not_alive:{local_state.get('pid')}")

    return {
        "maximized": not reasons,
        "reasons": reasons,
        "subagent_summary": sub_summary,
        "remote_worker_started": bool(worker.get("started")),
        "remote_live_running": bool(remote_live.get("rq1_or_esbmc_running")),
        "remote_case_parallel": case_parallel,
        "remote_capacity_case_parallel": capacity_parallel,
        "remote_memory_watchdog": bool(worker.get("memory_watchdog")),
        "remote_progress_watchdog": bool(worker.get("progress_watchdog")),
        "remote_process_watchdog": bool(worker_watchdog),
        "remote_mem_available_gib": remote_mem_available_gib(remote_live),
        "remote_mem_available_mb": remote_live.get("mem_available_mb"),
        "local_worker_started": bool(local_state.get("started")),
        "local_case_parallel": int(local_state.get("case_parallel") or 0),
        "local_memory_watchdog": bool(local_state.get("memory_watchdog")),
        "local_progress_watchdog": bool(local_state.get("progress_watchdog")),
        "local_process_watchdog": bool(local_state.get("process_watchdog")),
        "local_mem_available_gib": local_mem_available_gib(),
        "local_pid": local_state.get("pid"),
        "local_pid_alive": local_pid_alive,
    }


def patch_coverage(
    counts: Counter[str],
    applied: set[str],
) -> tuple[int, list[str], dict]:
    covered_categories: set[str] = set()
    batch_rows = []
    for batch in PATCH_BATCHES:
        if batch.patch_id in applied:
            covered_categories.update(batch.categories)
            batch_rows.append({
                "patch_id": batch.patch_id,
                "categories": list(batch.categories),
                "counted": sum(counts.get(category, 0)
                               for category in batch.categories),
            })
    covered = sum(counts.get(category, 0) for category in covered_categories)
    micro_rows = []
    for patch_id, (count, _reason) in FIXED_MICRO_PATCH_COVERAGE.items():
        if patch_id not in applied:
            continue
        category = FIXED_MICRO_PATCH_CATEGORIES.get(patch_id)
        if category in covered_categories:
            micro_rows.append({
                "patch_id": patch_id,
                "category": category,
                "counted": 0,
                "skipped_reason": "category_already_covered_by_batch",
            })
            continue
        covered += count
        micro_rows.append({
            "patch_id": patch_id,
            "category": category or "uncategorized",
            "counted": count,
        })
    details = {
        "batch_contributions": batch_rows,
        "micro_contributions": micro_rows,
        "dedupe_rule": (
            "micro patch coverage is counted only when its root-cause "
            "category is not already covered by a completed batch patch"),
        "capped_at_denominator": DENOMINATOR,
    }
    return min(covered, DENOMINATOR), sorted(covered_categories), details


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tsv", type=Path, default=DEFAULT_TSV)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--deadline-hours", type=float,
                        default=DEFAULT_DEADLINE_HOURS)
    parser.add_argument("--subagents", type=Path, default=DEFAULT_SUBAGENTS)
    parser.add_argument("--extra-subagents",
                        type=Path,
                        default=DEFAULT_EXTRA_SUBAGENTS)
    parser.add_argument("--remote-state", type=Path,
                        default=DEFAULT_REMOTE_STATE)
    parser.add_argument("--local-state", type=Path, default=DEFAULT_LOCAL_STATE)
    parser.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    parser.add_argument("--results-root", type=Path,
                        default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--no-remote-probe", action="store_true")
    parser.add_argument("--init-subagents", action="store_true")
    parser.add_argument("--force-init-subagents", action="store_true")
    parser.add_argument("--record-subagent-slot", default="")
    parser.add_argument("--record-subagent-status", default="")
    parser.add_argument("--record-subagent-patch-id", default="")
    parser.add_argument("--record-subagent-agent-id", default="")
    parser.add_argument("--record-subagent-note", default="")
    parser.add_argument(
        "--applied",
        default="",
        help="comma-separated patch ids that are implemented in code",
    )
    args = parser.parse_args()

    if args.init_subagents or args.force_init_subagents:
        init_subagent_state(args.subagents, force=args.force_init_subagents)
    if args.record_subagent_slot:
        if not args.record_subagent_status:
            raise SystemExit("--record-subagent-status is required")
        record_subagent(
            args.subagents,
            args.record_subagent_slot,
            args.record_subagent_status,
            args.record_subagent_patch_id,
            args.record_subagent_agent_id,
            args.record_subagent_note,
        )

    counts = load_counts(args.tsv)
    state = load_or_init_state(args.state, args.deadline_hours)
    countdown = countdown_fields(state)
    primary_subagents = load_json_file(args.subagents, {
        "status": "missing",
        "agents": [],
        "note": "subagent status file missing; update it when spawning agents",
    })
    extra_subagents = load_json_file(args.extra_subagents, {"agents": []})
    subagents = merge_subagent_docs(primary_subagents, extra_subagents)
    remote_state = load_json_file(args.remote_state, {
        "status": "missing",
        "note": "remote state file missing; remote runner is not hard-confirmed",
    })
    local_state = load_json_file(args.local_state, {
        "status": "missing",
        "note": "local state file missing; local runner is not hard-confirmed",
    })
    remote_live = (
        {"skipped": True}
        if args.no_remote_probe else remote_probe(args.remote_host)
    )
    actual = actual_rq1_progress(args.results_root)
    resources = resource_maximization(subagents, remote_state, remote_live,
                                      local_state)
    row_total = sum(counts.values())
    applied = {item.strip() for item in args.applied.split(",") if item.strip()}
    applied.update(applied_from_subagents(subagents))
    covered, covered_categories, coverage_details = patch_coverage(
        counts, applied)
    schedule = schedule_status(countdown, applied)

    print(f"denominator={DENOMINATOR}")
    print(f"tsv_rows={row_total}")
    if row_total != DENOMINATOR:
        print(f"row_denominator_delta={row_total - DENOMINATOR}")
    print(f"applied_patch_ids={','.join(sorted(applied)) or '<none>'}")
    print(f"theoretical_progress={covered}/{DENOMINATOR}")
    print("theoretical_progress_details:")
    print(json.dumps(coverage_details, indent=2, sort_keys=True))
    print("resource_maximization:")
    print(json.dumps(resources, indent=2, sort_keys=True))
    print("countdown:")
    for key, value in countdown.items():
        print(f"  {key}={value}")
    print("schedule_status:")
    print(json.dumps(schedule, indent=2, sort_keys=True))
    print("subagents:")
    print(json.dumps(subagents, indent=2, sort_keys=True))
    print("subagent_execution_rules:")
    print(json.dumps({
        "default_mode": "write_allowed_only_inside_exclusive_write_scope",
        "readonly_mode": "only when mode=readonly in status file",
        "theoretical_progress_rule": (
            "completed subagent patch_id in status file or --applied only"),
        "status_file": str(args.subagents),
    }, indent=2, sort_keys=True))
    print("remote_host:")
    print(json.dumps({
        "state_file": remote_state,
        "live_probe": remote_live,
    }, indent=2, sort_keys=True))
    print("local_worker:")
    print(json.dumps({
        "state_file": local_state,
    }, indent=2, sort_keys=True))
    print("actual_rq1_progress:")
    print(json.dumps(actual, indent=2, sort_keys=True))
    print("hard_requirements:")
    for item in HARD_REQUIREMENTS:
        print(f"  - {item}")
    print("resource_plan:")
    for item in RESOURCE_PLAN:
        print(f"  - {item}")
    print("subagent_24_plan:")
    for agent_id, task, write_scope, coverage in SUBAGENT_PLAN:
        print(f"  {agent_id}: task={task}")
        print(f"    write_scope={write_scope}")
        print(f"    expected_coverage={coverage}")
    print("category_counts:")
    for category, count in counts.most_common():
        print(f"  {count:3d}  {category}")
    print("patch_plan:")
    if FIXED_MICRO_PATCH_COVERAGE:
        print("micro_patch_plan:")
        for patch_id, (count, reason) in FIXED_MICRO_PATCH_COVERAGE.items():
            print(f"  {patch_id}: covers={count}, target={reason}")
    cumulative = 0
    planned_categories: set[str] = set()
    for batch in PATCH_BATCHES:
        planned_categories.update(batch.categories)
        cumulative = min(
            sum(counts.get(category, 0) for category in planned_categories),
            DENOMINATOR,
        )
        batch_count = sum(counts.get(category, 0) for category in batch.categories)
        print(
            f"  {batch.patch_id}: covers={batch_count}, "
            f"cumulative_if_done={cumulative}/{DENOMINATOR}, "
            f"deadline_h={batch.deadline_hours:g}, owner={batch.owner}")
        print(f"    target={batch.fix_target}")
    if covered_categories:
        print("covered_categories:")
        for category in covered_categories:
            print(f"  {category}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
