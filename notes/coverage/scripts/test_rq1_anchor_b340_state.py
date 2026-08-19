#!/usr/bin/env python3
"""Fail-closed checks for the sealed B340 state partition."""
# pylint: disable=protected-access

import hashlib
import json
import tempfile
from pathlib import Path

import rq1_put_ce_anchor_backfill as backfill


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def check(condition, message):
    """Print one TAP-like assertion and return its failure count."""
    print(("ok - " if condition else "not ok - ") + message)
    return 0 if condition else 1


def main():
    """Exercise positive loading and two independent seal failures."""
    bad = 0
    bad += check(backfill._has_executable_target_call("c0.f{value: 1}(7);", "f"),
                 "direct call options are executable target calls")
    bad += check(
        backfill._has_executable_target_call(
            '(bool ok,) = address(c0).call(abi.encodeWithSignature("f(uint256)", 7));', "f"),
        "exact low-level ABI signatures are executable target calls")
    bad += check(
        backfill._has_executable_target_call(
            '(bool ok,) = address(c0).call{value: 1}(abi.encodeWithSignature("f()"));', "f"),
        "low-level call options preserve the exact ABI target")
    bad += check(
        not backfill._has_executable_target_call(
            '// address(c0).call(abi.encodeWithSignature("f(uint256)", 7));\nassertTrue(true);',
            "f"), "commented low-level ABI signatures are not executable target calls")
    bad += check(
        not backfill._has_executable_target_call(
            '(bool ok,) = address(c0).call(abi.encodeWithSignature("other()"));', "f"),
        "a different ABI signature is not the target call")
    bad += check(
        not backfill._has_executable_target_call(
            '(bool ok,) = address(c0).call(abi.encodeWithSignature("f()garbage"));', "f"),
        "an ABI signature with trailing garbage is not the target call")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        evidence = root / "inventory.json"
        _write(evidence, {"records": []})
        identity = ["suite/subject", "pf", "f", "1", ""]
        row = {
            "identity": identity,
            "record_identity_sha256": "a" * 64,
            "required_kinds": ["state-delta"],
            "status": "selected",
        }
        artifact = root / "partition.json"
        _write(
            artifact, {
                "schema":
                "veriput-rq1-anchor-b340-state-partition/v1",
                "inputs": [{
                    "role": "recovery-inventory",
                    "path": str(evidence),
                    "sha256": _sha256(evidence),
                    "bytes": evidence.stat().st_size,
                }],
                "exclusive_owned":
                1,
                "rows": [row],
            })
        loaded = backfill._load_partition_rows(artifact, "b340-state")
        bad += check(loaded == [row], "sealed state-only selector is accepted")

        swapped = root / "swapped-inventory.json"
        _write(swapped, {"records": []})
        error = backfill._sealed_partition_inventory_error("b340-state", artifact, swapped)
        bad += check("differs" in str(error), "a swapped recovery inventory is rejected")

        _write(evidence, {"records": ["changed"]})
        try:
            backfill._load_partition_rows(artifact, "b340-state")
            refused = False
        except RuntimeError as error:
            refused = "input seal" in str(error)
        bad += check(refused, "changed selector input is rejected")

        _write(evidence, {"records": []})
        row["required_kinds"] = ["events", "state-delta"]
        _write(
            artifact, {
                "schema":
                "veriput-rq1-anchor-b340-state-partition/v1",
                "inputs": [{
                    "role": "recovery-inventory",
                    "path": str(evidence),
                    "sha256": _sha256(evidence),
                    "bytes": evidence.stat().st_size,
                }],
                "exclusive_owned":
                1,
                "rows": [row],
            })
        try:
            backfill._load_partition_rows(artifact, "b340-state")
            refused = False
        except RuntimeError as error:
            refused = "ownership" in str(error)
        bad += check(refused, "mixed observable selector is rejected")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
