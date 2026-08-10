#!/usr/bin/env python3
"""Convert worker/canonical feedback into idempotent theory blocks and tickets."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path


DEFAULT_RESULTS = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_PROGRESS = (
    Path("/tmp/veriput_local_progress.jsonl"),
    Path("/tmp/veriput_local_extra_progress.jsonl"),
    Path("/tmp/veriput_local_extra2_progress.jsonl"),
    Path("/tmp/veriput_local_extra3_progress.jsonl"),
    Path("/tmp/veriput_remote_progress.jsonl"),
)
DEFAULT_CLAIMS = Path("/tmp/veriput_rq1_case_theory_claims.jsonl")
DEFAULT_BLOCKS = Path("/tmp/veriput_rq1_theory_blocks.jsonl")
DEFAULT_TICKETS = Path("/tmp/veriput_rq1_repair_tickets.jsonl")
DEFAULT_EVENTS = Path("/tmp/veriput_rq1_feedback_events.jsonl")
DEFAULT_STATE = Path("/tmp/veriput_rq1_feedback_state.json")
LOCK = Path("/tmp/veriput_rq1_feedback_controller.lock")


def _json(path: Path, default):
    try:
        return json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return default


def _rows(path: Path) -> list[dict]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    result = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            result.append(row)
    return result


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _subject_key(bench: str, subject: str) -> str:
    return f"{bench}/{subject}"


def _claims(path: Path) -> dict[str, dict]:
    claims = {}
    for row in _rows(path):
        if str(row.get("review_status") or "") != "accepted":
            continue
        if not str(row.get("commit_sha") or row.get("review_commit") or ""):
            continue
        key = _subject_key(str(row.get("bench") or ""),
                           str(row.get("subject") or ""))
        if key != "/":
            claims[key] = row
    return claims


def _result(subject_dir: Path) -> tuple[dict, dict]:
    result = _json(subject_dir / "result.json", {})
    put = _json(subject_dir / "put.json", {})
    return (result if isinstance(result, dict) else {},
            put if isinstance(put, dict) else {})


def _counts(result: dict, put: dict) -> tuple[int, int, int]:
    row = result.get("row") if isinstance(result.get("row"), dict) else result
    put_row = put.get("row") if isinstance(put.get("row"), dict) else put

    def value(key: str) -> int:
        try:
            return max(int(row.get(key) or 0), int(put_row.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    return value("valid"), value("put_valid"), value("valid_put_with_R1_or_R2")


def _subject_dir(results_root: Path, row: dict) -> Path:
    return results_root / str(row.get("bench") or "") / "subjects" / str(
        row.get("subject") or "")


def _fingerprint(row: dict, result: dict, put: dict) -> str:
    payload = {
        "bench": row.get("bench"),
        "subject": row.get("subject"),
        "status": row.get("status"),
        "valid": _counts(result, put)[0],
        "put_valid": _counts(result, put)[1],
        "r1r2": _counts(result, put)[2],
        "result_mtime_ns": row.get("result_mtime_ns"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _latest_progress(paths: tuple[Path, ...]) -> dict[str, dict]:
    latest = {}
    for path in paths:
        for row in _rows(path):
            bench = str(row.get("bench") or "")
            subject = str(row.get("subject") or "")
            if not bench or not subject:
                continue
            if str(row.get("status") or "") in {
                    "running", "skipped-lease-held", "skipped-low-mem"
            }:
                continue
            key = _subject_key(bench, subject)
            old = latest.get(key)
            if old is None or str(row.get("ts") or "") >= str(old.get("ts") or ""):
                latest[key] = dict(row)
    return latest


def _weak_reason(row: dict, result: dict, put: dict) -> tuple[str, str] | None:
    valid, put_valid, r1r2 = _counts(result, put)
    status = str(row.get("status") or "")
    if status not in {"", "done"}:
        return "NO_VALID_AFTER_FEEDBACK", f"worker status={status}"
    if valid <= 0:
        return "NO_VALID_AFTER_FEEDBACK", "canonical valid=0"
    if put_valid <= 0:
        return "NO_PUT_AFTER_FEEDBACK", "canonical put_valid=0"
    if r1r2 <= 0:
        return "NO_R1R2_AFTER_FEEDBACK", "canonical R1/R2=0"
    return None


def scan(args: argparse.Namespace) -> dict:
    claims = _claims(args.claims)
    previous = _json(args.state, {})
    seen = set(previous.get("processed_fingerprints") or [])
    blocks = {_subject_key(str(row.get("bench") or ""), str(row.get("subject") or ""))
              for row in _rows(args.blocks)}
    latest = _latest_progress(tuple(args.progress))
    events = []
    for key, row in latest.items():
        subject_dir = _subject_dir(args.results_root, row)
        result, put = _result(subject_dir)
        if not result and not put:
            # A terminal worker row without canonical output is itself a
            # failure; it must be visible and retried by the repair queue.
            result, put = {}, {}
        fingerprint = _fingerprint(row, result, put)
        if fingerprint in seen:
            continue
        weak = _weak_reason(row, result, put)
        if weak is None:
            seen.add(fingerprint)
            continue
        category, reason = weak
        claim = claims.get(key)
        block_added = False
        if claim and key not in blocks:
            block = {
                "schema": "veriput-rq1-theory-block/v1",
                "ts": time.time(),
                "bench": row.get("bench"),
                "subject": row.get("subject"),
                "patch_id": claim.get("patch_id"),
                "commit_sha": claim.get("commit_sha") or claim.get("review_commit"),
                "reason": reason,
                "category": category,
                "active": True,
            }
            _append(args.blocks, block)
            blocks.add(key)
            block_added = True
        ticket = {
            "schema": "veriput-rq1-repair-ticket/v2",
            "ts": time.time(),
            "bench": row.get("bench"),
            "subject": row.get("subject"),
            "subject_dir": str(subject_dir),
            "result_file": str(subject_dir / "result.json"),
            "result_bucket": category,
            "category": category,
            "valid": _counts(result, put)[0],
            "put_valid": _counts(result, put)[1],
            "r1r2": _counts(result, put)[2],
            "failure_reason": reason,
            "theory_decrement_applied": block_added,
            "source": "rq1_feedback_controller",
        }
        _append(args.tickets, ticket)
        event = {
            "schema": "veriput-rq1-feedback-event/v1",
            "ts": time.time(),
            "fingerprint": fingerprint,
            "bench": row.get("bench"),
            "subject": row.get("subject"),
            "category": category,
            "reason": reason,
            "theory_decrement_applied": block_added,
            "theory_claim_present": bool(claim),
            "valid": _counts(result, put)[0],
            "put_valid": _counts(result, put)[1],
            "r1r2": _counts(result, put)[2],
        }
        _append(args.events, event)
        events.append(event)
        seen.add(fingerprint)
    state = {
        "schema": "veriput-rq1-feedback-state/v1",
        "updated_ts": time.time(),
        "processed_fingerprints": sorted(seen),
        "last_event_count": len(events),
        "active_theory_blocks": len(blocks),
        "events": events,
    }
    _write_state(args.state, state)
    return state


def _write_state(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--progress", type=Path, action="append",
                        default=list(DEFAULT_PROGRESS))
    parser.add_argument("--claims", type=Path, default=DEFAULT_CLAIMS)
    parser.add_argument("--blocks", type=Path, default=DEFAULT_BLOCKS)
    parser.add_argument("--tickets", type=Path, default=DEFAULT_TICKETS)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args()
    args.blocks.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = scan(args)
    print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
