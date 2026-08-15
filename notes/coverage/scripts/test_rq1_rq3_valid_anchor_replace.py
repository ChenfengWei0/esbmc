#!/usr/bin/env python3
"""Tests for strict valid-only RQ3-to-RQ1 anchor replacement."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import rq1_rq3_valid_anchor_replace as replace


class ValidAnchorReplacementTest(unittest.TestCase):

    def test_snapshot_seal_rejects_mutation(self) -> None:
        payload = {
            "schema": "veriput-rq3-valid-concrete-snapshot/v1",
            "published_shards": list(replace.PUBLISHED_SHARDS),
            "valid_count": 0,
            "raw_count": 0,
            "raw_equals_valid": True,
            "raw_keys_sha256": replace.key_set_digest(set()),
            "valid_keys_sha256": replace.key_set_digest(set()),
            "rows": [],
        }
        payload["seal_sha256"] = replace.digest_bytes(replace.canonical_json(payload))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(replace.load_snapshot(path)["valid_count"], 0)
            payload["valid_count"] = 1
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "seal mismatch"):
                replace.load_snapshot(path)

    def test_sealed_snapshot_still_rejects_nonvalid_rows(self) -> None:
        payload = {
            "schema": "veriput-rq3-valid-concrete-snapshot/v1",
            "published_shards": list(replace.PUBLISHED_SHARDS),
            "valid_count": 1,
            "raw_count": 1,
            "raw_equals_valid": True,
            "raw_keys_sha256": "same",
            "valid_keys_sha256": "same",
            "rows": [{
                "forge_status": "Failure"
            }],
        }
        payload["seal_sha256"] = replace.digest_bytes(replace.canonical_json(payload))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-valid candidate"):
                replace.load_snapshot(path)

    def test_equal_counts_do_not_prove_equal_raw_valid_sets(self) -> None:
        empty = replace.key_set_digest(set())
        payload = {
            "schema": "veriput-rq3-valid-concrete-snapshot/v1",
            "published_shards": list(replace.PUBLISHED_SHARDS),
            "valid_count": 0,
            "raw_count": 0,
            "raw_equals_valid": True,
            "raw_keys_sha256": "different",
            "valid_keys_sha256": empty,
            "rows": [],
        }
        payload["seal_sha256"] = replace.digest_bytes(replace.canonical_json(payload))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "equality claim"):
                replace.load_snapshot(path)

    def test_freeze_rejects_artifact_put_identity_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subject = root / "peer182" / "subjects" / "case"
            source = subject / "test" / "T.t.sol"
            put_path = subject / "put" / "job" / "put.json"
            source.parent.mkdir(parents=True)
            put_path.parent.mkdir(parents=True)
            source.write_text("contract T { function test_cov_1() public { assert(true); } }\n",
                              encoding="utf-8")
            put_path.write_text(json.dumps({
                "kind": "concrete",
                "path_function": "pf",
                "unit": "u",
                "enc": 1,
                "piece": None,
                "file": str(source),
                "test": "test_cov_1",
            }),
                                encoding="utf-8")
            artifact = {
                "kind": "concrete",
                "is_concrete": True,
                "is_put": False,
                "file": str(source),
                "test": "test_cov_1",
                "put_json": str(put_path),
                "unit": "u",
                "enc": 2,
                "forge_status": "Success",
                "valid_reference_test": True,
                "concrete_oracles": [{
                    "kind": "assertion"
                }],
            }
            (subject / "result.json").write_text(json.dumps(
                {"row": {
                    "raw_artifacts": [artifact],
                    "valid_artifacts": [artifact],
                }}),
                                                 encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "enc mismatch"):
                replace.freeze_snapshot(root, 1)

    def test_exact_match_replaces_only_generated_anchor_and_preserves_put(self) -> None:
        rq3_source = """contract RQ3Test {
  function test_cov_1() public { uint256 x = 7; assert(x == 7); }
}
"""
        rq1_source = """contract RQ1Test {
  function test_put_1(uint256 x) public { assert(x == x); }
  function test_ce_anchor_rq3_old() public { assert(false); }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rq3 = root / "rq3.t.sol"
            rq1 = root / "rq1.t.sol"
            rq3.write_text(rq3_source, encoding="utf-8")
            rq1.write_text(rq1_source, encoding="utf-8")
            identity = ["peer182/case", "pf", "unit", "1", ""]
            function = replace.zero_parameter_function(rq3_source, "test_cov_1")
            self.assertIsNotNone(function)
            snapshot = {
                "rows": [{
                    "identity": identity,
                    "file": str(rq3),
                    "test": "test_cov_1",
                    "source_sha256": replace.digest_file(rq3),
                    "function_sha256": replace.digest_bytes(function.encode()),
                }],
            }
            mapping = {
                "rows": [{
                    "status": "applied",
                    "identity": identity,
                    "case": "peer182/case",
                    "source": str(rq1),
                    "test": "test_put_1",
                    "anchor_test": "test_ce_anchor_rq3_old",
                    "applied_source_sha256": replace.digest_file(rq1),
                    "selected_rq3": {
                        "forge_status": None,
                        "valid_reference_test": False
                    },
                }]
            }
            rows = replace.stage_replacements(snapshot, mapping, root / "stage")
            self.assertEqual(rows[0]["status"], "staged")
            staged = Path(rows[0]["staged_source"]).read_text(encoding="utf-8")
            self.assertIn("uint256 x = 7", staged)
            self.assertNotIn("assert(false)", staged)
            before = rq1_source[replace.function_span(rq1_source, "test_put_1")[0]:replace.
                                function_span(rq1_source, "test_put_1")[1]]
            after_span = replace.function_span(staged, "test_put_1")
            self.assertEqual(before, staged[after_span[0]:after_span[1]])

    def test_cross_identity_and_non_generated_anchor_are_refused(self) -> None:
        source_text = """contract T {
  function test_put(uint256 x) public { assert(x == x); }
  function test_ce_anchor_manual() public { assert(true); }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "test.t.sol"
            source.write_text(source_text, encoding="utf-8")
            mapping = {
                "rows": [{
                    "status": "applied",
                    "identity": ["c", "p", "u", "1", ""],
                    "source": str(source),
                    "test": "test_put",
                    "applied_source_sha256": replace.digest_file(source),
                    "selected_rq3": {},
                }]
            }
            no_exact = replace.stage_replacements(
                {"rows": [{
                    "identity": ["c", "p", "u", "2", ""]
                }]}, mapping,
                Path(directory) / "stage1")
            self.assertEqual(no_exact[0]["reason"], "no exact valid candidate")
            exact = {"rows": [{"identity": ["c", "p", "u", "1", ""], "file": str(source)}]}
            refused = replace.stage_replacements(exact, mapping, Path(directory) / "stage2")
            self.assertIn("generated-only", refused[0]["reason"])

    def test_apply_is_blocked_until_raw_equals_valid(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "raw != valid"):
            replace.apply_replacements(
                [], {
                    "raw_count": 2,
                    "valid_count": 1,
                    "raw_equals_valid": False,
                    "raw_keys_sha256": "raw",
                    "valid_keys_sha256": "valid",
                }, 1)

    def test_failed_forge_gate_restores_original_source(self) -> None:
        original = """pragma solidity ^0.8.0;
contract T {
  function test_put(uint256 x) public pure { require(x == x); }
  function test_anchor() public pure { require(true); }
}
"""
        staged = original.replace("require(true)", "require(false)")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "test" / "T.t.sol"
            staged_path = root / "stage" / "T.t.sol"
            source.parent.mkdir()
            staged_path.parent.mkdir()
            source.write_text(original, encoding="utf-8")
            staged_path.write_text(staged, encoding="utf-8")
            (root / "foundry.toml").write_text('[profile.default]\nsrc = "src"\ntest = "test"\n',
                                               encoding="utf-8")
            (root / "src").mkdir()
            row = {
                "status": "staged",
                "source": str(source),
                "source_sha256": replace.digest_file(source),
                "staged_source": str(staged_path),
                "staged_source_sha256": replace.digest_file(staged_path),
                "test": "test_put",
                "anchor_test": "test_anchor",
            }
            seal = "equal"
            applied = replace.apply_replacements([row], {
                "raw_equals_valid": True,
                "raw_keys_sha256": seal,
                "valid_keys_sha256": seal,
            }, 30)
            self.assertEqual(applied, 0)
            self.assertEqual(row["status"], "rolled-back")
            self.assertEqual(source.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
