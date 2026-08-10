#!/usr/bin/env python3
"""Build deterministic cross-review assignments for RQ1 subagent patches."""

from __future__ import annotations

import argparse
import json
import time
from collections import OrderedDict
from pathlib import Path


DEFAULT_SUBAGENTS = Path("/tmp/veriput_rq1_subagents.json")
DEFAULT_EXTRA_SUBAGENTS = Path("/tmp/veriput_rq1_extra_subagents.json")
DEFAULT_OUT = Path("/tmp/veriput_rq1_review_queue.json")
DEFAULT_REPAIR_TICKETS = Path("/tmp/veriput_rq1_repair_tickets.jsonl")
DEFAULT_RESULTS_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
PUT_REVIEW_PATCH_IDS = {
    "a12-stage4-put-materialization-accounting",
    "a13-r1-oracle-ladder-strengthening",
    "a14-r2-ladder-fuzz-refute",
    "a28-put-r1r2-quality",
    "r2-pinned-coordinate-region",
    "veriput-strong-r2-recipe",
    "ast-public-getter-dependencies",
}


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {"agents": []}


def _agents(*docs: dict) -> list[dict]:
    out: list[dict] = []
    for doc in docs:
        for agent in doc.get("agents") or []:
            if isinstance(agent, dict):
                out.append(agent)
    return out


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


def _scope_key(agent: dict) -> str:
    scope = " ".join(str(item) for item in agent.get("write_scope") or [])
    if "src/solidity-frontend" in scope or "src/goto-programs" in scope:
        return "ESBMC_FRONTEND_COVERAGE"
    if "rq1_veriput_run.py" in scope:
        return "RQ1_RUNNER"
    if "certify_all.py" in scope or "solidity_path_generalise.py" in scope:
        return "CERTIFIER"
    if "solidity_path_put.py" in scope or "put_all.py" in scope:
        return "PUT_QUALITY"
    if "veriput_subjects.py" in scope or "unit_schedule.py" in scope:
        return "SCHEDULER_SUBJECTS"
    return "MISC"


DEFAULT_MAX_PATCHES_PER_ASSIGNMENT = 3


def _is_write_patch(agent: dict) -> bool:
    """Completed code patches require review even if old records omitted mode."""
    if agent.get("status") != "completed":
        return False
    mode = str(agent.get("mode") or "").strip().lower()
    if mode == "readonly":
        return False
    if mode == "write":
        return True
    return bool(str(agent.get("patch_id") or "").strip()
                and (agent.get("write_scope") or []))


def _quality_debt_subjects(repair_tickets: Path,
                           results_root: Path,
                           limit: int = 18) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for ticket in _jsonl(repair_tickets):
        bucket = str(ticket.get("result_bucket") or ticket.get("category") or "")
        if bucket not in {"NO_PUT_MATERIALIZATION", "NO_R1R2_ORACLE"}:
            continue
        key = (str(ticket.get("bench") or ""), str(ticket.get("subject") or ""),
               bucket)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "bench": key[0],
            "subject": key[1],
            "bucket": bucket,
            "valid": ticket.get("valid"),
            "put": ticket.get("put_valid"),
            "r1r2": ticket.get("r1r2"),
            "result": ticket.get("result_file") or ticket.get("subject_dir"),
        })
        if len(rows) >= limit:
            return rows
    for result in sorted(results_root.glob("*/subjects/*/result.json")):
        rel = result.relative_to(results_root).parts
        if len(rel) < 4 or rel[1] != "subjects":
            continue
        bench, subject = rel[0], rel[2]
        try:
            doc = json.loads(result.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        row = doc.get("row") if isinstance(doc.get("row"), dict) else doc
        put_doc = doc.get("put") if isinstance(doc.get("put"), dict) else {}
        valid = int(row.get("valid") or put_doc.get("valid") or 0)
        put = int(row.get("put_valid") or put_doc.get("put_valid") or 0)
        r1r2 = int(row.get("valid_put_with_R1_or_R2")
                   or put_doc.get("valid_put_with_R1_or_R2") or 0)
        if valid <= 0 or (put > 0 and r1r2 > 0):
            continue
        bucket = "NO_PUT_MATERIALIZATION" if put <= 0 else "NO_R1R2_ORACLE"
        key = (bench, subject, bucket)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "bench": bench,
            "subject": subject,
            "bucket": bucket,
            "valid": valid,
            "put": put,
            "r1r2": r1r2,
            "result": str(result),
        })
        if len(rows) >= limit:
            return rows
    return rows


