#!/usr/bin/env python3
"""Run a fixed RQ1 no-valid batch through the repo-managed supervisor."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import signal
import shlex
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace


HERE = Path(__file__).resolve().parent
SUPERVISOR = HERE / "rq1_worker_supervisor.py"
DEFAULT_INVENTORY = Path("notes/coverage/rq1_no_valid_each_case.json")
DEFAULT_RUN_ROOT = Path("notes/coverage/rq1_runs")
DEFAULT_REMOTE_HOST = "invmut-w2"
DEFAULT_RESULTS_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_MANUAL_MD = Path("notes/coverage/rq1_no_valid_manual_root_causes.md")
DEFAULT_LEDGER = Path("notes/coverage/rq1_batch_ledger.json")
DEFAULT_CASE_STATE = Path("notes/coverage/rq1_case_state.json")
DEFAULT_REPAIR_TICKETS = Path("/tmp/veriput_rq1_repair_tickets.jsonl")
DEFAULT_REMOTE_ESBMC = Path("/home/administrator/veriput_esbmc/repo")
DEFAULT_REMOTE_VERIPUT = Path("/home/administrator/VeriPUT")
DEFAULT_REMOTE_LD_LIBRARY_PATH = (
    "/home/administrator/veriput_esbmc/local-libs:"
    "/home/administrator/veriput_esbmc/lib"
)
HISTORICAL_RESULT_SUFFIX_RE = re.compile(
    r"(?P<canonical>.+?)(?P<suffix>\.redo\..+|\.superseded\..+|"
    r"\.adopted_from_.+|\.incomplete\..+)$")
PREVENTION_FIELD_RE = re.compile(r"prevent(?:ion)?", re.IGNORECASE)
PREVENTION_PATH_RE = re.compile(
    r"(?:`([^`]+)`|"
    r"((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
    r"\.(?:py|cpp|h|hpp|c|cc|md|json|jsonl|tsv)))")

STATE_ORDER = {
    "NO_VALID": 0,
    "VALID_NO_PUT": 1,
    "VALID_PUT_NO_R1R2": 2,
    "VALID_PUT_R1R2": 3,
}

GROUND_TRUTH_REQUIRED_FIELDS = (
    "target_contract",
    "target_functions",
    "expected_units",
    "expected_path",
    "expected_region",
    "expected_oracle",
    "expected_r1r2",
    "root_cause",
    "last_failure",
    "why_static_missed",
    "prevention_code_change",
    "prevention_files_read",
    "fix_targets",
    "source_files_read",
    "evidence_files_read",
)


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")


def case_key(bench: str, subject: str) -> str:
    return f"{bench}/{subject}"


def load_case_state(args: argparse.Namespace) -> dict:
    doc = read_json(args.case_state)
    doc.setdefault("schema", "veriput-rq1-case-state/v1")
    doc.setdefault("initial_no_valid_baseline", 205)
    doc.setdefault("cases", {})
    return doc


def write_case_state(args: argparse.Namespace, doc: dict) -> None:
    doc["updated_ts"] = time.time()
    write_json(args.case_state, doc)


def case_state_row(args: argparse.Namespace, bench: str, subject: str) -> dict:
    doc = load_case_state(args)
    key = case_key(bench, subject)
    row = doc["cases"].setdefault(key, {
        "bench": bench,
        "subject": subject,
        "state": "NO_VALID",
        "history": [],
    })
    return row


def _non_empty(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def run_dir(args: argparse.Namespace) -> Path:
    return args.run_root / args.batch_id


def manifest_path(args: argparse.Namespace) -> Path:
    return run_dir(args) / "manifest.tsv"


def state_path(args: argparse.Namespace) -> Path:
    return run_dir(args) / "supervisor.json"


def lease_path(args: argparse.Namespace) -> Path:
    return run_dir(args) / "leases.json"


def _case_heading_pattern(bench: str, subject: str) -> re.Pattern:
    return re.compile(
        rf"^##+\s+\d+\.\s+{re.escape(bench)}/{re.escape(subject)}(?:\s|$)",
        re.MULTILINE,
    )


def _manual_section(manual_md: Path, bench: str, subject: str) -> str:
    try:
        text = manual_md.read_text(errors="replace")
    except OSError:
        return ""
    match = _case_heading_pattern(bench, subject).search(text)
    if not match:
        return ""
    next_match = re.search(r"^##+\s+\d+\.\s+", text[match.end():], re.MULTILINE)
    end = match.end() + next_match.start() if next_match else len(text)
    return text[match.start():end]


def _normalize_repo_path(token: str) -> str:
    token = token.strip().strip(".,;:()[]{}\"'")
    if not token:
        return ""
    if token.startswith("./"):
        token = token[2:]
    return token


def _path_token_is_relevant(token: str) -> bool:
    if not token:
        return False
    if token.startswith(("/tmp/", "http://", "https://")):
        return False
    suffixes = (
        ".py", ".cpp", ".h", ".hpp", ".c", ".cc", ".md", ".json", ".jsonl",
        ".tsv",
    )
    return "/" in token and token.endswith(suffixes)


def historical_prevention_references(args: argparse.Namespace) -> dict:
    """Collect every prevention/prevent field in md/state used by run gates."""
    md_entries = []
    files = set()
    issues = []
    for root in args.prevention_md_roots:
        for path in sorted(Path(root).glob("**/*.md")):
            try:
                lines = path.read_text(errors="replace").splitlines()
            except OSError:
                continue
            for lineno, line in enumerate(lines, 1):
                if not PREVENTION_FIELD_RE.search(line):
                    continue
                issue = {
                    "source": str(path),
                    "line": lineno,
                    "text": line.strip(),
                }
                issues.append(issue)
                for match in PREVENTION_PATH_RE.finditer(line):
                    token = _normalize_repo_path(match.group(1) or match.group(2) or "")
                    if _path_token_is_relevant(token):
                        files.add(token)
                        md_entries.append({
                            "source": str(path),
                            "line": lineno,
                            "path": token,
                        })

    state_doc = load_case_state(args)
    state_entries = []
    for key, row in sorted((state_doc.get("cases") or {}).items()):
        gt = row.get("ground_truth")
        if not isinstance(gt, dict):
            continue
        for field in ("prevention_files_read", "fix_targets"):
            for token in gt.get(field) or []:
                token = _normalize_repo_path(str(token))
                if _path_token_is_relevant(token):
                    files.add(token)
                    state_entries.append({
                        "case": key,
                        "field": field,
                        "path": token,
                    })
        for field in ("prevention_code_change", "why_static_missed",
                      "last_failure"):
            value = str(gt.get(field) or "")
            if PREVENTION_FIELD_RE.search(field) or PREVENTION_FIELD_RE.search(value):
                issues.append({
                    "source": str(args.case_state),
                    "case": key,
                    "field": field,
                    "text": value,
                })
                for match in PREVENTION_PATH_RE.finditer(value):
                    token = _normalize_repo_path(match.group(1) or match.group(2) or "")
                    if _path_token_is_relevant(token):
                        files.add(token)
                        state_entries.append({
                            "case": key,
                            "field": field,
                            "path": token,
                        })
    return {
        "schema": "veriput-rq1-historical-prevention-references/v1",
        "md_roots": [str(path) for path in args.prevention_md_roots],
        "issue_count": len(issues),
        "issues": issues,
        "file_count": len(files),
        "files": sorted(files),
        "md_entries": md_entries,
        "state_entries": state_entries,
    }


def ground_truth_status(args: argparse.Namespace, rows: list[dict]) -> dict:
    state_doc = load_case_state(args)
    cases = []
    ok = True
    for row in rows:
        bench = str(row.get("bench") or "")
        subject = str(row.get("subject") or "")
        key = case_key(bench, subject)
        state_row = state_doc["cases"].get(key) or {}
        structured = state_row.get("ground_truth")
        if not isinstance(structured, dict):
            structured = {}
        section = _manual_section(args.manual_md, bench, subject)
        missing = [
            field for field in GROUND_TRUTH_REQUIRED_FIELDS
            if not _non_empty(structured.get(field))
        ]
        case_ok = not missing
        ok = ok and case_ok
        cases.append({
            "bench": bench,
            "subject": subject,
            "state": state_row.get("state", "NO_VALID"),
            "has_section": bool(section),
            "has_structured_ground_truth": bool(structured),
            "missing": missing,
            "ok": case_ok,
        })
    return {
        "schema": "veriput-rq1-ground-truth-gate/v1",
        "manual_md": str(args.manual_md),
        "case_state": str(args.case_state),
        "case_count": len(cases),
        "ok": ok,
        "required_fields": GROUND_TRUTH_REQUIRED_FIELDS,
        "cases": cases,
    }


def seed_ground_truth(args: argparse.Namespace) -> dict:
    """Create structured ground-truth placeholders from the current manual MD.

    This does not mark a case ready.  It only gives the investigator a concrete
    JSON location to fill, avoiding keyword-based gates.
    """
    state = load_case_state(args)
    rows = inventory_rows(args)
    seeded = []
    for row in rows:
        bench = str(row.get("bench") or "")
        subject = str(row.get("subject") or "")
        key = case_key(bench, subject)
        slot = state["cases"].setdefault(key, {
            "bench": bench,
            "subject": subject,
            "state": "NO_VALID",
            "history": [],
        })
        gt = slot.setdefault("ground_truth", {})
        gt.setdefault("target_contract", "")
        gt.setdefault("target_functions", [])
        gt.setdefault("expected_units", [])
        gt.setdefault("expected_path", "")
        gt.setdefault("expected_region", "")
        gt.setdefault("expected_oracle", "")
        gt.setdefault("expected_r1r2", "")
        gt.setdefault("root_cause", "")
        gt.setdefault("last_failure", "")
        gt.setdefault("why_static_missed", "")
        gt.setdefault("prevention_code_change", "")
        gt.setdefault("prevention_files_read", [])
        gt.setdefault("fix_targets", [])
        gt.setdefault("source_files_read", [])
        gt.setdefault("evidence_files_read", [])
        gt.setdefault("manual_md_section_present",
                      bool(_manual_section(args.manual_md, bench, subject)))
        seeded.append(key)
    write_case_state(args, state)
    return {
        "schema": "veriput-rq1-ground-truth-seed/v1",
        "case_state": str(args.case_state),
        "seeded": seeded,
    }


def init_state(args: argparse.Namespace) -> dict:
    inventory = read_json(args.inventory)
    rows = inventory.get("rows") if isinstance(inventory.get("rows"), list) else []
    state = load_case_state(args)
    for row in rows:
        bench = str(row.get("bench") or "")
        subject = str(row.get("subject") or "")
        if not bench or not subject:
            continue
        key = case_key(bench, subject)
        slot = state["cases"].setdefault(key, {
            "bench": bench,
            "subject": subject,
            "state": "NO_VALID",
            "history": [],
        })
        slot.setdefault("bench", bench)
        slot.setdefault("subject", subject)
        slot.setdefault("state", "NO_VALID")
        slot.setdefault("history", [])
        slot.setdefault("inventory_result_json", row.get("result_json"))
        slot.setdefault("inventory_primary_reason", row.get("primary_reason"))
    state["initial_no_valid_baseline"] = len(rows)
    state["inventory"] = str(args.inventory)
    write_case_state(args, state)
    return {
        "schema": "veriput-rq1-case-state-init/v1",
        "case_state": str(args.case_state),
        "inventory": str(args.inventory),
        "case_count": len(rows),
    }


def state_summary(args: argparse.Namespace) -> dict:
    doc = load_case_state(args)
    inventory = read_json(args.inventory)
    inventory_rows = (inventory.get("rows")
                      if isinstance(inventory.get("rows"), list) else [])
    identities = []
    seen = set()
    for inventory_row in inventory_rows:
        bench = str(inventory_row.get("bench") or "")
        subject = str(inventory_row.get("subject") or "")
        key = case_key(bench, subject)
        if not bench or not subject or key in seen:
            continue
        seen.add(key)
        identities.append((bench, subject, key))
    cases = doc.get("cases") or {}
    rows = [
        cases.get(key) or {
            "bench": bench,
            "subject": subject,
            "state": "NO_VALID",
        } for bench, subject, key in identities
    ]
    counts = Counter(str(row.get("state") or "NO_VALID") for row in rows)
    missing_gt = []
    for row in rows:
        gt = row.get("ground_truth") if isinstance(row.get("ground_truth"), dict) else {}
        missing = [
            field for field in GROUND_TRUTH_REQUIRED_FIELDS
            if not _non_empty(gt.get(field))
        ]
        if missing:
            missing_gt.append({
                "bench": row.get("bench"),
                "subject": row.get("subject"),
                "missing": missing,
            })
    return {
        "schema": "veriput-rq1-case-state-summary/v1",
        "case_state": str(args.case_state),
        "case_count": len(rows),
        "state_counts": dict(sorted(counts.items())),
        "ground_truth_missing_count": len(missing_gt),
        "ground_truth_missing": missing_gt[:50],
    }


def sync_state_from_results(args: argparse.Namespace) -> dict:
    inventory = read_json(args.inventory)
    rows = inventory.get("rows") if isinstance(inventory.get("rows"), list) else []
    state_doc = load_case_state(args)
    changed = []
    counts = Counter()
    seen = set()
    for row in rows:
        bench = str(row.get("bench") or "")
        subject = str(row.get("subject") or "")
        if not bench or not subject:
            continue
        key = case_key(bench, subject)
        if key in seen:
            continue
        seen.add(key)
        subject_dir, result = best_result_for_subject(args, bench, subject)
        nums = result_numbers(result)
        bucket = quality_bucket(nums["valid"], nums["put"], nums["r1r2"])
        counts[bucket] += 1
        slot = state_doc["cases"].setdefault(key, {
            "bench": bench,
            "subject": subject,
            "state": "NO_VALID",
            "history": [],
        })
        old_state = str(slot.get("state") or "NO_VALID")
        slot.update({
            "bench": bench,
            "subject": subject,
            "state": bucket,
            "last_result_sync_ts": time.time(),
            "last_result_json": str(subject_dir / "result.json"),
            "last_result_subject_dir": str(subject_dir),
            "last_valid": nums["valid"],
            "last_put_valid": nums["put"],
            "last_r1r2": nums["r1r2"],
        })
        if old_state != bucket:
            event = {
                "ts": time.time(),
                "batch_id": args.batch_id,
                "source": "sync-results",
                "from": old_state,
                "to": bucket,
                "valid": nums["valid"],
                "put_valid": nums["put"],
                "r1r2": nums["r1r2"],
                "result_json": str(subject_dir / "result.json"),
                "result_subject_dir": str(subject_dir),
            }
            slot.setdefault("history", []).append(event)
            changed.append({
                "bench": bench,
                "subject": subject,
                "from": old_state,
                "to": bucket,
                "valid": nums["valid"],
                "put_valid": nums["put"],
                "r1r2": nums["r1r2"],
            })
    state_doc["last_results_sync_ts"] = time.time()
    write_case_state(args, state_doc)
    return {
        "schema": "veriput-rq1-case-state-results-sync/v1",
        "case_state": str(args.case_state),
        "results_root": str(args.results_root),
        "inventory": str(args.inventory),
        "case_count": len(seen),
        "state_counts": dict(sorted(counts.items())),
        "changed_count": len(changed),
        "changed": changed,
    }


def _full_ground_truth() -> dict:
    return {
        "target_contract": "Target",
        "target_functions": ["hit"],
        "expected_units": ["Target::hit"],
        "expected_path": "call hit once and reach the post-state oracle",
        "expected_region": "sender/value/state constrained by the source guard",
        "expected_oracle": "post-state equality oracle",
        "expected_r1r2": "R1 over argument coordinate, R2 over state coordinate",
        "root_cause": "synthetic audit fixture",
        "last_failure": "previous run reached Stage4 but produced no valid row",
        "why_static_missed": "audit fixture models evidence only visible in PUT summary",
        "prevention_code_change": "monitor reads Stage4 summary and gate requires postmortem fields",
        "prevention_files_read": ["notes/coverage/scripts/rq1_case_batch.py"],
        "fix_targets": ["notes/coverage/scripts/rq1_case_batch.py"],
        "source_files_read": ["src/Target.sol"],
        "evidence_files_read": ["result.json", "driver.log"],
    }


def _audit_base_args(args: argparse.Namespace, tmp: Path) -> SimpleNamespace:
    return SimpleNamespace(
        batch_id="audit-batch",
        inventory=tmp / "inventory.json",
        run_root=tmp / "runs",
        start_index=1,
        end_index=5,
        batch_size=5,
        require_batch_size=True,
        run_state=[],
        local_parallel=5,
        remote_parallel=3,
        remote_host="audit-host",
        remote_esbmc=tmp / "remote-esbmc",
        remote_veriput=tmp / "remote-veriput",
        timeout_s=600,
        local_memlimit_gib=8,
        remote_memlimit_gib=6.0,
        remote_reserve_mem_gib=2.0,
        local_rss_limit_gib=18,
        remote_rss_limit_gib=9.0,
        results_root=tmp / "results",
        manual_md=tmp / "manual.md",
        ledger=tmp / "ledger.json",
        case_state=tmp / "case_state.json",
        repair_tickets=tmp / "repair_tickets.jsonl",
        require_ground_truth=True,
        stop_on_hard_decision=False,
        update_manual_md=True,
        supervise_interval_s=30,
        supervise_timeout_s=7200,
        settle_after_supervise_stop=True,
        reset_leases=True,
        prevention_md_roots=[tmp / "notes"],
    )


def _audit_write_inventory(path: Path, count: int = 8) -> list[dict]:
    rows = [{
        "bench": "bugfix124",
        "subject": f"audit_subject_{idx:03d}",
        "primary_reason": "audit fixture",
        "result_json": "",
    } for idx in range(1, count + 1)]
    write_json(path, {"schema": "audit-inventory/v1", "count": count, "rows": rows})
    return rows


def _audit_ground_truth_gate(tmp: Path) -> tuple[bool, str]:
    audit_args = _audit_base_args(argparse.Namespace(), tmp)
    rows = _audit_write_inventory(audit_args.inventory, 5)
    init_state(audit_args)
    seed_ground_truth(audit_args)
    seeded_gate = ground_truth_status(audit_args, rows)
    if seeded_gate["ok"]:
        return False, "seed-ground-truth unexpectedly passed with placeholders"
    state = load_case_state(audit_args)
    for row in rows:
        state["cases"][case_key(row["bench"], row["subject"])]["ground_truth"] = (
            _full_ground_truth())
    write_case_state(audit_args, state)
    ready_gate = ground_truth_status(audit_args, rows)
    if not ready_gate["ok"]:
        return False, "full structured ground truth did not pass"
    short_args = _audit_base_args(argparse.Namespace(), tmp)
    short_args.end_index = 4
    try:
        inventory_rows(short_args)
    except SystemExit as exc:
        if "batch size gate failed" in str(exc):
            return True, "placeholder gate fails, full ground truth passes, short batch rejects"
        return False, f"unexpected batch gate failure: {exc}"
    return False, "short batch was accepted"


def _audit_preflight_and_oracle(tmp: Path) -> tuple[bool, str]:
    subject_dir = tmp / "results" / "bugfix124" / "subjects" / "audit_subject_001"
    subject_dir.mkdir(parents=True)
    gt = _full_ground_truth()
    write_json(subject_dir / "unit-schedule.json", {
        "contract": "Target",
        "jobs": [{
            "unit": "Target::hit",
            "unit_info": {"contract": "Target"},
            "priority_reason": "internal-target-wrapper",
            "sequence_strategy": "tx2",
        }],
    })
    ok_preflight = schedule_preflight(subject_dir, gt)
    if not ok_preflight["ok"] or ok_preflight["target_unit_hits"] != ["Target::hit"]:
        return False, "matching schedule did not pass preflight"
    write_json(subject_dir / "unit-schedule.json", {
        "contract": "Target",
        "jobs": [{
            "unit": "Other::hit",
            "unit_info": {"contract": "Other"},
        }],
    })
    bad_preflight = schedule_preflight(subject_dir, gt)
    if bad_preflight["ok"] or not bad_preflight["bad_contract_jobs"]:
        return False, "wrong-contract schedule was not rejected"
    summary = subject_dir / "put" / "Target__hit" / "put-summary.json"
    write_json(summary, {
        "deliverable_b": {
            "rows": [{
                "test": "test_put",
                "kind": "PUT",
                "valid_reference_test": True,
                "oracle_tags": ["R1"],
                "assertion_oracles": [{
                    "classes": ["R2"],
                    "class_combo": "R1+R2",
                    "var": "x",
                    "text": "assertEq(x, y)",
                    "coordinates": [{"name": "amount", "class": "R2"}],
                    "coordinate_classes": ["R2"],
                    "emitted_in_test": True,
                    "verdict": "certified",
                }],
            }],
        },
    })
    detail = oracle_detail_from_summary(summary)
    if detail["oracle_tag_counts"].get("R1", 0) < 1:
        return False, "R1 tag was not extracted"
    if detail["oracle_tag_counts"].get("R2", 0) < 1:
        return False, "R2 oracle class was not extracted"
    if detail["coordinate_counts"].get("amount") != 1:
        return False, "coordinate was not extracted"
    return True, "preflight accepts target schedule, rejects wrong contract, extracts R1/R2 coordinate"


def _audit_settle_side_effects(tmp: Path) -> tuple[bool, str]:
    audit_args = _audit_base_args(argparse.Namespace(), tmp)
    rows = _audit_write_inventory(audit_args.inventory, 5)
    init_state(audit_args)
    state = load_case_state(audit_args)
    for row in rows:
        state["cases"][case_key(row["bench"], row["subject"])]["ground_truth"] = (
            _full_ground_truth())
    write_case_state(audit_args, state)
    prepare(audit_args)
    for idx, row in enumerate(rows, start=1):
        subject_dir = audit_args.results_root / row["bench"] / "subjects" / row["subject"]
        subject_dir.mkdir(parents=True)
        if idx == 1:
            write_json(subject_dir / "result.json", {
                "valid": 1,
                "put_valid": 1,
                "r1r2": 1,
            })
        elif idx == 2:
            write_json(subject_dir / "result.json", {
                "valid": 1,
                "put_valid": 0,
                "r1r2": 0,
            })
        else:
            write_json(subject_dir / "result.json", {})
    first = settle(audit_args)
    second = settle(audit_args)
    state_after = read_json(audit_args.case_state)
    ledger = read_json(audit_args.ledger)
    manual = audit_args.manual_md.read_text(errors="replace")
    tickets = audit_args.repair_tickets.read_text(errors="replace").splitlines()
    full_key = case_key("bugfix124", "audit_subject_001")
    no_put_key = case_key("bugfix124", "audit_subject_002")
    no_valid_key = case_key("bugfix124", "audit_subject_003")
    if first["new_valid"] != 2 or first["new_put"] != 1 or first["new_r1r2"] != 1:
        return False, f"unexpected settlement counters: {first.get('bucket_counts')}"
    if second["case_count"] != 5:
        return False, "second settle did not keep batch shape"
    cases = state_after.get("cases") or {}
    if cases.get(full_key, {}).get("state") != "VALID_PUT_R1R2":
        return False, "full quality case was not promoted"
    if cases.get(no_put_key, {}).get("state") != "VALID_NO_PUT":
        return False, "valid-no-PUT case was not classified"
    if cases.get(no_valid_key, {}).get("state") != "NO_VALID":
        return False, "no-valid case was not retained"
    if "audit-batch" not in (ledger.get("batches") or {}):
        return False, "ledger did not record batch"
    if manual.count("RQ1_BATCH_SETTLEMENT_BEGIN audit-batch") != 1:
        return False, "manual settlement was not marker-upserted"
    if len(tickets) < 4:
        return False, "repair tickets missing for below-VALID_PUT_R1R2 cases"
    return True, "settle writes state/ledger/MD once and repair tickets for quality debt"


def _audit_rolling_fill(tmp: Path) -> tuple[bool, str]:
    audit_args = _audit_base_args(argparse.Namespace(), tmp)
    audit_args.run_state = ["NO_VALID"]
    rows = _audit_write_inventory(audit_args.inventory, 10)
    init_state(audit_args)
    state = load_case_state(audit_args)
    for idx, row in enumerate(rows, start=1):
        key = case_key(row["bench"], row["subject"])
        state["cases"][key]["ground_truth"] = _full_ground_truth()
        if idx <= 5:
            state["cases"][key]["state"] = "VALID_PUT_R1R2"
    write_case_state(audit_args, state)
    selected = inventory_rows(audit_args)
    selected_subjects = [row["subject"] for row in selected]
    expected = [f"audit_subject_{idx:03d}" for idx in range(6, 11)]
    if selected_subjects != expected:
        return False, f"rolling selected {selected_subjects}, expected {expected}"
    prepared = prepare(audit_args)
    if prepared["case_count"] != 5:
        return False, f"prepare wrote {prepared['case_count']} cases, expected 5"
    return True, "run-state rolling fill skips already-fixed rows and prepares exactly 5"


def _audit_historical_prevention_gate(tmp: Path) -> tuple[bool, str]:
    audit_args = _audit_base_args(argparse.Namespace(), tmp)
    audit_args.manual_md = tmp / "notes" / "manual.md"
    rows = _audit_write_inventory(audit_args.inventory, 8)
    prevent_file = tmp / "notes" / "coverage" / "scripts" / "prevent_fix.py"
    prevent_file.parent.mkdir(parents=True, exist_ok=True)
    prevent_file.write_text("# prevention fixture\n")
    batch_file = tmp / "notes" / "coverage" / "scripts" / "rq1_case_batch.py"
    batch_file.write_text("# batch fixture\n")
    audit_args.manual_md.parent.mkdir(parents=True, exist_ok=True)
    audit_args.manual_md.write_text(
        "## note\n"
        "- prevention_code_change: read `notes/coverage/scripts/prevent_fix.py`\n")
    init_state(audit_args)
    state = load_case_state(audit_args)
    for row in rows:
        key = case_key(row["bench"], row["subject"])
        gt = _full_ground_truth()
        gt["prevention_files_read"] = ["notes/coverage/scripts/rq1_case_batch.py"]
        gt["fix_targets"] = ["notes/coverage/scripts/rq1_case_batch.py"]
        state["cases"][key]["ground_truth"] = gt
    write_case_state(audit_args, state)
    cwd = Path.cwd()
    try:
        os.chdir(tmp)
        refs = historical_prevention_references(audit_args)
        if "notes/coverage/scripts/prevent_fix.py" not in refs["files"]:
            return False, f"historical prevention file was not detected: {refs}"
        first = pre_run_readiness(audit_args)
        hist_check = next(
            (row for row in first["checks"]
             if row["name"] == "all historical prevent/prevention files were read before rerun"),
            None,
        )
        if not hist_check or hist_check["ok"]:
            return False, "readiness accepted unread historical prevention file"
        state = load_case_state(audit_args)
        for row in rows:
            key = case_key(row["bench"], row["subject"])
            state["cases"][key]["ground_truth"]["prevention_files_read"].append(
                "notes/coverage/scripts/prevent_fix.py")
        write_case_state(audit_args, state)
        second = pre_run_readiness(audit_args)
        hist_check = next(
            (row for row in second["checks"]
             if row["name"] == "all historical prevent/prevention files were read before rerun"),
            None,
        )
        if not hist_check or not hist_check["ok"]:
            return False, f"readiness still rejected after recording read: {hist_check}"
    finally:
        os.chdir(cwd)
    return True, "historical md prevention files are detected and gate reruns"


def _audit_behavior() -> dict:
    checks = []
    with tempfile.TemporaryDirectory(prefix="rq1_case_batch_audit_") as tmp_s:
        tmp = Path(tmp_s)
        for item, name, fn in (
            (15, "behavior: ground-truth and batch gates",
             _audit_ground_truth_gate),
            (16, "behavior: scheduler preflight and oracle extraction",
             _audit_preflight_and_oracle),
            (17, "behavior: settlement state ledger tickets upsert",
             _audit_settle_side_effects),
            (18, "behavior: rolling run-state fill",
             _audit_rolling_fill),
            (19, "behavior: historical prevention readiness gate",
             _audit_historical_prevention_gate),
        ):
            try:
                ok, evidence = fn(tmp / f"check_{item}")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                ok, evidence = False, f"{type(exc).__name__}: {exc}"
            checks.append({
                "item": item,
                "name": name,
                "ok": bool(ok),
                "evidence": evidence,
            })
    return {"checks": checks, "ok": all(row["ok"] for row in checks)}


def audit(args: argparse.Namespace) -> dict:
    state = load_case_state(args)
    checks = []

    def add(item: int, name: str, ok: bool, evidence: str) -> None:
        checks.append({
            "item": item,
            "name": name,
            "ok": bool(ok),
            "evidence": evidence,
        })

    add(1, "fixed ledger/state machine",
        state.get("schema") == "veriput-rq1-case-state/v1"
        and int(state.get("initial_no_valid_baseline") or 0) == 205
        and len(state.get("cases") or {}) == 205,
        f"{args.case_state}: baseline={state.get('initial_no_valid_baseline')} "
        f"cases={len(state.get('cases') or {})}")
    add(2, "fixed-size rolling case batch gate",
        args.batch_size == 5 and args.require_batch_size,
        f"batch_size={args.batch_size} require_batch_size={args.require_batch_size}")
    add(3, "structured ground truth gate",
        tuple(GROUND_TRUTH_REQUIRED_FIELDS)
        and callable(ground_truth_status)
        and callable(seed_ground_truth),
        f"required_fields={','.join(GROUND_TRUTH_REQUIRED_FIELDS)}")
    add(4, "code repair before run enforced by gate",
        args.require_ground_truth,
        f"start refuses when ground_truth gate fails: require_ground_truth={args.require_ground_truth}")
    add(4.1, "prevention files must be read before rerun",
        "prevention_files_read" in GROUND_TRUTH_REQUIRED_FIELDS,
        "readiness requires prevention_files_read to exist, be readable, and cover fix_targets")
    add(4.2, "historical prevention scan is a readiness gate",
        callable(historical_prevention_references),
        "readiness scans md/state prevent/prevention references and refuses unread files")
    add(5, "scheduler preflight",
        callable(schedule_preflight),
        "schedule_preflight checks expected contract, expected units, wrong-contract jobs")
    add(6, "local5 remote3 execution",
        args.local_parallel == 5 and args.remote_parallel == 3,
        f"local_parallel={args.local_parallel} remote_parallel={args.remote_parallel}")
    add(6.1, "remote3 memory cap",
        args.remote_parallel == 3 and float(args.remote_memlimit_gib) == 6.0,
        f"remote_parallel={args.remote_parallel} remote_memlimit_gib={args.remote_memlimit_gib}")
    add(7, "continuous supervision",
        callable(monitor) and callable(supervise),
        "monitor/supervise report stage, cert tail, PUT summaries, preflight, memory")
    add(8, "hard early stop",
        callable(hard_stop_required) and callable(kill_subject_processes)
        and callable(kill_remote_subject_processes),
        "subject-filtered local and remote kill functions exist")
    add(9, "result classification",
        quality_bucket(0, 0, 0) == "NO_VALID"
        and quality_bucket(1, 0, 0) == "VALID_NO_PUT"
        and quality_bucket(1, 1, 0) == "VALID_PUT_NO_R1R2"
        and quality_bucket(1, 1, 1) == "VALID_PUT_R1R2",
        "quality_bucket maps valid/PUT/R1R2 to four states")
    add(9.1, "canonical row overrides stale artifact counts",
        result_numbers({
            "row": {
                "valid": 0,
                "put_valid": 0,
                "valid_put_with_R1_or_R2": 0,
            },
            "put": {
                "artifact_counts": {
                    "valid": 1,
                    "put_valid": 1,
                    "valid_put_with_R1_or_R2": 1,
                }
            },
        })["valid"] == 0,
        "explicit row.valid=0 is not promoted by historical artifacts")
    add(10, "writeback metadata capture",
        callable(oracle_details) and callable(oracle_detail_from_summary),
        "settle captures result.json, put summaries, oracle tags, coordinates")
    add(11, "failure rollback/repair ticket",
        bool(args.repair_tickets),
        f"settle writes repair ticket for every bucket below VALID_PUT_R1R2: {args.repair_tickets}")
    add(12, "quality debt queues represented",
        set(STATE_ORDER) == {
            "NO_VALID", "VALID_NO_PUT", "VALID_PUT_NO_R1R2", "VALID_PUT_R1R2"
        },
        f"states={sorted(STATE_ORDER)}")
    add(13, "batch settlement ledger",
        bool(args.ledger) and callable(settle) and callable(append_manual_settlement),
        f"settle writes settlement.json, {args.ledger}, {args.case_state}, and MD upsert")
    add(14, "forbidden actions gated",
        args.require_ground_truth and args.require_batch_size
        and not args.stop_on_hard_decision,
        "default start requires readiness, ground truth, batch size; monitor stop is explicit")
    behavior = _audit_behavior()
    checks.extend(behavior["checks"])

    return {
        "schema": "veriput-rq1-batch-automation-audit/v1",
        "ok": all(row["ok"] for row in checks),
        "checks": checks,
    }


def inventory_rows(args: argparse.Namespace) -> list[dict]:
    inventory = read_json(args.inventory)
    rows = inventory.get("rows") if isinstance(inventory.get("rows"), list) else []
    selected = []
    if args.run_state and args.require_batch_size:
        allowed = set(args.run_state or [])
        for absolute_index, row in enumerate(rows[args.start_index - 1:],
                                             args.start_index):
            row_copy = dict(row)
            row_copy["_absolute_index"] = absolute_index
            if _row_state(args, row_copy) not in allowed:
                continue
            selected.append(row_copy)
            if len(selected) >= args.batch_size:
                break
        if len(selected) != args.batch_size:
            raise SystemExit(
                f"rolling batch gate failed: found {len(selected)} "
                f"{sorted(allowed)} cases from index {args.start_index}, "
                f"expected {args.batch_size}")
        return selected
    selected = []
    for absolute_index, row in enumerate(rows[args.start_index - 1:args.end_index],
                                         args.start_index):
        row_copy = dict(row)
        row_copy["_absolute_index"] = absolute_index
        selected.append(row_copy)
    if len(selected) != args.end_index - args.start_index + 1:
        raise SystemExit("inventory range is incomplete")
    if args.require_batch_size and len(selected) != args.batch_size:
        raise SystemExit(
            f"batch size gate failed: selected {len(selected)} cases, "
            f"expected {args.batch_size}")
    return selected


def gate(args: argparse.Namespace) -> dict:
    return ground_truth_status(args, inventory_rows(args))


def _row_state(args: argparse.Namespace, row: dict) -> str:
    state_doc = load_case_state(args)
    key = case_key(str(row.get("bench") or ""), str(row.get("subject") or ""))
    state_row = state_doc.get("cases", {}).get(key) or {}
    return str(state_row.get("state") or "NO_VALID")


def runnable_rows(args: argparse.Namespace, rows: list[dict]) -> list[tuple[int, dict, str]]:
    allowed = set(args.run_state or [])
    out = []
    for fallback_index, row in zip(range(args.start_index,
                                         args.start_index + len(rows)), rows):
        absolute_index = int(row.get("_absolute_index") or fallback_index)
        state = _row_state(args, row)
        if allowed and state not in allowed:
            continue
        out.append((absolute_index, row, state))
    return out


def prepare(args: argparse.Namespace) -> dict:
    selected = inventory_rows(args)
    gt = ground_truth_status(args, selected)
    if args.require_ground_truth and not gt["ok"]:
        write_json(run_dir(args) / "ground-truth-gate.json", gt)
        raise SystemExit(
            "ground truth gate failed; write expected path/region/oracle/R1R2 "
            f"for every case in {args.manual_md} before running ESBMC")
    out = manifest_path(args)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("bench", "subject", "category", "theory_patch_id",
                        "state_before_run"),
            delimiter="\t",
        )
        writer.writeheader()
        runnable = runnable_rows(args, selected)
        for absolute_index, row, state in runnable:
            writer.writerow({
                "bench": row.get("bench"),
                "subject": row.get("subject"),
                "category": f"manual{absolute_index:03d}_{args.batch_id}",
                "theory_patch_id": args.batch_id,
                "state_before_run": state,
            })
    meta = {
        "schema": "veriput-rq1-case-batch/v1",
        "batch_id": args.batch_id,
        "inventory": str(args.inventory),
        "start_index": args.start_index,
        "end_index": args.end_index,
        "selected_case_count": len(selected),
        "case_count": len(runnable),
        "run_state_filter": list(args.run_state or []),
        "local_parallel": args.local_parallel,
        "remote_parallel": args.remote_parallel,
        "manifest": str(out),
        "state": str(state_path(args)),
        "lease_file": str(lease_path(args)),
        "run_dir": str(run_dir(args)),
        "prepared_ts": time.time(),
        "ground_truth_gate": gt,
    }
    write_json(run_dir(args) / "batch.json", meta)
    return meta


def supervisor_cmd(args: argparse.Namespace, command: str) -> list[str]:
    return [
        sys.executable,
        str(SUPERVISOR),
        command,
        "--manifest",
        str(manifest_path(args)),
        "--state",
        str(state_path(args)),
        "--run-dir",
        str(run_dir(args)),
        "--lease-file",
        str(lease_path(args)),
        "--remote-host",
        args.remote_host,
        "--remote-esbmc",
        str(args.remote_esbmc),
        "--remote-veriput",
        str(args.remote_veriput),
        "--local-parallel",
        str(args.local_parallel),
        "--remote-parallel",
        str(args.remote_parallel),
        "--timeout-s",
        str(args.timeout_s),
        "--local-memlimit-gib",
        str(args.local_memlimit_gib),
        "--remote-memlimit-gib",
        str(args.remote_memlimit_gib),
        "--remote-reserve-mem-gib",
        str(args.remote_reserve_mem_gib),
        "--local-rss-limit-gib",
        str(args.local_rss_limit_gib),
        "--remote-rss-limit-gib",
        str(args.remote_rss_limit_gib),
    ]


def command_json(cmd: list[str]) -> dict:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, check=False)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"stdout": proc.stdout}
    payload.update({"returncode": proc.returncode, "stderr": proc.stderr.strip()})
    return payload


def remote_preflight_snapshot(args: argparse.Namespace) -> dict:
    required_mem_gib = (
        float(args.remote_parallel) * float(args.remote_memlimit_gib)
        + float(args.remote_reserve_mem_gib)
    )
    if int(args.remote_parallel) <= 0:
        return {
            "host": args.remote_host,
            "remote_esbmc": str(args.remote_esbmc),
            "remote_veriput": str(args.remote_veriput),
            "remote_parallel": args.remote_parallel,
            "remote_memlimit_gib": args.remote_memlimit_gib,
            "remote_reserve_mem_gib": args.remote_reserve_mem_gib,
            "required_mem_gib": 0.0,
            "returncode": 0,
            "stdout": "remote preflight disabled because remote_parallel=0",
            "stderr": "",
            "ready": True,
            "disabled": True,
        }
    script = f"""
