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


def _assignment_prompt(group: dict) -> str:
    subjects = group["subjects"][:8]
    subject_lines = "\n".join(
        f"- {item['bench']}/{item['subject']} category={item['category']} "
        f"original={item.get('original_category') or '<none>'} "
        f"valid={item.get('valid')} put={item.get('put_valid')} "
        f"r1r2={item.get('r1r2')} result={item.get('result_file') or item.get('subject_dir')}"
        for item in subjects)
    scopes = ", ".join(group["write_scope"])
    return f"""Read notes/coverage/scripts/rq1_subagent_prompt_rules.md first.
Do NOT run ESBMC, ctest, pytest, RQ1, certify_all, put_all,
solidity_path_put, or any benchmark case. Do NOT modify
/home/samson/workspace/VeriPUT/Datasets.

Autonomous repair task for bucket {group['bucket_key']}.
Write scope is exclusive: {scopes}.

Failure/weak-result subjects to inspect:
{subject_lines}

You must inspect the listed result/driver/put artifacts plus the owning source
code before editing. If the old theoretical coverage is contradicted by these
results, explain which coverage claim must be reduced. Patch only the assigned
write scope. Completion must include inspected artifacts, inspected code,
code-level root cause, changed paths, theoretical coverage delta (+/-), and
confirmation that Datasets were untouched."""


def build_dispatch(repair_tickets: Path, results_root: Path, no_valid_tsv: Path,
                   limit: int) -> dict:
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
        _canonical_weak_rows(results_root, root_causes, limit=max(limit, 0)))

    grouped: OrderedDict[str, dict] = OrderedDict()
    for row in rows:
        category = str(row.get("category") or "")
        scopes = list(row.get("suggested_write_scope") or _default_scopes(category))
        key = _scope_key(scopes, category)
        group = grouped.setdefault(key, {
            "bucket_key": key,
            "write_scope": scopes,
            "subjects": [],
            "priority": "normal",
        })
        group["subjects"].append(row)
        if row.get("priority") == "high" or not int(row.get("valid") or 0):
            group["priority"] = "high"
    assignments = []
    for key, group in grouped.items():
        group["subject_count"] = len(group["subjects"])
        group["prompt"] = _assignment_prompt(group)
        assignments.append(group)
        if limit > 0 and len(assignments) >= limit:
            break
    return {
        "schema": "veriput-rq1-repair-dispatch/v1",
        "generated_ts": time.time(),
        "repair_tickets": str(repair_tickets),
        "no_valid_tsv": str(no_valid_tsv),
        "results_root": str(results_root),
        "assignment_count": len(assignments),
        "assignments": assignments,
        "rule": (
            "Whenever assignment_count > 0, the main agent must spawn or reuse "
            "subagents for these assignments instead of manually handling every "
            "failure. If the subagent tool is at capacity, close completed "
            "agents first and record that capacity was the blocker."),
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
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    doc = build_dispatch(args.repair_tickets, args.results_root,
                         args.no_valid_tsv, args.limit)
    args.out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print(json.dumps(doc, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
