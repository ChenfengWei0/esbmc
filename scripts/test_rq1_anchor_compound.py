#!/usr/bin/env python3
"""Focused regression tests for compound exact CE anchor helpers."""
# pylint: disable=wrong-import-position,cyclic-import

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "notes" / "coverage" / "scripts"))

import rq1_anchor_compound as compound  # noqa: E402
from rq1_concrete_replay_store import _oracle_binding_errors  # noqa: E402
from solidity_path_put import _oracle_claim_coverage_error  # noqa: E402


def check(value, message):
    """Emit one TAP-like assertion result."""
    print(("ok - " if value else "not ok - ") + message)
    return 0 if value else 1


def main():
    """Run the focused regression checks."""
    # pylint: disable=too-many-locals
    bad = 0
    source = """contract ProbeTest {
  Probe c0;
  function test_cov_0() public {
    c0.pair(7);
  }
}
"""
    rendered, oracles, error = compound.add_indexed_return_oracles(source, "test_cov_0", "pair",
                                                                   [("", "uint8"),
                                                                    ("", "int16")], "(1, -2)")
    bad += check(error is None and len(oracles) == 2,
                 "tuple return produces one oracle per component")
    bad += check([item["return_index"] for item in oracles] == [0, 1]
                 and all(item["return_arity"] == 2 for item in oracles),
                 "tuple return metadata is completely indexed")
    bad += check(not _oracle_binding_errors(rendered, "test_cov_0", "pair", oracles),
                 "tuple assertions bind to exactly one typed target result")
    claim = {"return_value": "(1, -2)", "exit_kind": "normal"}
    bad += check(
        _oracle_claim_coverage_error(claim, oracles) is None,
        "tuple oracles cover the retained report value")
    bad += check(oracles[1]["expected"] == "int16(-2)",
                 "signed two's-complement witness is rendered in range")

    _rendered, _oracles, error = compound.add_indexed_return_oracles(source, "test_cov_0", "pair",
                                                                     [("", "uint8")], "(1, -2)")
    bad += check("arity" in str(error), "tuple arity mismatch fails closed")

    record = {
        "recovery_category": "directly-generatable",
        "identity": {
            "case": "c",
            "path_function": "pf",
            "unit": "f",
            "enc": 1
        },
        "observable_evidence": {
            "anchor_required_kinds": ["return", "revert"]
        },
        "ce": {
            "return_value": "0",
            "return_value_known": True,
            "exit_kind": "revert",
            "revert": {
                "kind": "rollback"
            }
        },
    }
    projected, error = compound.executable_claim(record)
    bad += check(
        compound.owns_record(record) and compound.obligation_key(record)[-1] == "",
        "compound partition and obligation key are deterministic")
    bad += check(
        error is None and "return_value" not in projected and projected["exit_kind"] == "revert",
        "revert projection excludes the non-executable phantom return")
    bad += check(record["ce"]["return_value"] == "0",
                 "claim projection never mutates historical evidence")

    tuple_record = {
        "recovery_category": "directly-generatable",
        "observable_evidence": {
            "anchor_required_kinds": ["return"]
        },
        "ce": {
            "return_value": "(1, 2)"
        },
    }
    bad += check(compound.owns_record(tuple_record),
                 "genuine tuple-only return belongs to this partition")
    tuple_record["ce"]["return_value"] = "(1)"
    bad += check(not compound.owns_record(tuple_record),
                 "parenthesized scalar stays in the scalar-return partition")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        inventory = root / "inventory.json"
        inventory.write_text("{}\n", encoding="utf-8")
        status_path = root / "status.json"
        status_path.write_text(json.dumps({
            "schema": "veriput-rq1-anchor-parallel-status/v1",
            "exclusive_identity_assignments": {
                "anchor_compound": [["c", "pf", "f", "1", ""]]
            },
        }),
                               encoding="utf-8")
        raw_report = {
            "inventory":
            str(inventory),
            "counts": {
                "ready": 2
            },
            "rows": [{
                "identity": ["c", "pf", "f", "1", ""],
                "record_identity_sha256": "a" * 64,
                "status": "ready",
            }, {
                "identity": ["setup", "pf", "f", "2", ""],
                "record_identity_sha256": "b" * 64,
                "status": "ready",
            }],
        }
        strict = compound.strict_partition_artifact(raw_report, status_path)
        bad += check(strict["exclusive_owned"] == 1 and len(strict["ready"]) == 1,
                     "strict artifact consumes only exclusive compound ownership")
        bad += check(strict["excluded_by_precedence"] == 1 and strict["ownership_status_sha256"],
                     "strict artifact seals ownership and records exclusions")
        strict["_artifact_path"] = str(root / "strict.json")
        progress_path = root / "progress.json"
        progress_path.write_text(json.dumps({
            "recovery_partition":
            "compound",
            "partition_artifact":
            str(root / "strict.json"),
            "counts": {
                "ready": 1
            },
            "rows": [{
                "identity": ["c", "pf", "f", "1", ""],
                "status": "ready",
            }],
        }),
                                 encoding="utf-8")
        prepared = compound.bind_prepared_rows(strict, progress_path)
        bad += check(
            prepared["observable_ready"] == 1 and len(prepared["ready"]) == 1
            and prepared["preparation_progress_sha256"],
            "prepared selector binds the main backfill readiness result")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