def _put_quality_prompt(group: dict) -> str:
    patch_lines = "\n".join(
        f"- {item.get('slot')} patch={item.get('patch_id')} "
        f"agent={item.get('agent_id')} scope={','.join(item.get('write_scope') or [])}"
        for item in group["patches"])
    debt_lines = "\n".join(
        f"- {item.get('bench')}/{item.get('subject')} bucket={item.get('bucket')} "
        f"valid={item.get('valid')} put={item.get('put')} "
        f"r1r2={item.get('r1r2')} result={item.get('result')}"
        for item in group["quality_debt_subjects"])
    return f"""Read notes/coverage/scripts/rq1_subagent_prompt_rules.md first.
Do NOT run ESBMC/RQ1/ctest/pytest. Do NOT run certify_all, put_all,
solidity_path_put, or benchmark cases. Do NOT touch Datasets.

{group['bucket_key']}: active quality review for PUT/R1/R2 patches:
a12/a13/a14/a28, r2-pinned-coordinate-region, veriput-strong-r2-recipe,
ast-public-getter-dependencies, and the PUT rollback frame patch.

Inspect scripts/solidity_path_put.py, notes/coverage/scripts/put_all.py,
notes/coverage/scripts/veriput_recipe.py, scripts/solidity_ast_dependencies.py,
canonical no-PUT/no-R1R2 tickets, and the listed diffs. Return verdicts and
which quality debts should be considered recoverable vs rejected.

Patches requiring quality review:
{patch_lines}

Quality-debt subjects to inspect:
{debt_lines}

Completion must say which patch_ids are accepted/rejected/needs-work, whether
their theoretical PUT/R1/R2 coverage should increase or decrease, and whether
each inspected debt is recoverable by code repair or should stay concrete/non-
parameterized."""


def _prompt(group: dict) -> str:
    lines = []
    for item in group["patches"][:12]:
        lines.append(
            f"- {item.get('slot')} patch={item.get('patch_id')} "
            f"agent={item.get('agent_id')} scope={','.join(item.get('write_scope') or [])}"
        )
    certify_focus = ""
    if str(group.get("bucket_key") or "").startswith("CERTIFIER"):
        certify_focus = """
AUTO-REVIEW-CERTIFY-001: active quality review for certify/generalise patches.
Review certify-not-certified, not-certified-ce-pin-repair, a08, a09, a10
against invalidated NOT_CERTIFIED/no-path/path-cap cases. Inspect
certify_all.py, solidity_path_generalise.py, repair tickets and canonical
result logs. Return verdict per patch_id and exact code-level missing fixes
needed to recover net theory.
"""
    return f"""Read notes/coverage/scripts/rq1_subagent_prompt_rules.md first.
Do NOT run ESBMC, ctest, pytest, RQ1, certify_all, put_all,
solidity_path_put, or any benchmark case. Do NOT modify
/home/samson/workspace/VeriPUT/Datasets.

Cross-review bucket {group['bucket_key']} shard {group.get('shard', 1)}.
Inspect the listed diffs plus adjacent shared call paths and the
progress-ledger coverage claims. Do not make unrelated edits. If a patch
conflicts with another patch or its theoretical coverage is contradicted by
canonical RQ1 results, report the patch_id and the coverage delta that must be
removed.
{certify_focus}

Patches requiring independent review:
{chr(10).join(lines)}

Completion MUST report this shape for every reviewed patch_id:
1. changed_code: what the previous subagent changed, with file/function refs.
2. prior_failure: why the previous round failed, naming concrete failed or weak
   RQ1 subjects/artifacts that contradicted the old theory.
3. correctness_argument: why the reviewed or newly proposed code is the right
   code-level fix, including the data/control path from artifact to source.
4. verdict: accepted / rejected / needs-work.
5. theory_delta: exact +N/-N no-valid coverage or +N/-N PUT/R1/R2 quality
   coverage. Use 0 when the patch is useful but not evidence-backed.
6. covered_cases: exact benchmark/subject cases with old result bucket that
   the positive delta covers. An accepted verdict requires this field and a
   positive, case-level +N delta; otherwise verdict MUST be needs-work.
7. next_action: if rejected/needs-work, name the repair bucket and exclusive
   write scope that must be dispatched immediately.

A review that does not explain changed_code, prior_failure,
correctness_argument, verdict, theory_delta, covered_cases, and next_action is invalid and
must keep review_status=pending."""


def _priority(agent: dict) -> tuple[int, str, str]:
    patch_id = str(agent.get("patch_id") or "")
    scope = " ".join(str(item) for item in agent.get("write_scope") or [])
    task = str(agent.get("task") or "")
    hot = (
        "runner" in patch_id
        or "rq1_veriput_run.py" in scope
        or "runner" in task.lower()
        or "final-concrete-fallback" in patch_id
        or "a18-runner-result-adoption-tags" in patch_id
    )
    return (0 if hot else 1, _scope_key(agent), patch_id)


