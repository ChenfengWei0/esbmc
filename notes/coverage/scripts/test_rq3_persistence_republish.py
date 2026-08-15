#!/usr/bin/env python3
"""Unit tests for the RQ3 persistence metadata republisher."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import rq3_persistence_republish as migrate


class RQ3PersistenceRepublishTest(unittest.TestCase):

    def test_latest_rows_prefers_appended_republication(self) -> None:
        old = {"subject_id": "case-a", "valid": 0}
        new = {"subject_id": "case-a", "valid": 3}
        rows = migrate._latest_rows(((json.dumps(old) + "\n" + json.dumps(new) + "\n").encode()))
        self.assertEqual(rows["gen:veriput:case-a"]["valid"], 3)

    def test_republish_summary_partitions_by_hash_bound_keys(self) -> None:
        rows = [{
            "kind": "concrete",
            "is_concrete": True,
            "valid_reference_test": True,
            "forge_status": "Success",
            "file": f"/tmp/test-{index}.t.sol",
            "test": f"test_{index}",
        } for index in range(3)]
        publishable = migrate.persistence_publication_key(rows[1])
        coverage = {
            "publishable_validity_keys": [publishable],
        }
        result = migrate._republish_summary({"raw": 3}, rows, coverage,
                                            "2 concrete replays could not be persisted")
        self.assertEqual([row["test"] for row in result["valid_tests"]], ["test_1"])
        self.assertEqual({row["test"]
                          for row in result["unpublished_valid_tests"]}, {"test_0", "test_2"})
        self.assertEqual(result["valid"], 1)
        self.assertEqual(result["status"], "ok")

    def test_compare_before_write_detects_changed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text("old\n")
            plan = {"preimages": {str(path): migrate._sha256(path)}}
            path.write_text("changed\n")
            with self.assertRaisesRegex(migrate.MigrationError, "compare-before-write mismatch"):
                migrate._verify_preimages(plan)

    def test_restore_uses_transaction_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "result.json"
            backup = root / "backup.json"
            target.write_text("new\n")
            backup.write_text("old\n")
            migrate._restore(
                {str(target): {
                     "path": str(backup),
                     "sha256": migrate._sha256(backup),
                 }})
            self.assertEqual(target.read_text(), "old\n")

    def test_manual_rollback_refuses_changed_postimage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            target = bundle / "target.json"
            backup = bundle / "backup.json"
            target.write_text("committed\n")
            backup.write_text("old\n")
            transaction = {
                "root": str(bundle),
                "backups": {
                    str(target): {
                        "path": str(backup),
                        "sha256": migrate._sha256(backup),
                    }
                },
                "postimages": {
                    str(target): migrate._sha256(target)
                },
            }
            (bundle / "transaction.json").write_text(json.dumps(transaction))
            target.write_text("later change\n")
            with self.assertRaisesRegex(migrate.MigrationError, "changed postimage"):
                migrate.rollback(bundle)

    def test_restore_refuses_modified_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "result.json"
            backup = root / "backup.json"
            target.write_text("new\n")
            backup.write_text("old\n")
            record = {
                str(target): {
                    "path": str(backup),
                    "sha256": migrate._sha256(backup),
                }
            }
            backup.write_text("tampered\n")
            with self.assertRaisesRegex(migrate.MigrationError, "backup hash mismatch"):
                migrate._restore(record)


if __name__ == "__main__":
    unittest.main()
