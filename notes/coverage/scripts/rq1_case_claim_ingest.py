#!/usr/bin/env python3
"""Atomically record an accepted, case-level RQ1 validation claim."""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import time
from pathlib import Path


DEFAULT_OUT = Path("/tmp/veriput_rq1_case_theory_claims.jsonl")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--patch-id", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--bench", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--category", required=True)
    parser.add_argument("--fix-target", required=True)
    parser.add_argument("--expected-valid-delta", type=int, required=True)
    parser.add_argument("--expected-put-delta", type=int, default=0)
    parser.add_argument("--expected-r1r2-delta", type=int, default=0)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if not COMMIT_RE.fullmatch(args.commit_sha):
        raise SystemExit("commit sha is required")
    if args.expected_valid_delta <= 0 and args.expected_put_delta <= 0 and \
            args.expected_r1r2_delta <= 0:
        raise SystemExit("a validation claim needs a positive expected delta")
    row = {
        "schema": "veriput-rq1-case-theory-claim/v1",
        "ts": time.time(),
        "patch_id": args.patch_id,
        "commit_sha": args.commit_sha,
        "review_status": "accepted",
        "bench": args.bench,
        "subject": args.subject,
        "category": args.category,
        "fix_target": args.fix_target,
        "expected_valid_delta": args.expected_valid_delta,
        "expected_put_delta": args.expected_put_delta,
        "expected_r1r2_delta": args.expected_r1r2_delta,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(json.dumps(row, sort_keys=True) + "\n")
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    print(json.dumps(row, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
