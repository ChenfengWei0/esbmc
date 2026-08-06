#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import certify_all  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def main():
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)
        journal = workdir / "cov-ce-journal.json"
        journal.write_text(json.dumps({
            "kind":
            "solidity-complete-path-ce-journal",
            "version":
            3,
            "partial":
            True,
            "complete":
            False,
            "claims_decided":
            6,
            "claims_total":
            277,
            "witnesses": {
                "sol:@C@C@F@f#1:path:31\t": {
                    "condition": "f:path:31",
                    "path_id": "31",
                    "path_depth": 4,
                    "path_function": "sol:@C@C@F@f#1",
                    "witnesses": [{}, {}, {}],
                },
                "sol:@C@C@F@f#1:path:32\t": {
                    "condition": "f:path:32",
                    "path_depth": 5,
                    "witness_count": "2",
                },
            },
        }))
        since = time.time() - 1
        got = certify_all.result_partial_witness_journal(str(workdir), since)
        bad += check(got is not None, "partial journal is read")
        bad += check(got["path_count"] == 2 and got["witness_count"] == 5,
                     f"path/witness counts are compacted: {got}")
        bad += check(got["claims_decided"] == 6 and got["claims_total"] == 277,
                     f"claim progress is preserved: {got}")
        bad += check([p["path_id"] for p in got["paths"]] == ["31", "32"],
                     f"path ids are preserved or derived: {got['paths']}")
        stale_since = time.time() + 60
        bad += check(
            certify_all.result_partial_witness_journal(str(workdir),
                                                       stale_since) is None,
            "stale journal is ignored")

        os.remove(journal)
        bad += check(certify_all.result_partial_witness_journal(str(workdir)) is None,
                     "missing journal is absent rather than empty data")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
