#!/usr/bin/env python3
"""Integration tests for the RQ3 raw-equals-valid audit gate."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("run_rq3_no_cer_reg.py")


class RQ3AuditIntegrationTest(unittest.TestCase):
    """Exercise the public audit-only CLI against real temporary ledgers."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.test_file = self.root / "Replay.t.sol"
        self.test_file.write_text(
            "contract ReplayTest {\n"
            "  function test_cov_0() public { assertTrue(true); }\n"
            "}\n",
            encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def artifact(self, test_name="test_cov_0"):
        """Return one structurally valid concrete replay artifact."""
        return {
            "file": str(self.test_file),
            "test": test_name,
            "kind": "concrete",
            "is_concrete": True,
            "is_put": False,
            "concrete_oracles": [{
                "kind": "normal-exit"
            }],
        }

    def write_result(self, relative, raw, valid):
        """Publish a result ledger at the requested relative location."""
        result_json = self.root / relative / "result.json"
        result_json.parent.mkdir(parents=True, exist_ok=True)
        result_json.write_text(json.dumps(
            {"put": {
                "raw_artifacts": raw,
                "valid_artifacts": valid,
            }}),
                               encoding="utf-8")

    def audit(self):
        """Run the real command-line entry point and return its JSON output."""
        completed = subprocess.run(
            [sys.executable,
             str(SCRIPT), "--result-root",
             str(self.root), "--audit-only"],
            check=False,
            capture_output=True,
            text=True)
        return completed, json.loads(completed.stdout)

    def test_equal_logical_sets_pass(self):
        artifact = self.artifact()
        self.write_result("peer182/subjects/canonical", [artifact, artifact], [artifact])

        completed, audit = self.audit()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(audit["ok"])
        self.assertEqual(audit["raw_tests"], 1)
        self.assertEqual(audit["valid_tests"], 1)
        self.assertEqual(audit["raw_only_count"], 0)
        self.assertEqual(audit["valid_only_count"], 0)
        self.assertEqual(audit["raw_only"], [])
        self.assertEqual(audit["valid_only"], [])

    def test_raw_only_logical_test_fails_with_exact_row(self):
        artifact = self.artifact()
        self.write_result("bugfix124/subjects/canonical", [artifact], [])

        completed, audit = self.audit()

        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertFalse(audit["ok"])
        self.assertEqual(audit["raw_tests"], 1)
        self.assertEqual(audit["valid_tests"], 0)
        self.assertEqual(audit["raw_only_count"], 1)
        self.assertEqual(audit["raw_only"], [{
            "file": str(self.test_file),
            "test": "test_cov_0",
        }])

    def test_valid_only_logical_test_fails_with_exact_row(self):
        artifact = self.artifact()
        self.write_result("real203/subjects/canonical", [], [artifact])

        completed, audit = self.audit()

        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertFalse(audit["ok"])
        self.assertEqual(audit["valid_only_count"], 1)
        self.assertEqual(audit["valid_only"], [{
            "file": str(self.test_file),
            "test": "test_cov_0",
        }])

    def test_non_published_shards_and_redo_results_are_ignored(self):
        artifact = self.artifact()
        self.write_result("peer182/subjects/canonical", [artifact], [artifact])
        self.write_result("peer182/subjects/canonical.redo.1", [artifact], [])
        self.write_result("missing-pilot/shard-00/peer182/subjects/case", [artifact], [])
        self.write_result("peer182/shard-00/subjects/case", [artifact], [])

        completed, audit = self.audit()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(audit["ok"])
        self.assertEqual(audit["result_files"], 1)
        self.assertEqual(audit["raw_tests"], 1)
        self.assertEqual(audit["valid_tests"], 1)

    def test_empty_published_inventory_fails(self):
        completed, audit = self.audit()

        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertFalse(audit["ok"])
        self.assertEqual(audit["published_result_files"], 0)
        self.assertEqual(audit["result_files"], 0)

    def test_corrupt_published_ledger_fails(self):
        result_json = self.root / "peer182/subjects/corrupt/result.json"
        result_json.parent.mkdir(parents=True)
        result_json.write_text("{broken", encoding="utf-8")

        completed, audit = self.audit()

        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertFalse(audit["ok"])
        self.assertEqual(audit["published_result_files"], 1)
        self.assertEqual(audit["result_files"], 0)
        self.assertEqual(audit["ledger_errors"], 1)
        self.assertEqual(audit["ledger_error_details"][0]["file"], str(result_json))

    def test_equal_malformed_artifacts_fail(self):
        self.write_result("real203/subjects/malformed", [{}], [{}])

        completed, audit = self.audit()

        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertFalse(audit["ok"])
        self.assertEqual(audit["raw_tests"], 0)
        self.assertEqual(audit["valid_tests"], 0)
        self.assertEqual(audit["invalid_artifacts"], 2)
        self.assertEqual({row["side"]
                          for row in audit["invalid_artifact_details"]}, {"raw", "valid"})

    def test_non_list_artifact_ledger_fails_without_crashing(self):
        self.write_result("bugfix124/subjects/malformed-list", {"bad": True}, [])

        completed, audit = self.audit()

        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertFalse(audit["ok"])
        self.assertEqual(audit["invalid_artifacts"], 1)
        self.assertEqual(audit["invalid_artifact_details"][0]["errors"], ["artifacts-not-list"])


if __name__ == "__main__":
    unittest.main()
