#!/usr/bin/env python3
"""Build autonomous subagent assignments from RQ1 repair feedback.

This script is intentionally deterministic: it does not spawn agents itself,
because the Codex subagent tool is outside the shell process.  It produces the
exact assignments/prompts that the main agent must dispatch whenever worker
feedback shows no-valid, no-PUT, no-R1/R2, or schema/artifact regressions.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import OrderedDict
from pathlib import Path


DEFAULT_REPAIR_TICKETS = Path("/tmp/veriput_rq1_repair_tickets.jsonl")
DEFAULT_DISPATCH_QUEUE = Path("/tmp/veriput_rq1_dispatch_queue.json")
DEFAULT_NO_VALID_TSV = Path("/tmp/veriput_no_valid_root_causes.tsv")
DEFAULT_RESULTS_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_LIMIT = 24
DEFAULT_SUBJECTS_PER_ASSIGNMENT = 4
DEFAULT_MIN_ASSIGNMENTS = 12


SCOPE_MAP = (
    ("src/solidity-frontend", "ESBMC_SOLIDITY_FRONTEND"),
    ("src/goto-programs/goto_coverage.cpp", "ESBMC_COVERAGE"),
    ("scripts/solidity_path_put.py", "PUT_ORACLE_QUALITY"),
    ("scripts/solidity_path_generalise.py", "GENERALISE_REGION"),
    ("notes/coverage/scripts/certify_all.py", "CERTIFIER"),
    ("notes/coverage/scripts/put_all.py", "PUT_MATERIALIZATION"),
    ("notes/coverage/scripts/rq1_veriput_run.py", "RQ1_RUNNER"),
    ("notes/coverage/scripts/unit_schedule.py", "UNIT_SCHEDULER"),
    ("notes/coverage/scripts/veriput_subjects.py", "SUBJECT_DISCOVERY"),
)


def _jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def _root_cause_index(path: Path) -> dict[tuple[str, str], dict]:
    rows: dict[tuple[str, str], dict] = {}
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return rows
    if not lines:
        return rows
    header = lines[0].split("\t")
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split("\t")
        row = {
            header[index]: fields[index] if index < len(fields) else ""
            for index in range(len(header))
        }
        bench = row.get("bench") or row.get("dataset") or ""
        subject = row.get("subject") or ""
        if bench and subject:
            rows[(bench, subject)] = row
    return rows


def _int(doc: dict, key: str) -> int:
    try:
        return int(doc.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _canonical_weak_rows(
    results_root: Path,
    root_causes: dict[tuple[str, str], dict],
    limit: int,
) -> list[dict]:
    rows = []
    seen = set()
    for result in sorted(results_root.glob("*/subjects/*/result.json")):
        path_s = str(result)
        if any(marker in path_s for marker in (
                ".redo.", ".superseded.", ".adopted_from_", ".incomplete.")):
            continue
        rel = result.relative_to(results_root).parts
        if len(rel) < 4 or rel[1] != "subjects":
            continue
        bench, subject = rel[0], rel[2]
        key = (bench, subject)
        if key in seen:
            continue
        seen.add(key)
        doc = _json(result)
        row = doc.get("row") if isinstance(doc.get("row"), dict) else doc
        put = doc.get("put") if isinstance(doc.get("put"), dict) else {}
        valid = max(_int(row, "valid"), _int(put, "valid"))
        put_valid = max(_int(row, "put_valid"), _int(put, "put_valid"))
        r1r2 = max(_int(row, "valid_put_with_R1_or_R2"),
                   _int(put, "valid_put_with_R1_or_R2"))
        if valid <= 0:
            category = "NO_VALID_AFTER_RUN"
        elif put_valid <= 0:
            category = "NO_PUT_MATERIALIZATION"
        elif r1r2 <= 0:
            category = "NO_R1R2_ORACLE"
        else:
            continue
        rows.append({
            "bench": bench,
            "subject": subject,
            "category": category,
            "original_category": root_causes.get(
                (bench, subject), {}).get("category"),
            "root_cause": root_causes.get((bench, subject), {}).get("fix_target"),
            "theoretical_action": (
                "subtract covered no-valid claim until a follow-up patch "
                "clears this canonical result" if valid <= 0 else
                "quality debt: valid result still needs PUT/R1/R2 repair"),
            "result_file": str(result),
            "valid": valid,
            "put_valid": put_valid,
            "r1r2": r1r2,
        })
        if limit > 0 and len(rows) >= limit:
            break
    return rows


def _scope_key(scopes: list[str], category: str) -> str:
    joined = " ".join(scopes)
    for needle, key in SCOPE_MAP:
        if needle in joined:
            return key
    if category.startswith("ESBMC_"):
        return "ESBMC_SOLIDITY_FRONTEND"
    if category in {"NO_PUT_MATERIALIZATION", "NO_R1R2_ORACLE"}:
        return "PUT_ORACLE_QUALITY"
    if "NO_COORDINATE" in category:
        return "PUT_MATERIALIZATION"
    if "SCHEDULE" in category:
        return "UNIT_SCHEDULER"
    return "RQ1_RUNNER"


def _row_priority(row: dict) -> tuple[int, str, str, str]:
    category = str(row.get("category") or "")
    result_bucket = str(row.get("result_bucket") or "")
    valid = int(row.get("valid") or 0)
    put_valid = int(row.get("put_valid") or 0)
    r1r2 = int(row.get("r1r2") or 0)
    if valid <= 0 or result_bucket == "NO_VALID_AFTER_RUN" or \
            category.startswith("ESBMC_"):
        rank = 0
    elif put_valid <= 0:
        rank = 1
    elif r1r2 <= 0:
        rank = 2
    else:
        rank = 3
    return (
        rank,
        category,
        str(row.get("bench") or ""),
        str(row.get("subject") or ""),
    )


def _default_scopes(category: str) -> list[str]:
    if category.startswith("ESBMC_"):
        return ["src/solidity-frontend/*.cpp", "src/goto-programs/goto_coverage.cpp"]
    if category == "NO_R1R2_ORACLE":
        return ["scripts/solidity_path_put.py"]
    if category == "NO_PUT_MATERIALIZATION":
        return ["scripts/solidity_path_put.py", "notes/coverage/scripts/put_all.py"]
    if "NOT_CERTIFIED" in category:
        return ["notes/coverage/scripts/certify_all.py",
                "scripts/solidity_path_generalise.py"]
    if "NO_COORDINATE" in category:
        return ["notes/coverage/scripts/put_all.py",
                "notes/coverage/scripts/veriput_subjects.py"]
    if "SCHEDULE" in category:
        return ["notes/coverage/scripts/unit_schedule.py",
                "notes/coverage/scripts/veriput_subjects.py"]
    return ["notes/coverage/scripts/rq1_veriput_run.py",
            "notes/coverage/scripts/certify_all.py"]


def _repair_scopes(row: dict) -> list[str]:
    """Choose repair ownership from current evidence, not stale ticket scope."""
    category = str(row.get("category") or row.get("result_bucket") or "")
    original = str(row.get("original_category")
                   or row.get("root_cause_category") or "")
    root = str(row.get("root_cause") or row.get("fix_target") or "").lower()
    if category == "NO_PUT_MATERIALIZATION":
        return ["notes/coverage/scripts/put_all.py",
                "scripts/solidity_path_put.py"]
    if category == "NO_R1R2_ORACLE":
        return ["scripts/solidity_path_put.py",
                "scripts/solidity_ast_dependencies.py"]
    evidence = " ".join((category, original, root))
    if "ESBMC_" in evidence or "frontend" in root or "cov-report" in root:
        return ["src/solidity-frontend/*.cpp",
                "src/goto-programs/goto_coverage.cpp"]
    if "RUNNER_" in evidence or "STAGE" in evidence or "budget" in root \
            or "early" in root or "materialization" in root:
        return ["notes/coverage/scripts/rq1_veriput_run.py",
                "notes/coverage/scripts/unit_schedule.py"]
    if "SCHEDULE_" in evidence or "target" in root or "subject" in root:
        return ["notes/coverage/scripts/unit_schedule.py",
                "notes/coverage/scripts/unit_campaign_plan.py",
                "notes/coverage/scripts/veriput_subjects.py"]
    if "NO_COORDINATE" in evidence or "getter" in root \
            or "dependency" in root:
        return ["scripts/solidity_ast_dependencies.py",
                "notes/coverage/scripts/put_all.py"]
    if "NOT_CERTIFIED" in evidence or "NO_PATH" in evidence \
            or "PATH_COV" in evidence or "certify" in root \
            or "region" in root:
        return ["scripts/solidity_path_generalise.py"]
    explicit = row.get("suggested_write_scope")
    if explicit:
        return list(explicit)
    return _default_scopes(category)


def _stable_group_key(scopes: list[str], category: str) -> str:
    return f"{_scope_key(scopes, category)}::{category or 'UNKNOWN'}"


def _dataset_band(bench: object) -> str:
    value = str(bench or "")
    if value.startswith("peer"):
        return "peer"
    if value.startswith("bugfix"):
        return "bugfix"
    if value.startswith(("real", "stress")):
        return "stress"
    return value or "unknown"


def _failure_family(row: dict) -> str:
    category = str(row.get("category") or "")
    original = str(row.get("original_category") or "")
    root = str(row.get("root_cause") or "").lower()
    subject = str(row.get("subject") or "")
    if category == "NO_VALID_AFTER_RUN":
        if "NOT_CERTIFIED" in original or "certify" in root:
            return "certify-not-certified"
        if "NO_PATH" in original or "no path" in root:
            return "certify-no-path"
        if "PATH_COV_GOAL_CAP" in original or "goal cap" in root:
            return "certify-path-cap"
        if "NO_WITNESS" in original or "witness" in root:
            return "certify-no-witness"
        if "ESBMC" in original:
            return "esbmc-no-valid"
        if "RUNNER" in original or "STAGE" in original:
            return "runner-no-valid"
        return "no-valid-other"
    if category == "NO_PUT_MATERIALIZATION":
        if "L1Block" in subject or "constant" in root or "pure" in root:
            return "no-put-static-or-constant"
        if "getter" in root:
            return "no-put-getter"
        if "CERTIFY" in original:
            return "no-put-after-certify"
        return "no-put-materialization"
    if category == "NO_R1R2_ORACLE":
        if "rollback" in root or "revert" in root:
            return "r1r2-rollback-frame"
        if "getter" in root:
            return "r1r2-getter"
        return "r1r2-oracle"
    return category.lower() or "unknown"


def _repair_group_key(scopes: list[str], category: str, row: dict) -> str:
    return "::".join((
        _scope_key(scopes, category),
        category or "UNKNOWN",
        _failure_family(row),
        _dataset_band(row.get("bench")),
    ))


def _scope_signature(scopes: list[str]) -> tuple[str, ...]:
    return tuple(sorted(str(item) for item in scopes))


def _assignment_prompt(group: dict) -> str:
    subjects = group["subjects"][:8]
    subject_lines = "\n".join(
        f"- {item['bench']}/{item['subject']} category={item['category']} "
        f"original={item.get('original_category') or '<none>'} "
        f"valid={item.get('valid')} put={item.get('put_valid')} "
        f"r1r2={item.get('r1r2')} result={item.get('result_file') or item.get('subject_dir')}"
        for item in subjects)
    scopes = ", ".join(group["write_scope"])
    mode = group.get("mode") or "write"
    if mode == "readonly_root_cause":
        mode_rule = (
            "Mode: readonly_root_cause. Do not edit files. Return a precise "
            "code-level root-cause report and minimal patch plan for the "
            "write-owner of this scope.")
    else:
        mode_rule = (
            "Mode: write-owner. Patch only the assigned write scope; do not "
            "overlap writes with other assignments.")
    return f"""Read notes/coverage/scripts/rq1_subagent_prompt_rules.md first.