set -euo pipefail
export LD_LIBRARY_PATH={shlex.quote(DEFAULT_REMOTE_LD_LIBRARY_PATH)}:${{LD_LIBRARY_PATH:-}}
test -d {shlex.quote(str(args.remote_esbmc))}
test -d {shlex.quote(str(args.remote_veriput))}
test -f {shlex.quote(str(args.remote_esbmc / 'notes/coverage/scripts/rq1_veriput_run.py'))}
test -f {shlex.quote(str(args.remote_esbmc / 'notes/coverage/scripts/rq1_esbmc_result_interpret.py'))}
test -x {shlex.quote(str(args.remote_esbmc / 'build/src/esbmc/esbmc'))}
{shlex.quote(str(args.remote_esbmc / 'build/src/esbmc/esbmc'))} --version >/dev/null
available=$(awk '/MemAvailable/{{printf "%.3f", $2/1024/1024}}' /proc/meminfo)
python3 -c 'available=float("'"$available"'"); required=float("{required_mem_gib:.3f}"); import sys; sys.exit(0 if available >= required else 1)'
echo "remote-preflight-ok MemAvailable=${{available}}GiB required={required_mem_gib:.3f}GiB"
"""
    proc = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            args.remote_host,
            f"bash -lc {shlex.quote(script)}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return {
        "host": args.remote_host,
        "remote_esbmc": str(args.remote_esbmc),
        "remote_veriput": str(args.remote_veriput),
        "remote_parallel": args.remote_parallel,
        "remote_memlimit_gib": args.remote_memlimit_gib,
        "remote_reserve_mem_gib": args.remote_reserve_mem_gib,
        "required_mem_gib": round(required_mem_gib, 3),
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "ready": proc.returncode == 0,
    }


def pre_run_readiness(args: argparse.Namespace) -> dict:
    inventory_error = ""
    if manifest_path(args).exists():
        rows = manifest_rows(args)
    else:
        try:
            selected = inventory_rows(args)
            rows = [
                {
                    "bench": row.get("bench"),
                    "subject": row.get("subject"),
                    "category": f"manual{int(row.get('_absolute_index') or 0):03d}_{args.batch_id}",
                    "theory_patch_id": args.batch_id,
                    "state_before_run": _row_state(args, row),
                }
                for _, row, _ in runnable_rows(args, selected)
            ]
        except SystemExit as exc:
            rows = []
            inventory_error = str(exc)
    gt = ground_truth_status(args, rows) if rows else {
        "ok": False,
        "cases": [],
        "error": inventory_error,
    }
    monitor_doc = monitor(args)
    checks = []

    def add(name: str, ok: bool, evidence: object) -> None:
        checks.append({"name": name, "ok": bool(ok), "evidence": evidence})

    add("rolling batch has the configured runnable case count",
        len(rows) == args.batch_size and args.batch_size == 5 and not inventory_error,
        {
            "manifest_rows": len(rows),
            "batch_size": args.batch_size,
            "inventory_error": inventory_error,
        })
    if args.require_ground_truth:
        add("structured ground truth and failure postmortem complete",
            bool(gt.get("ok")),
            {"missing": [
                case for case in gt.get("cases") or [] if not case.get("ok")
            ]})
    else:
        add("structured ground truth and failure postmortem complete",
            True,
            {"disabled": True, "reason": "--no-require-ground-truth"})
    state_doc = load_case_state(args)
    prevention_failures = []
    if args.require_ground_truth:
        for row in rows:
            key = case_key(row["bench"], row["subject"])
            gt_row = ((state_doc.get("cases") or {}).get(key) or {}).get("ground_truth")
            if not isinstance(gt_row, dict):
                gt_row = {}
            prevention_files = [
                str(path) for path in gt_row.get("prevention_files_read") or []]
            fix_targets = [str(path) for path in gt_row.get("fix_targets") or []]
            missing_paths = [
                path for path in prevention_files
                if not Path(path).exists()
            ]
            uncovered_targets = [
                path for path in fix_targets
                if path not in prevention_files
            ]
            if not prevention_files or missing_paths or uncovered_targets:
                prevention_failures.append({
                    "case": key,
                    "prevention_files_read": prevention_files,
                    "fix_targets": fix_targets,
                    "missing_paths": missing_paths,
                    "fix_targets_not_read": uncovered_targets,
                })
        add("all prevention files from MD/state were read before rerun",
            not prevention_failures,
            prevention_failures)
    else:
        add("all prevention files from MD/state were read before rerun",
            True,
            {"disabled": True, "reason": "--no-require-ground-truth"})
    batch_prevention_files = set()
    for row in rows:
        key = case_key(row["bench"], row["subject"])
        gt_row = ((state_doc.get("cases") or {}).get(key) or {}).get("ground_truth")
        if not isinstance(gt_row, dict):
            gt_row = {}
        batch_prevention_files.update(
            _normalize_repo_path(str(path))
            for path in gt_row.get("prevention_files_read") or [])
    if args.require_ground_truth:
        historical_prevention = historical_prevention_references(args)
        historical_missing = [
            path for path in historical_prevention["files"]
            if not Path(path).exists()
        ]
        historical_unread = [
            path for path in historical_prevention["files"]
            if path not in batch_prevention_files
        ]
        add("all historical prevent/prevention files were read before rerun",
            not historical_missing and not historical_unread,
            {
                "md_roots": historical_prevention["md_roots"],
                "issue_count": historical_prevention["issue_count"],
                "required_file_count": historical_prevention["file_count"],
                "missing_paths": historical_missing,
                "not_in_current_batch_prevention_files_read": historical_unread,
            })
    else:
        add("all historical prevent/prevention files were read before rerun",
            True,
            {"disabled": True, "reason": "--no-require-ground-truth"})
    add("local/remote resource policy fixed",
        args.local_parallel == 5
        and int(args.local_memlimit_gib) == 8
        and (
            (args.remote_parallel == 3
             and float(args.remote_memlimit_gib) == 6.0
             and float(args.remote_reserve_mem_gib) == 2.0)
            or args.remote_parallel == 0),
        {
            "local_parallel": args.local_parallel,
            "remote_parallel": args.remote_parallel,
            "local_memlimit_gib": args.local_memlimit_gib,
            "remote_memlimit_gib": args.remote_memlimit_gib,
            "remote_reserve_mem_gib": args.remote_reserve_mem_gib,
        })
    bad_preflight = []
    for case in monitor_doc.get("cases") or []:
        preflight = case.get("scheduler_preflight") or {}
        if preflight.get("exists") and not preflight.get("ok"):
            bad_preflight.append({
                "subject": case.get("subject"),
                "preflight": preflight,
            })
    add("existing scheduler artifacts do not show wrong-target units",
        not bad_preflight,
        bad_preflight)
    add("30 second intervention supervision configured",
        args.supervise_interval_s == 30 and callable(supervise),
        {"supervise_interval_s": args.supervise_interval_s})
    remote = remote_preflight_snapshot(args)
    add("remote ESBMC/VeriPUT/memory preflight ready", remote["ready"], remote)
    doc = {
        "schema": "veriput-rq1-pre-run-readiness/v1",
        "batch_id": args.batch_id,
        "manifest": str(manifest_path(args)),
        "run_dir": str(run_dir(args)),
        "case_count": len(rows),
        "checks": checks,
        "ok": all(row["ok"] for row in checks),
    }
    write_json(run_dir(args) / "pre-run-readiness.json", doc)
    return doc


def manifest_rows(args: argparse.Namespace) -> list[dict]:
    path = manifest_path(args)
    if not path.exists():
        return []
    with path.open(newline="") as stream:
        return [
            row for row in csv.DictReader(stream, delimiter="\t")
            if row.get("bench") and row.get("subject")
        ]


def start(args: argparse.Namespace) -> dict:
    readiness = pre_run_readiness(args)
    if not readiness["ok"]:
        return {
            "started": False,
            "reason": "pre-run-readiness-failed",
            "readiness": readiness,
        }
    if not manifest_path(args).exists():
        prepare(args)
    state = read_json(state_path(args))
    live_workers = [
        row for row in state.get("workers") or []
        if isinstance(row.get("pid"), int) and Path(f"/proc/{row['pid']}").exists()
    ]
    if args.reset_leases and not live_workers:
        write_json(lease_path(args), {
            "schema": "veriput-rq1-case-leases/v1",
            "leases": {},
            "reset_ts": time.time(),
            "reset_by": "rq1_case_batch.py start",
        })
        for path in run_dir(args).glob("local_worker_*"):
            if path.suffix in {".jsonl", ".log", ".json"}:
                path.write_text("")
        remote_log = run_dir(args) / "remote_worker_supervisor.log"
        if remote_log.exists():
            remote_log.write_text("")
    return command_json(supervisor_cmd(args, "start"))


def stop(args: argparse.Namespace) -> dict:
    result = command_json(supervisor_cmd(args, "stop"))
    # Best-effort cleanup for child processes that survived process-group TERM.
    state = read_json(state_path(args))
    pids = []
    scope = str(run_dir(args))
    for worker in state.get("workers") or []:
        pid = worker.get("pid")
        if not isinstance(pid, int) or not Path(f"/proc/{pid}").exists():
            continue
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_text(errors="replace")
        except OSError:
            cmdline = ""
        if scope and scope not in cmdline.replace("\x00", " "):
            result.setdefault("skipped_out_of_scope_pids", []).append(pid)
            continue
        pids.append(pid)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    return result


def local_resource_snapshot() -> dict:
    mem_available = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                mem_available = round(int(line.split()[1]) / 1024 / 1024, 2)
                break
    except OSError:
        pass
    proc = subprocess.run(
        [
            "pgrep",
            "-af",
            "rq1_local_pump.py|rq1_remote_pump.py|rq1_veriput_run.py|certify_all.py|solidity_path_generalise.py|solidity_path_put.py|build/src/esbmc/esbmc",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    lines = [line for line in proc.stdout.splitlines() if "pgrep -af" not in line]
    return {
        "mem_available_gib": mem_available,
        "matching_process_count": len(lines),
        "matching_processes": lines[:50],
    }


def status(args: argparse.Namespace) -> dict:
    sup = command_json(supervisor_cmd(args, "status"))
    state = read_json(state_path(args))
    progress = []
    for path in sorted(run_dir(args).glob("local_worker_*_progress.jsonl")):
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            lines = []
        progress.append({"path": str(path), "events": len(lines),
                         "last": lines[-1] if lines else ""})
    return {
        "schema": "veriput-rq1-case-batch-status/v1",
        "batch_id": args.batch_id,
        "run_dir": str(run_dir(args)),
        "manifest": str(manifest_path(args)),
        "supervisor": sup,
        "state": state,
        "local_resources": local_resource_snapshot(),
        "local_progress": progress,
    }


def active_process_lines() -> list[str]:
    proc = subprocess.run(
        [
            "pgrep",
            "-af",
            (
                "rq1_local_pump.py|rq1_remote_pump.py|rq1_veriput_run.py|"
                "certify_all.py|put_all.py|solidity_path_generalise.py|"
                "solidity_path_put.py|build/src/esbmc/esbmc"
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return [line for line in proc.stdout.splitlines() if "pgrep -af" not in line]


def kill_subject_processes(subject: str) -> list[str]:
    killed = []
    for line in active_process_lines():
        if subject not in line:
            continue
        pid_s = line.split(None, 1)[0]
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
            except OSError:
                pass
            time.sleep(0.2)
        killed.append(line)
    return killed


def kill_remote_subject_processes(host: str, subject: str) -> dict:
    script = (
        "set +e\n"
        f"subject={shlex_quote(subject)}\n"
        "pgrep -af 'rq1_veriput_run.py|certify_all.py|put_all.py|"
        "solidity_path_generalise.py|solidity_path_put.py|build/src/esbmc/esbmc' "
        "| grep -F -- \"$subject\" > /tmp/veriput_kill_subject_before.txt\n"
        "awk '{print $1}' /tmp/veriput_kill_subject_before.txt | "
        "xargs -r kill -TERM 2>/dev/null\n"
        "sleep 1\n"
        "awk '{print $1}' /tmp/veriput_kill_subject_before.txt | "
        "xargs -r kill -KILL 2>/dev/null\n"
        "cat /tmp/veriput_kill_subject_before.txt\n"
    )
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return {
        "host": host,
        "subject": subject,
        "returncode": proc.returncode,
        "killed": [line for line in proc.stdout.splitlines() if line.strip()],
        "stderr": proc.stderr.strip(),
    }


def shlex_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def read_jsonl_tail(path: Path, limit: int = 5) -> list[dict]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"raw": line[:500]})
    return rows


def _is_valid_reference_test(test: dict) -> bool:
    """Apply the RQ1 replay-validity gate to one detailed artifact row."""
    stage2_source = str(test.get("stage2_source") or "").replace("_", "-")
    stage4_kind = str(test.get("stage4_kind") or "").replace("_", "-")
    if (stage2_source in ("no-unit-deploy-fallback", "structural-deploy-only")
            or stage4_kind in ("deploy-only", "creation-code-only")):
        return False
    return (test.get("valid_reference_test") is True and not test.get("stale")
            and test.get("refused") is not True)


def _detailed_test_rows(result: dict) -> list[dict]:
    rows = []
    for source in (result, result.get("row"), result.get("put"),
                   result.get("adoption")):
        if not isinstance(source, dict):
            continue
        for key in ("raw_tests", "valid_tests", "raw_artifacts",
                    "valid_artifacts"):
            value = source.get(key)
            if isinstance(value, list):
                rows.extend(test for test in value if isinstance(test, dict))
    deduped = {}
    for test in rows:
        identity = (
            str(test.get("file") or ""),
            str(test.get("test") or ""),
            str(test.get("kind") or ""),
            str(test.get("unit") or ""),
            str(test.get("enc") if test.get("enc") is not None else ""),
        )
        deduped[identity] = test
    return list(deduped.values())


def result_numbers(result: dict) -> dict:
    detailed = _detailed_test_rows(result)
    if detailed:
        valid_tests = [test for test in detailed if _is_valid_reference_test(test)]
        valid_puts = [test for test in valid_tests if test.get("kind") == "put"]
        r1r2 = [
            test for test in valid_puts
            if {"R1", "R2"} & set(test.get("oracle_classes") or [])
        ]
        return {
            "valid": len(valid_tests),
            "put": len(valid_puts),
            "r1r2": len(r1r2),
            "quality_bucket": quality_bucket(
                len(valid_tests), len(valid_puts), len(r1r2)),
        }

    adoption = result.get("adoption") if isinstance(result.get("adoption"), dict) else {}
    row = result.get("row") if isinstance(result.get("row"), dict) else {}
    put = result.get("put") if isinstance(result.get("put"), dict) else {}
    artifact_counts = (put.get("artifact_counts")
                       if isinstance(put.get("artifact_counts"), dict) else {})

    def current_count(key: str, *legacy_keys: str) -> int:
        """Read the canonical decision before historical artifact counters."""
        current = []
        for obj in (row, adoption):
            if key in obj:
                current.append(int(obj.get(key) or 0))
        if current:
            return max(current)
        for obj in (result, artifact_counts, put):
            for legacy_key in legacy_keys or (key, ):
                if legacy_key in obj:
                    return int(obj.get(legacy_key) or 0)
        return 0

    return {
        "valid": current_count("valid"),
        "put": current_count("put_valid"),
        "r1r2": current_count("valid_put_with_R1_or_R2", "r1r2",
                               "valid_put_with_R1_or_R2"),
        "quality_bucket": put.get("quality_bucket") or result.get("bucket"),
    }


def latest_put_summaries(subject_dir: Path, limit: int = 3) -> list[dict]:
    out = []
    paths = sorted((subject_dir / "put").glob("*/put-summary.json"),
                   key=lambda path: path.stat().st_mtime if path.exists() else 0)
    for path in paths[-limit:]:
        doc = read_json(path)
        deliverable = doc.get("deliverable_b") if isinstance(doc.get("deliverable_b"), dict) else {}
        quality = deliverable.get("quality") if isinstance(deliverable.get("quality"), dict) else {}
        rows = deliverable.get("rows")
        if isinstance(rows, list):
            valid_rows = [
                row for row in rows
                if isinstance(row, dict) and _is_valid_reference_test(row)
            ]
            put_rows = [row for row in valid_rows if row.get("kind") == "put"]
            r1r2_rows = [
                row for row in put_rows
                if {"R1", "R2"} & set(row.get("oracle_classes") or [])
            ]
            valid_count = len(valid_rows)
            put_count = len(put_rows)
            detailed_r1r2_count = len(r1r2_rows)
            aggregate_r1r2_count = int(quality.get("r1r2_rows") or 0)
            r1r2_count = min(
                put_count, max(detailed_r1r2_count, aggregate_r1r2_count))
        else:
            valid_count = quality.get("valid_reference_rows")
            put_count = quality.get("put_rows")
            r1r2_count = quality.get("r1r2_rows")
        out.append({
            "unit": path.parent.name,
            "b": deliverable.get("b"),
            "valid_reference_rows": valid_count,
            "put_rows": put_count,
            "r1r2_rows": r1r2_count,
        })
    return out


def put_summary_numbers(put_summaries: list[dict]) -> dict:
    """Promote Stage4 PUT summaries into the same boolean counters as result.json.

    `rq1_veriput_run.py` can finish Stage4 work before result.json reflects the
    strongest rows.  The supervisor and settlement code use this as evidence,
    while successful workers are still allowed to finish normally.
    """
    valid = 0
    put = 0
    r1r2 = 0
    for row in put_summaries:
        valid += int(row.get("valid_reference_rows") or 0)
        put += int(row.get("put_rows") or 0)
        r1r2 += int(row.get("r1r2_rows") or 0)
    return {
        "valid": valid,
        "put": put,
        "r1r2": r1r2,
    }


def merge_numbers(result_nums: dict, summary_nums: dict) -> dict:
    merged = dict(result_nums)
    for key in ("valid", "put", "r1r2"):
        merged[key] = max(int(result_nums.get(key) or 0),
                          int(summary_nums.get(key) or 0))
    if not merged.get("quality_bucket"):
        merged["quality_bucket"] = quality_bucket(
            merged["valid"],
            merged["put"],
            merged["r1r2"],
        )
    return merged


def recover_result_from_stage4_summary(subject_dir: Path, nums: dict,
                                       put_summaries: list[dict]) -> bool:
    if not nums.get("valid"):
        return False
    result_path = subject_dir / "result.json"
    result = read_json(result_path)
    current = result_numbers(result)
    if (current.get("valid") >= nums.get("valid")
            and current.get("put") >= nums.get("put")
            and current.get("r1r2") >= nums.get("r1r2")):
        return False
    bucket = quality_bucket(nums["valid"], nums["put"], nums["r1r2"])
    result.setdefault("schema", "veriput-rq1-result/v1")
    result.setdefault("status", "ok")
    result["valid"] = max(int(result.get("valid") or 0), int(nums["valid"] > 0))
    result["put_valid"] = max(int(result.get("put_valid") or 0),
                              int(nums["put"] > 0))
    result["r1r2"] = max(int(result.get("r1r2") or 0),
                         int(nums["r1r2"] > 0))
    result["bucket"] = bucket
    result["adoption"] = {
        "valid": int(nums["valid"] > 0),
        "put_valid": int(nums["put"] > 0),
        "valid_put_with_R1_or_R2": int(nums["r1r2"] > 0),
        "quality_bucket": bucket,
        "source": "rq1_case_batch.stage4_summary_recovery",
        "recovered_ts": time.time(),
    }
    put = result.setdefault("put", {})
    if isinstance(put, dict):
        put["quality_bucket"] = bucket
        artifact_counts = put.setdefault("artifact_counts", {})
        if isinstance(artifact_counts, dict):
            artifact_counts["valid"] = max(int(artifact_counts.get("valid") or 0),
                                           int(nums["valid"] > 0))
            artifact_counts["put_valid"] = max(
                int(artifact_counts.get("put_valid") or 0),
                int(nums["put"] > 0),
            )
            artifact_counts["valid_put_with_R1_or_R2"] = max(
                int(artifact_counts.get("valid_put_with_R1_or_R2") or 0),
                int(nums["r1r2"] > 0),
            )
        put["stage4_summary_recovery"] = {
            "valid_reference_rows": nums["valid"],
            "put_rows": nums["put"],
            "r1r2_rows": nums["r1r2"],
            "summaries": put_summaries,
        }
    write_json(result_path, result)
    return True


def oracle_detail_from_summary(path: Path) -> dict:
    doc = read_json(path)
    deliverable = doc.get("deliverable_b") if isinstance(doc.get("deliverable_b"), dict) else {}
    details = []
    tag_counts = Counter()
    coord_counts = Counter()
    for row in deliverable.get("rows") or []:
        if not isinstance(row, dict):
            continue
        if row.get("valid_reference_test") is not True:
            continue
        row_tags = set(str(tag) for tag in row.get("oracle_tags") or [])
        for oracle in row.get("assertion_oracles") or []:
            if not isinstance(oracle, dict):
                continue
            classes = [str(label) for label in oracle.get("classes") or []]
            coords = oracle.get("coordinates") if isinstance(oracle.get("coordinates"), list) else []
            for label in classes:
                tag_counts[label] += 1
                row_tags.add(label)
            for coord in coords:
                if isinstance(coord, dict):
                    coord_counts[str(coord.get("name") or "")] += 1
            details.append({
                "test": row.get("test"),
                "kind": row.get("kind"),
                "classes": classes,
                "class_combo": oracle.get("class_combo"),
                "var": oracle.get("var"),
                "text": oracle.get("text"),
                "coordinates": coords,
                "coordinate_classes": oracle.get("coordinate_classes") or [],
                "emitted_in_test": oracle.get("emitted_in_test"),
                "verdict": oracle.get("verdict"),
            })
        for tag in row_tags:
            tag_counts[tag] += 1
    return {
        "summary": str(path),
        "oracle_tag_counts": dict(sorted(tag_counts.items())),
        "coordinate_counts": dict(sorted((k, v) for k, v in coord_counts.items() if k)),
        "assertion_oracles": details,
    }


def oracle_details(subject_dir: Path) -> list[dict]:
    return [
        oracle_detail_from_summary(path)
        for path in sorted((subject_dir / "put").glob("*/put-summary.json"))
    ]


def schedule_preflight(subject_dir: Path, ground_truth: dict) -> dict:
    schedule = read_json(subject_dir / "unit-schedule.json")
    jobs = schedule.get("jobs") if isinstance(schedule.get("jobs"), list) else []
    expected_units = [
        str(unit) for unit in ground_truth.get("expected_units") or []
        if str(unit).strip()
    ]
    expected_contract = str(ground_truth.get("target_contract") or "")
    job_units = [str(job.get("unit") or "") for job in jobs if isinstance(job, dict)]
    target_hits = [unit for unit in expected_units if unit in job_units]
    bad_contract_jobs = [
        {
            "unit": job.get("unit"),
            "contract": (job.get("unit_info") or {}).get("contract"),
        }
        for job in jobs
        if isinstance(job, dict)
        and (job.get("unit_info") or {}).get("contract")
        and expected_contract
        and (job.get("unit_info") or {}).get("contract") != expected_contract
    ]
    wrapper_jobs = [
        {
            "unit": job.get("unit"),
            "priority_reason": job.get("priority_reason"),
            "sequence_strategy": job.get("sequence_strategy"),
        }
        for job in jobs
        if isinstance(job, dict)
        and job.get("priority_reason") == "internal-target-wrapper"
    ]
    ok = not bad_contract_jobs
    if jobs and expected_units:
        ok = ok and bool(target_hits)
    if jobs and expected_contract and schedule.get("contract"):
        ok = ok and str(schedule.get("contract")) == expected_contract
    return {
        "schedule": str(subject_dir / "unit-schedule.json"),
        "exists": bool(schedule),
        "job_count": len(jobs),
        "expected_contract": expected_contract,
        "schedule_contract": schedule.get("contract"),
        "expected_units": expected_units,
        "scheduled_units": job_units[:50],
        "target_unit_hits": target_hits,
        "bad_contract_jobs": bad_contract_jobs,
        "internal_target_wrappers": wrapper_jobs,
        "ok": ok,
    }


def infer_stage(result: dict, cert_path: Path, put_summaries: list[dict],
                active: list[str]) -> str:
    nums = merge_numbers(result_numbers(result), put_summary_numbers(put_summaries))
    if nums["valid"]:
        return "final/adopted"
    if put_summaries:
        return "Stage4/PUT"
    cert_rows = read_jsonl_tail(cert_path, limit=1)
    if cert_rows:
        return "Stage2/certify"
    if active:
        return "Stage1/wrapper"
    return "not-running/no-final-result"


def monitor_decision(nums: dict, cert_summary: dict, put_summaries: list[dict],
                     active: list[str]) -> tuple[str, str]:
    if nums["valid"] and nums["put"] and nums["r1r2"]:
        if active:
            return "继续跑", "已产出 valid PUT/R1/R2，等待 worker 正常落盘"
        return "已完成", "valid+PUT+R1/R2 已满足"
    if nums["valid"] and nums["put"]:
        return "转代码修复", "已有 valid PUT，但缺 R1/R2；继续跑同轮收益低"
    if nums["valid"]:
        return "转代码修复", "已有 valid 但不是 PUT；继续跑同轮不解决泛化"
    if not active and not nums["valid"]:
        return "转代码修复", "进程已结束且 no-valid"
    killed = int((cert_summary.get("bucket_counts") or {}).get("KILLED") or 0)
    certified = int(cert_summary.get("certified_regions") or 0)
    if put_summaries:
        stage4_nums = put_summary_numbers(put_summaries)
        if stage4_nums["valid"] and stage4_nums["put"] and stage4_nums["r1r2"]:
            if active:
                return "继续跑", "Stage4 已有 valid PUT/R1R2 候选，等待 worker 正常落盘"
            return "已完成", "Stage4 valid PUT/R1R2 候选已落入结算视图"
        if stage4_nums["valid"] and stage4_nums["put"]:
            return "转代码修复", "Stage4 已有 valid PUT 但缺 R1/R2"
        if stage4_nums["valid"]:
            return "转代码修复", "Stage4 只能产生 concrete valid，需修 PUT 泛化"
        if not active:
            return "转代码修复", "Stage4 已结束但所有候选 valid_rows=0"
    if killed >= 3 and certified == 0:
        return "建议停止", "Stage2 多个 KILLED 且无 certified region，继续跑大概率浪费"
    if active:
        return "继续跑", "仍有活进程且尚未出现终局失败信号"
    return "观察", "证据不足"


def hard_stop_required(case: dict) -> bool:
    decision = str(case.get("decision") or "")
    if decision == "建议停止":
        return True
    if decision == "转代码修复":
        return case.get("active_processes", 0) > 0
    return False


def monitor(args: argparse.Namespace) -> dict:
    rows = manifest_rows(args)
    processes = active_process_lines()
    cases = []
    for row in rows:
        bench = row["bench"]
        subject = row["subject"]
        subject_dir = args.results_root / bench / "subjects" / subject
        result = read_json(subject_dir / "result.json")
        put_summaries = latest_put_summaries(subject_dir)
        nums = merge_numbers(result_numbers(result),
                             put_summary_numbers(put_summaries))
        cert = result.get("certification") if isinstance(result.get("certification"), dict) else {}
        cert_path = subject_dir / "cert/certify-results.jsonl"
        cert_tail = read_jsonl_tail(cert_path, limit=3)
        if not cert and cert_tail:
            buckets = Counter(str(item.get("bucket") or "UNKNOWN") for item in cert_tail)
            cert = {
                "rows_seen_tail": len(cert_tail),
                "bucket_counts_tail": dict(buckets),
            }
        state_row = case_state_row(args, bench, subject)
        gt = state_row.get("ground_truth") if isinstance(
            state_row.get("ground_truth"), dict) else {}
        preflight = schedule_preflight(subject_dir, gt)
        active = [line for line in processes if subject in line]
        stage = infer_stage(result, cert_path, put_summaries, active)
        decision, reason = monitor_decision(nums, cert, put_summaries, active)
        if active and not preflight["ok"] and preflight["exists"]:
            decision = "建议停止"
            reason = "scheduler/preflight failed: target unit missing or wrong contract"
        cases.append({
            "bench": bench,
            "subject": subject,
            "stage": stage,
            "valid": nums["valid"],
            "put": nums["put"],
            "r1r2": nums["r1r2"],
            "quality_bucket": nums["quality_bucket"],
            "cert_rows": cert.get("rows"),
            "certified_regions": cert.get("certified_regions"),
            "cert_bucket_counts": cert.get("bucket_counts") or
                                  cert.get("bucket_counts_tail") or {},
            "timed_out_units": cert.get("timed_out_units") or [],
            "latest_cert": [
                {
                    "unit": item.get("unit"),
                    "bucket": item.get("bucket"),
                    "exit": item.get("exit"),
                    "progress": ((item.get("driver_diagnostic") or {}).get(
                        "progress_stage") if isinstance(item.get("driver_diagnostic"),
                                                        dict) else None),
                }
                for item in cert_tail
            ],
            "put_summaries": put_summaries,
            "stage4_valid_rows": put_summary_numbers(put_summaries)["valid"],
            "stage4_put_rows": put_summary_numbers(put_summaries)["put"],
            "stage4_r1r2_rows": put_summary_numbers(put_summaries)["r1r2"],
            "scheduler_preflight": preflight,
            "ground_truth_ready": not [
                field for field in GROUND_TRUTH_REQUIRED_FIELDS
                if not _non_empty(gt.get(field))
            ],
            "active_processes": len(active),
            "decision": decision,
            "decision_reason": reason,
        })
    doc = {
        "schema": "veriput-rq1-case-batch-monitor/v1",
        "batch_id": args.batch_id,
        "run_dir": str(run_dir(args)),
        "case_count": len(cases),
        "local_resources": local_resource_snapshot(),
        "cases": cases,
    }
    hard = [case for case in cases if hard_stop_required(case)]
    doc["hard_stop_required"] = bool(hard)
    doc["hard_stop_cases"] = [
        {
            "bench": case.get("bench"),
            "subject": case.get("subject"),
            "decision": case.get("decision"),
            "decision_reason": case.get("decision_reason"),
        }
        for case in hard
    ]
    if args.stop_on_hard_decision and hard:
        doc["stop_result"] = {
            "scope": "subjects",
            "killed": {
                case["subject"]: {
                    "local": kill_subject_processes(str(case["subject"])),
                    "remote": kill_remote_subject_processes(
                        args.remote_host, str(case["subject"])),
                }
                for case in hard
            },
        }
    return doc


def quality_bucket(valid: int, put: int, r1r2: int) -> str:
    if valid <= 0:
        return "NO_VALID"
    if put <= 0:
        return "VALID_NO_PUT"
    if r1r2 <= 0:
        return "VALID_PUT_NO_R1R2"
    return "VALID_PUT_R1R2"


def _result_strength(result: dict) -> tuple[int, int, int, int, int, int]:
    nums = result_numbers(result)
    return (
        1 if nums["valid"] > 0 else 0,
        1 if nums["put"] > 0 else 0,
        1 if nums["r1r2"] > 0 else 0,
        nums["valid"],
        nums["put"],
        nums["r1r2"],
    )


def _result_subject_matches(path: Path, subject: str) -> bool:
    name = path.name
    if name == subject:
        return True
    match = HISTORICAL_RESULT_SUFFIX_RE.match(name)
    return bool(match and match.group("canonical") == subject)


def best_result_for_subject(args: argparse.Namespace, bench: str,
                            subject: str) -> tuple[Path, dict]:
    parent = args.results_root / bench / "subjects"
    canonical_dir = parent / subject
    canonical_result = read_json(canonical_dir / "result.json")
    if canonical_result:
        return canonical_dir, canonical_result
    candidates = []
    if parent.exists():
        for subject_dir in parent.iterdir():
            if not subject_dir.is_dir() or not _result_subject_matches(
                    subject_dir, subject):
                continue
            result_path = subject_dir / "result.json"
            result = read_json(result_path)
            if result:
                try:
                    mtime = result_path.stat().st_mtime
                except OSError:
                    mtime = 0.0
                candidates.append((subject_dir, result, _result_strength(result), mtime))
    if not candidates:
        subject_dir = parent / subject
        return subject_dir, {}
    candidates.sort(key=lambda item: (item[2], item[3]), reverse=True)
    return candidates[0][0], candidates[0][1]


def settle(args: argparse.Namespace) -> dict:
    rows = manifest_rows(args)
    cases = []
    counters = Counter()
    state_doc = load_case_state(args)
    for row in rows:
        bench = row["bench"]
        subject = row["subject"]
        key = case_key(bench, subject)
        subject_dir = args.results_root / bench / "subjects" / subject
        result = read_json(subject_dir / "result.json")
        put_summaries = latest_put_summaries(subject_dir, limit=100)
        nums = merge_numbers(result_numbers(result),
                             put_summary_numbers(put_summaries))
        recovered = recover_result_from_stage4_summary(
            subject_dir, nums, put_summaries)
        bucket = quality_bucket(nums["valid"], nums["put"], nums["r1r2"])
        counters[bucket] += 1
        counters["valid"] += int(nums["valid"] > 0)
        counters["put"] += int(nums["put"] > 0)
        counters["r1r2"] += int(nums["r1r2"] > 0)
        state_row = state_doc["cases"].setdefault(key, {
            "bench": bench,
            "subject": subject,
            "state": "NO_VALID",
            "history": [],
        })
        preflight = schedule_preflight(
            subject_dir,
            state_row.get("ground_truth") if isinstance(
                state_row.get("ground_truth"), dict) else {},
        )
        oracle = oracle_details(subject_dir)
        case_doc = {
            "bench": bench,
            "subject": subject,
            "valid": nums["valid"],
            "put_valid": nums["put"],
            "r1r2": nums["r1r2"],
            "bucket": bucket,
            "result_json": str(subject_dir / "result.json"),
            "put_summaries": put_summaries[-10:],
            "recovered_from_stage4_summary": recovered,
            "oracle_details": oracle,
            "scheduler_preflight": preflight,
        }
        cases.append(case_doc)
        old_state = str(state_row.get("state") or "NO_VALID")
        state_row.update({
            "bench": bench,
            "subject": subject,
            "state": bucket,
            "last_settled_ts": time.time(),
            "last_batch_id": args.batch_id,
            "last_result_json": str(subject_dir / "result.json"),
            "last_valid": nums["valid"],
            "last_put_valid": nums["put"],
            "last_r1r2": nums["r1r2"],
            "last_scheduler_preflight": preflight,
            "last_oracle_details": oracle,
        })
        state_row.setdefault("history", []).append({
            "ts": time.time(),
            "batch_id": args.batch_id,
            "from": old_state,
            "to": bucket,
            "valid": nums["valid"],
            "put_valid": nums["put"],
            "r1r2": nums["r1r2"],
            "result_json": str(subject_dir / "result.json"),
            "recovered_from_stage4_summary": recovered,
        })
        if bucket != "VALID_PUT_R1R2":
            ticket = {
                "schema": "veriput-rq1-repair-ticket/v1",
                "ts": time.time(),
                "bench": bench,
                "subject": subject,
                "category": row.get("category"),
                "result_bucket": bucket,
                "valid": nums["valid"],
                "put_valid": nums["put"],
                "r1r2": nums["r1r2"],
                "subject_dir": str(subject_dir),
                "result_json": str(subject_dir / "result.json"),
                "reason": "batch settlement below VALID_PUT_R1R2",
                "rule": "no-valid/no-PUT/no-R1R2 must return to static investigation and code repair",
            }
            append_jsonl(args.repair_tickets, ticket)
            state_row.setdefault("repair_tickets", []).append(ticket)
    doc = {
        "schema": "veriput-rq1-batch-settlement/v1",
        "batch_id": args.batch_id,
        "settled_ts": time.time(),
        "inventory": str(args.inventory),
        "start_index": args.start_index,
        "end_index": args.end_index,
        "case_count": len(cases),
        "new_valid": counters["valid"],
        "new_put": counters["put"],
        "new_r1r2": counters["r1r2"],
        "bucket_counts": dict(counters),
        "cases": cases,
    }
    write_json(run_dir(args) / "settlement.json", doc)
    write_case_state(args, state_doc)
    ledger = read_json(args.ledger)
    ledger.setdefault("schema", "veriput-rq1-batch-ledger/v1")
    ledger.setdefault("batches", {})
    ledger["batches"][args.batch_id] = doc
    ledger["updated_ts"] = time.time()
    write_json(args.ledger, ledger)
    if args.update_manual_md:
        append_manual_settlement(args.manual_md, doc)
    return doc


def supervise_intervention_event(iteration: int, doc: dict) -> dict:
    return {
        "schema": "veriput-rq1-supervise-intervention/v1",
        "iteration": iteration,
        "ts": time.time(),
        "batch_id": doc.get("batch_id"),
        "hard_stop_required": doc.get("hard_stop_required"),
        "hard_stop_cases": doc.get("hard_stop_cases") or [],
        "local_resources": doc.get("local_resources") or {},
        "cases": [
            {
                "bench": case.get("bench"),
                "subject": case.get("subject"),
                "stage": case.get("stage"),
                "valid": case.get("valid"),
                "put": case.get("put"),
                "r1r2": case.get("r1r2"),
                "quality_bucket": case.get("quality_bucket"),
                "active_processes": case.get("active_processes"),
                "certified_regions": case.get("certified_regions"),
                "cert_bucket_counts": case.get("cert_bucket_counts"),
                "stage4_valid_rows": case.get("stage4_valid_rows"),
                "stage4_put_rows": case.get("stage4_put_rows"),
                "stage4_r1r2_rows": case.get("stage4_r1r2_rows"),
                "decision": case.get("decision"),
                "decision_reason": case.get("decision_reason"),
            }
            for case in doc.get("cases") or []
        ],
    }


def print_supervise_intervention(event: dict) -> None:
    resources = event.get("local_resources") or {}
    print(
        f"[监督介入 #{event.get('iteration')}] "
        f"内存可用GiB={resources.get('mem_available_gib')} "
        f"相关进程={resources.get('matching_process_count')} "
        f"硬早停={event.get('hard_stop_required')}",
        flush=True,
    )
    for case in event.get("cases") or []:
        print(
            f"- {case.get('subject')} "
            f"stage={case.get('stage')} "
            f"active={case.get('active_processes')} "
            f"valid/PUT/R1R2={case.get('valid')}/{case.get('put')}/{case.get('r1r2')} "
            f"stage4={case.get('stage4_valid_rows')}/"
            f"{case.get('stage4_put_rows')}/{case.get('stage4_r1r2_rows')} "
            f"decision={case.get('decision')} "
            f"reason={case.get('decision_reason')}",
            flush=True,
        )


def supervise(args: argparse.Namespace) -> dict:
    events = []
    intervention_path = run_dir(args) / "supervise_interventions.jsonl"
    deadline = time.time() + max(1, args.supervise_timeout_s)
    iteration = 0
    while time.time() < deadline:
        iteration += 1
        doc = monitor(args)
        event = supervise_intervention_event(iteration, doc)
        append_jsonl(intervention_path, event)
        print_supervise_intervention(event)
        events.append(event)
        if doc.get("hard_stop_required") and args.stop_on_hard_decision:
            stop_result = stop(args)
            settlement = settle(args) if args.settle_after_supervise_stop else {}
            result = {
                "schema": "veriput-rq1-case-batch-supervise/v1",
                "batch_id": args.batch_id,
                "status": "stopped-hard-decision",
                "events": events,
                "stop_result": stop_result,
                "settlement": settlement,
            }
            write_json(run_dir(args) / "supervise.json", result)
            return result
        all_terminal = all(
            case.get("active_processes", 0) <= 0
            and case.get("decision") in {"已完成", "转代码修复", "观察"}
            for case in doc.get("cases") or []
        )
        if all_terminal and doc.get("cases"):
            settlement = settle(args) if args.settle_after_supervise_stop else {}
            result = {
                "schema": "veriput-rq1-case-batch-supervise/v1",
                "batch_id": args.batch_id,
                "status": "all-terminal",
                "events": events,
                "settlement": settlement,
            }
            write_json(run_dir(args) / "supervise.json", result)
            return result
        time.sleep(max(1, args.supervise_interval_s))
    result = {
        "schema": "veriput-rq1-case-batch-supervise/v1",
        "batch_id": args.batch_id,
        "status": "timeout",
        "events": events,
    }
    write_json(run_dir(args) / "supervise.json", result)
    return result


def append_manual_settlement(manual_md: Path, doc: dict) -> None:
    manual_md.parent.mkdir(parents=True, exist_ok=True)
    begin = f"<!-- RQ1_BATCH_SETTLEMENT_BEGIN {doc['batch_id']} -->"
    end = f"<!-- RQ1_BATCH_SETTLEMENT_END {doc['batch_id']} -->"
    lines = [
        begin,
        "",
        f"## Batch settlement: {doc['batch_id']}",
        "",
        f"- case_count: {doc['case_count']}",
        f"- new valid: {doc['new_valid']}",
        f"- new PUT: {doc['new_put']}",
        f"- new R1/R2: {doc['new_r1r2']}",
        f"- bucket_counts: {json.dumps(doc['bucket_counts'], sort_keys=True)}",
        "",
    ]
    for case in doc["cases"]:
        lines.append(
            f"- `{case['bench']}/{case['subject']}`: "
            f"{case['bucket']} valid={case['valid']} "
            f"put={case['put_valid']} r1r2={case['r1r2']} "
            f"result={case['result_json']}"
        )
        details = case.get("oracle_details") or []
        for detail in details:
            if detail.get("oracle_tag_counts"):
                lines.append(
                    f"  - oracle_tags: {json.dumps(detail['oracle_tag_counts'], sort_keys=True)}"
                )
            if detail.get("coordinate_counts"):
                lines.append(
                    f"  - coordinates: {json.dumps(detail['coordinate_counts'], sort_keys=True)}"
                )
    lines.extend(["", end, ""])
    block = "\n".join(lines)
    try:
        text = manual_md.read_text(errors="replace")
    except OSError:
        text = ""
    pattern = re.compile(
        rf"{re.escape(begin)}.*?{re.escape(end)}\n?",
        re.DOTALL,
    )
    if pattern.search(text):
        text = pattern.sub(block, text)
    else:
        text = text.rstrip() + "\n\n" + block
    manual_md.write_text(text)


def print_chinese(doc: dict) -> None:
    if doc.get("schema") == "veriput-rq1-batch-automation-audit/v1":
        print(f"自动化闭环审计：{'PASS' if doc.get('ok') else 'FAIL'}")
        for row in doc.get("checks") or []:
            print(f"{row.get('item')}. {'PASS' if row.get('ok') else 'FAIL'} {row.get('name')} -- {row.get('evidence')}")
        return
    if doc.get("schema") == "veriput-rq1-case-state-init/v1":
        print(f"状态机初始化：{doc.get('case_state')}")
        print(f"inventory：{doc.get('inventory')}")
        print(f"case_count：{doc.get('case_count')}")
        return
    if doc.get("schema") == "veriput-rq1-ground-truth-seed/v1":
        print(f"结构化调查占位已生成：{doc.get('case_state')}")
        print(f"case 数：{len(doc.get('seeded') or [])}")
        for item in doc.get("seeded") or []:
            print(f"- {item}")
        return
    if doc.get("schema") == "veriput-rq1-case-state-summary/v1":
        print(f"状态账本：{doc.get('case_state')}")
        print(f"case_count：{doc.get('case_count')}")
        print(f"state_counts：{doc.get('state_counts')}")
        print(f"ground_truth_missing_count：{doc.get('ground_truth_missing_count')}")
        for item in doc.get("ground_truth_missing") or []:
            print(f"- {item.get('bench')}/{item.get('subject')} missing={item.get('missing')}")
        return
    if doc.get("schema") == "veriput-rq1-case-state-results-sync/v1":
        print(f"Results 同步到账本：{doc.get('case_state')}")
        print(f"case_count：{doc.get('case_count')}")
        print(f"state_counts：{doc.get('state_counts')}")
        print(f"changed_count：{doc.get('changed_count')}")
        for item in doc.get("changed") or []:
            print(f"- {item.get('bench')}/{item.get('subject')} {item.get('from')} -> {item.get('to')} valid/PUT/R1R2={item.get('valid')}/{item.get('put_valid')}/{item.get('r1r2')}")
        return
    if doc.get("schema") == "veriput-rq1-ground-truth-gate/v1":
        print(f"调查门禁：{'通过' if doc.get('ok') else '失败'}")
        print(f"manual md：{doc.get('manual_md')}")
        for case in doc.get("cases") or []:
            print(f"- {case.get('bench')}/{case.get('subject')} ok={case.get('ok')} missing={case.get('missing')}")
        return
    if doc.get("schema") == "veriput-rq1-case-batch/v1":
        print(f"批次准备：{doc.get('batch_id')}")
        print(f"selected_case_count：{doc.get('selected_case_count')}")
        print(f"run_case_count：{doc.get('case_count')}")
        print(f"run_state_filter：{doc.get('run_state_filter')}")
        print(f"manifest：{doc.get('manifest')}")
        return
    if doc.get("schema") == "veriput-rq1-pre-run-readiness/v1":
        print(f"跑前最大努力门禁：{'通过' if doc.get('ok') else '失败'}")
        print(f"批次：{doc.get('batch_id')} case_count={doc.get('case_count')}")
        for row in doc.get("checks") or []:
            print(f"- {'PASS' if row.get('ok') else 'FAIL'} {row.get('name')} evidence={row.get('evidence')}")
        return
    if doc.get("schema") == "veriput-rq1-batch-settlement/v1":
        print(f"批次结算：{doc.get('batch_id')}")
        print(f"case_count：{doc.get('case_count')}")
        print(f"新增 valid/PUT/R1R2：{doc.get('new_valid')}/{doc.get('new_put')}/{doc.get('new_r1r2')}")
        print(f"bucket_counts：{doc.get('bucket_counts')}")
        for case in doc.get("cases") or []:
            print(f"- {case.get('subject')} {case.get('bucket')} valid/PUT/R1R2={case.get('valid')}/{case.get('put_valid')}/{case.get('r1r2')}")
        return
    if doc.get("schema") == "veriput-rq1-case-batch-supervise/v1":
        print(f"批次监督：{doc.get('batch_id')}")
        print(f"状态：{doc.get('status')}")
        print(f"事件数：{len(doc.get('events') or [])}")
        if doc.get("stop_result"):
            print(f"停止结果：{doc.get('stop_result')}")
        if doc.get("settlement"):
            settlement = doc.get("settlement") or {}
            print(f"结算新增 valid/PUT/R1R2：{settlement.get('new_valid')}/{settlement.get('new_put')}/{settlement.get('new_r1r2')}")
        return
    if doc.get("schema") == "veriput-rq1-case-batch-status/v1":
        sup = doc.get("supervisor") or {}
        workers = ((doc.get("state") or {}).get("workers") or [])
        alive = sum(1 for row in workers if row.get("pid"))
        local = doc.get("local_resources") or {}
        print(f"批次：{doc.get('batch_id')}")
        print(f"运行目录：{doc.get('run_dir')}")
        print(f"supervisor running：{sup.get('running')}")
        print(f"worker 槽位：{alive}")
        print(f"本机可用内存 GiB：{local.get('mem_available_gib')}")
        print(f"本机相关进程数：{local.get('matching_process_count')}")
        for row in doc.get("local_progress") or []:
            print(f"进度文件：{row.get('path')} events={row.get('events')} last={row.get('last')}")
        return
    if doc.get("schema") == "veriput-rq1-case-batch-monitor/v1":
        local = doc.get("local_resources") or {}
        print(f"批次：{doc.get('batch_id')}")
        print(f"运行目录：{doc.get('run_dir')}")
        print(f"本机可用内存 GiB：{local.get('mem_available_gib')}")
        print(f"本机相关进程数：{local.get('matching_process_count')}")
        for case in doc.get("cases") or []:
            print(f"- {case.get('subject')}")
            print(f"  阶段：{case.get('stage')}")
            print(f"  valid/PUT/R1R2：{case.get('valid')}/{case.get('put')}/{case.get('r1r2')}")
            print(f"  quality：{case.get('quality_bucket')}")
            print(f"  Stage2：rows={case.get('cert_rows')} certified={case.get('certified_regions')} buckets={case.get('cert_bucket_counts')}")
            if case.get("timed_out_units"):
                print(f"  timeout units：{', '.join(case.get('timed_out_units'))}")
            for item in case.get("latest_cert") or []:
                print(f"  latest cert：unit={item.get('unit')} bucket={item.get('bucket')} exit={item.get('exit')} progress={item.get('progress')}")
            for item in case.get("put_summaries") or []:
                print(f"  Stage4：unit={item.get('unit')} b={item.get('b')} valid_rows={item.get('valid_reference_rows')} put_rows={item.get('put_rows')} r1r2={item.get('r1r2_rows')}")
            preflight = case.get("scheduler_preflight") or {}
            print(f"  跑前验收：ok={preflight.get('ok')} jobs={preflight.get('job_count')} target_hits={preflight.get('target_unit_hits')} wrong_contract={preflight.get('bad_contract_jobs')}")
            print(f"  活进程：{case.get('active_processes')}")
            print(f"  决策：{case.get('decision')}，原因：{case.get('decision_reason')}")
        if doc.get("hard_stop_required"):
            print(f"硬早停：需要，cases={doc.get('hard_stop_cases')}")
            if doc.get("stop_result"):
                print(f"硬早停执行结果：{doc.get('stop_result')}")
        return
    print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "gate", "audit", "init-state",
                                            "seed-ground-truth", "state",
                                            "sync-results", "readiness", "start",
                                            "status", "monitor", "supervise",
                                            "settle", "stop"))
    parser.add_argument("--batch-id", default="manual-005-012")
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--start-index", type=int, default=5)
    parser.add_argument("--end-index", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--require-batch-size",
                        action=argparse.BooleanOptionalAction,
                        default=True)
    parser.add_argument("--local-parallel", type=int, default=5)
    parser.add_argument("--remote-parallel", type=int, default=3)
    parser.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    parser.add_argument("--remote-esbmc", type=Path, default=DEFAULT_REMOTE_ESBMC)
    parser.add_argument("--remote-veriput", type=Path, default=DEFAULT_REMOTE_VERIPUT)
    parser.add_argument("--timeout-s", type=int, default=600)
    parser.add_argument("--local-memlimit-gib", type=int, default=8)
    parser.add_argument("--remote-memlimit-gib", type=float, default=6.0)
    parser.add_argument("--remote-reserve-mem-gib", type=float, default=2.0)
    parser.add_argument("--local-rss-limit-gib", type=int, default=18)
    parser.add_argument("--remote-rss-limit-gib", type=float, default=9.0)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--manual-md", type=Path, default=DEFAULT_MANUAL_MD)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--case-state", type=Path, default=DEFAULT_CASE_STATE)
    parser.add_argument("--repair-tickets", type=Path,
                        default=DEFAULT_REPAIR_TICKETS)
    parser.add_argument("--prevention-md-root",
                        dest="prevention_md_roots",
                        action="append",
                        type=Path,
                        default=[Path("notes")],
                        help="scan these md trees for prevent/prevention file references before rerun")
    parser.add_argument("--require-ground-truth",
                        action=argparse.BooleanOptionalAction,
                        default=True)
    parser.add_argument("--run-state",
                        action="append",
                        default=[],
                        choices=sorted(STATE_ORDER),
                        help="only put selected cases in these current states "
                             "into the worker manifest; gate still checks the "
                             "full fixed batch")
    parser.add_argument("--stop-on-hard-decision",
                        action=argparse.BooleanOptionalAction,
                        default=False)
    parser.add_argument("--update-manual-md",
                        action=argparse.BooleanOptionalAction,
                        default=True)
    parser.add_argument("--supervise-interval-s", type=int, default=30)
    parser.add_argument("--supervise-timeout-s", type=int, default=7200)
    parser.add_argument("--settle-after-supervise-stop",
                        action=argparse.BooleanOptionalAction,
                        default=True)
    parser.add_argument("--reset-leases",
                        action=argparse.BooleanOptionalAction,
                        default=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args)
    elif args.command == "gate":
        result = gate(args)
    elif args.command == "audit":
        result = audit(args)
    elif args.command == "init-state":
        result = init_state(args)
    elif args.command == "seed-ground-truth":
        result = seed_ground_truth(args)
    elif args.command == "state":
        result = state_summary(args)
    elif args.command == "sync-results":
        result = sync_state_from_results(args)
    elif args.command == "readiness":
        result = pre_run_readiness(args)
    elif args.command == "start":
        result = start(args)
    elif args.command == "stop":
        result = stop(args)
    elif args.command == "monitor":
        result = monitor(args)
    elif args.command == "supervise":
        result = supervise(args)
    elif args.command == "settle":
        result = settle(args)
    else:
        result = status(args)
    print_chinese(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