def _shards(group: dict, max_patches: int) -> list[dict]:
    patches = sorted(group["patches"], key=_priority)
    max_patches = max(1, max_patches)
    out = []
    for index in range(0, len(patches), max_patches):
        shard = dict(group)
        shard["patches"] = patches[index:index + max_patches]
        shard["patch_count"] = len(shard["patches"])
        shard["shard"] = (index // max_patches) + 1
        shard["prompt"] = _prompt(shard)
        out.append(shard)
    return out


def _put_quality_shards(group: dict, max_patches: int) -> list[dict]:
    patches = sorted(group["patches"], key=_priority)
    max_patches = max(1, max_patches)
    out = []
    for index in range(0, len(patches), max_patches):
        shard = dict(group)
        shard["patches"] = patches[index:index + max_patches]
        shard["patch_count"] = len(shard["patches"])
        shard["shard"] = (index // max_patches) + 1
        shard["bucket_key"] = f"AUTO-REVIEW-PUT-{shard['shard']:03d}"
        shard["prompt"] = _put_quality_prompt(shard)
        out.append(shard)
    return out


def build_review_queue(subagents: Path, extra_subagents: Path,
                       max_patches_per_assignment: int,
                       repair_tickets: Path,
                       results_root: Path) -> dict:
    agents = _agents(_json(subagents), _json(extra_subagents))
    grouped: OrderedDict[str, dict] = OrderedDict()
    seen_review_keys: set[tuple[str, str]] = set()
    for agent in agents:
        if not _is_write_patch(agent):
            continue
        review_status = str(agent.get("review_status") or "pending")
        if review_status == "accepted":
            continue
        if int(agent.get("review_round") or 0) >= 1:
            # A needs-work/rejected patch is repaired into a new patch_id; the
            # original patch is never reviewed a second time.
            continue
        patch_id = str(agent.get("patch_id") or "")
        if patch_id in PUT_REVIEW_PATCH_IDS:
            continue
        dedupe_key = (patch_id, _scope_key(agent))
        if patch_id and dedupe_key in seen_review_keys:
            continue
        if patch_id:
            seen_review_keys.add(dedupe_key)
        key = _scope_key(agent)
        group = grouped.setdefault(key, {
            "bucket_key": key,
            "patches": [],
        })
        group["patches"].append(agent)
    assignments = []
    for group in grouped.values():
        assignments.extend(_shards(group, max_patches_per_assignment))
    put_patches = []
    seen_put_patch_ids = set()
    for agent in agents:
        patch_id = str(agent.get("patch_id") or "")
        if patch_id not in PUT_REVIEW_PATCH_IDS:
            continue
        if str(agent.get("review_status") or "pending") == "accepted":
            continue
        if int(agent.get("review_round") or 0) >= 1:
            continue
        if patch_id in seen_put_patch_ids:
            continue
        seen_put_patch_ids.add(patch_id)
        put_patches.append(agent)
    put_patches.append({
        "slot": "AUTO-PUT-QUALITY-001",
        "patch_id": "put-rollback-frame-oracle",
        "agent_id": "019feb87-5684-70e0-9872-df3698f07d43",
        "write_scope": [
            "scripts/solidity_path_put.py",
            "notes/coverage/scripts/put_all.py",
        ],
    })
    if put_patches:
        quality_group = {
            "bucket_key": "AUTO-REVIEW-PUT-001",
            "patches": put_patches,
            "patch_count": len(put_patches),
            "quality_debt_subjects": _quality_debt_subjects(
                repair_tickets, results_root),
        }
        quality_group["quality_debt_subject_count"] = len(
            quality_group["quality_debt_subjects"])
        assignments = (
            _put_quality_shards(quality_group, max_patches_per_assignment)
            + assignments)
    return {
        "schema": "veriput-rq1-review-dispatch/v1",
        "generated_ts": time.time(),
        "max_patches_per_assignment": max_patches_per_assignment,
        "assignment_count": len(assignments),
        "assignments": assignments,
        "rule": (
            "Every completed write-mode patch, including legacy completed "
            "records that have patch_id+write_scope but omitted mode, with "
            "review_status other than accepted and review_round=0 must be "
            "cross-reviewed once. A needs-work/rejected patch must receive a "
            "new patch_id before another review. Review "
            "assignments are sharded so pending patches cannot hide inside a "
            "coarse bucket. Net "
            "theoretical coverage must not count pending/rejected/needs-work "
            "patch_ids. A review may update theory only when it reports "
            "changed_code, prior_failure, correctness_argument, verdict, "
            "theory_delta, and next_action per patch_id."),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subagents", type=Path, default=DEFAULT_SUBAGENTS)
    parser.add_argument("--extra-subagents",
                        type=Path,
                        default=DEFAULT_EXTRA_SUBAGENTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--repair-tickets",
                        type=Path,
                        default=DEFAULT_REPAIR_TICKETS)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--max-patches-per-assignment",
                        type=int,
                        default=DEFAULT_MAX_PATCHES_PER_ASSIGNMENT)
    args = parser.parse_args()
    doc = build_review_queue(args.subagents, args.extra_subagents,
                             args.max_patches_per_assignment,
                             args.repair_tickets,
                             args.results_root)
    args.out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print(json.dumps(doc, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