Do NOT run ESBMC, ctest, pytest, RQ1, certify_all, put_all,
solidity_path_put, or any benchmark case. Do NOT modify
/home/samson/workspace/VeriPUT/Datasets.

Autonomous repair task for bucket {group['bucket_key']}.
Shard {group.get('shard_index', 1)}/{group.get('shard_count', 1)}.
Write scope is exclusive: {scopes}.
{mode_rule}

Failure/weak-result subjects to inspect:
{subject_lines}

You must inspect the listed result/driver/put artifacts plus the owning source
code before editing. If the old theoretical coverage is contradicted by these
results, explain which coverage claim must be reduced. Patch only the assigned
write scope. Completion must include inspected artifacts, inspected code,
code-level root cause, changed paths, theoretical coverage delta (+/-), and
confirmation that Datasets were untouched.

This assignment is an immediate intervention generated from failed or weak
worker feedback. Do not wait for any worker batch to finish. Your final report
must state:
1. failed_cases: the concrete subjects/artifacts inspected.
2. prior_theory_failure: which previous patch/category claim was contradicted.
3. code_fix: what code changed and why this is the failure path.
4. theory_delta: +N/-N no-valid or PUT/R1/R2 quality delta.
5. review_needed: the patch_id/scope that must be cross-reviewed before net
   theory can increase."""


def _split_group(group: dict, subjects_per_assignment: int) -> list[dict]:
    subjects = list(group.get("subjects") or [])
    if subjects_per_assignment <= 0 or len(subjects) <= subjects_per_assignment:
        group = dict(group)
        group["subject_count"] = len(subjects)
        group["bucket_subject_total"] = len(subjects)
        group["base_bucket_key"] = group["bucket_key"]
        group["shard_index"] = 1
        group["shard_count"] = 1
        group["prompt"] = _assignment_prompt(group)
        return [group]
    shards = []
    shard_count = (len(subjects) + subjects_per_assignment - 1) // \
        subjects_per_assignment
    for index in range(shard_count):
        shard = dict(group)
        shard["subjects"] = subjects[index * subjects_per_assignment:
                                    (index + 1) * subjects_per_assignment]
        shard["subject_count"] = len(shard["subjects"])
        shard["bucket_subject_total"] = len(subjects)
        shard["base_bucket_key"] = group["bucket_key"]
        shard["shard_index"] = index + 1
        shard["shard_count"] = shard_count
        shard["bucket_key"] = f"{group['bucket_key']}#{index + 1:02d}"
        shard["prompt"] = _assignment_prompt(shard)
        shards.append(shard)
    return shards


def _interleave_shards(groups: list[dict],
                       subjects_per_assignment: int,
                       limit: int) -> list[dict]:
    """Round-robin buckets so large feedback classes cannot starve others."""
    shard_lists = [
        _split_group(group, subjects_per_assignment)
        for group in groups
    ]
    assignments = []
    cursor = 0
    while any(cursor < len(shards) for shards in shard_lists):
        for shards in shard_lists:
            if cursor >= len(shards):
                continue
            assignments.append(shards[cursor])
            if limit > 0 and len(assignments) >= limit:
                return assignments
        cursor += 1
    return assignments


def _mark_write_modes(assignments: list[dict]) -> None:
    """Keep one writer per exact scope while still dispatching broad triage."""
    owners: set[tuple[str, ...]] = set()
    for assignment in assignments:
        signature = _scope_signature(assignment.get("write_scope") or [])
        if signature in owners:
            assignment["mode"] = "readonly_root_cause"
            assignment["write_conflict_rule"] = (
                "A prior assignment owns this exact write scope. This shard "
                "must not edit files; it should inspect artifacts/source and "
                "return root-cause findings for the write-owner.")
        else:
            owners.add(signature)
            assignment["mode"] = "write"
        assignment["prompt"] = _assignment_prompt(assignment)


def build_dispatch(repair_tickets: Path, results_root: Path, no_valid_tsv: Path,
                   limit: int, subjects_per_assignment: int,
                   min_assignments: int) -> dict:
    root_causes = _root_cause_index(no_valid_tsv)
    rows = []
    for ticket in _jsonl(repair_tickets):
        if not isinstance(ticket, dict):
            continue
        category = str(ticket.get("result_bucket") or ticket.get("category") or "")
        if not category:
            continue
        root = root_causes.get((str(ticket.get("bench") or ""),
                                str(ticket.get("subject") or "")), {})
        rows.append({
            **ticket,
            "category": category,
            "original_category": ticket.get("original_category")
            or root.get("category"),
            "root_cause": root.get("fix_target"),
            "result_file": ticket.get("subject_dir"),
        })
    rows.extend(
        _canonical_weak_rows(
            results_root,
            root_causes,
            limit=max(limit * max(1, subjects_per_assignment), 0),
        ))
    rows.sort(key=_row_priority)

    grouped: OrderedDict[str, dict] = OrderedDict()
    for row in rows:
        category = str(row.get("category") or "")
        scopes = _repair_scopes(row)
        key = _repair_group_key(scopes, category, row)
        group = grouped.setdefault(key, {
            "bucket_key": key,
            "scope_key": _scope_key(scopes, category),
            "category": category,
            "failure_family": _failure_family(row),
            "dataset_band": _dataset_band(row.get("bench")),
            "write_scope": scopes,
            "subjects": [],
            "priority": "normal",
        })
        group["subjects"].append(row)
        if row.get("priority") == "high" or not int(row.get("valid") or 0):
            group["priority"] = "high"
    total_weak_subjects = sum(
        len(group.get("subjects") or []) for group in grouped.values())
    assignments = _interleave_shards(
        list(grouped.values()), subjects_per_assignment,
        max(limit, min_assignments))
    _mark_write_modes(assignments)
    return {
        "schema": "veriput-rq1-repair-dispatch/v1",
        "generated_ts": time.time(),
        "repair_tickets": str(repair_tickets),
        "no_valid_tsv": str(no_valid_tsv),
        "results_root": str(results_root),
        "assignment_count": len(assignments),
        "min_assignment_target": min_assignments,
        "write_owner_count": sum(
            1 for item in assignments if item.get("mode") == "write"),
        "readonly_root_cause_count": sum(
            1 for item in assignments
            if item.get("mode") == "readonly_root_cause"),
        "base_bucket_count": len(grouped),
        "total_weak_subjects": total_weak_subjects,
        "assigned_subject_capacity": sum(
            int(item.get("subject_count") or 0) for item in assignments),
        "assignment_limit": limit,
        "assignments": assignments,
        "subjects_per_assignment": subjects_per_assignment,
        "rule": (
            "Whenever assignment_count > 0, the main agent must spawn or reuse "
            "subagents for these assignments instead of manually handling every "
            "failure. Assignments are split by write scope, result bucket, "
            "failure family, dataset band, and small shards so more than ten "
            "agents can investigate contradicted theory slices in parallel. "
            "If the subagent tool is at capacity, close completed agents first "
            "and record that capacity was the blocker. Only one write-owner "
            "may edit an exact write scope; duplicate-scope shards are "
            "readonly_root_cause to avoid write conflicts."),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repair-tickets",
                        type=Path,
                        default=DEFAULT_REPAIR_TICKETS)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--no-valid-tsv",
                        type=Path,
                        default=DEFAULT_NO_VALID_TSV)
    parser.add_argument("--out", type=Path, default=DEFAULT_DISPATCH_QUEUE)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--subjects-per-assignment",
                        type=int,
                        default=DEFAULT_SUBJECTS_PER_ASSIGNMENT)
    parser.add_argument("--min-assignments",
                        type=int,
                        default=DEFAULT_MIN_ASSIGNMENTS)
    args = parser.parse_args()
    doc = build_dispatch(args.repair_tickets, args.results_root,
                         args.no_valid_tsv, args.limit,
                         args.subjects_per_assignment,
                         args.min_assignments)
    args.out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print(json.dumps(doc, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
