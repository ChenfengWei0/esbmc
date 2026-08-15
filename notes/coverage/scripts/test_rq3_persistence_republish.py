#!/usr/bin/env python3
"""Unit tests for the RQ3 persistence metadata republisher."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import rq3_persistence_republish as migrate


class RQ3PersistenceRepublishTest(unittest.TestCase):

    @staticmethod
    def _canonical_root(directory: str) -> Path:
        root = Path(directory) / migrate.EXPECTED_ROOT_SUFFIX
        root.mkdir(parents=True)
        return root

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

    def test_candidate_source_seals_detect_test_and_flat_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            test_file = root / "test" / "Case.t.sol"
            flat_file = root / "src" / "flat.sol"
            test_file.parent.mkdir()
            flat_file.parent.mkdir()
            test_file.write_text("test\n")
            flat_file.write_text("flat\n")
            seals = migrate._candidate_source_seals([{
                "file": str(test_file),
                "test": "test_case",
            }])
            plan = {
                "cases": [{
                    "result": str(root / "result.json"),
                    "candidates": 1,
                    "candidate_keys": [[str(test_file), "test_case"]],
                    "candidate_source_seals": seals,
                }]
            }
            migrate._verify_candidate_source_seals(plan)
            test_file.write_text("changed test\n")
            with self.assertRaisesRegex(migrate.MigrationError, "test source seal mismatch"):
                migrate._verify_candidate_source_seals(plan)
            test_file.write_text("test\n")
            flat_file.write_text("changed flat\n")
            with self.assertRaisesRegex(migrate.MigrationError, "flat source seal mismatch"):
                migrate._verify_candidate_source_seals(plan)

    def test_candidate_source_seals_reject_partial_population(self) -> None:
        plan = {
            "cases": [{
                "result": "/tmp/result.json",
                "candidates": 1,
                "candidate_keys": [["/tmp/test.t.sol", "test_case"]],
                "candidate_source_seals": [],
            }]
        }
        with self.assertRaisesRegex(migrate.MigrationError, "seal population mismatch"):
            migrate._verify_candidate_source_seals(plan)

    def test_result_snapshot_detects_added_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for dataset in migrate.DATASETS:
                (root / dataset / "subjects").mkdir(parents=True)
            first = root / migrate.DATASETS[0] / "subjects" / "one" / "result.json"
            first.parent.mkdir()
            first.write_text("{}\n")
            snapshot = migrate._result_snapshot(root)
            plan = {
                "root": str(root),
                "result_snapshot": snapshot,
                "result_snapshot_sha256": migrate._mapping_sha256(snapshot),
            }
            migrate._verify_result_snapshot(plan)
            added = root / migrate.DATASETS[1] / "subjects" / "two" / "result.json"
            added.parent.mkdir()
            added.write_text("{}\n")
            with self.assertRaisesRegex(migrate.MigrationError, "full result snapshot changed"):
                migrate._verify_result_snapshot(plan)

    def test_plan_seal_covers_candidate_seals_and_write_bytes(self) -> None:
        plan = {
            "cases": [{
                "candidate_source_seals": [{
                    "file": "/tmp/test.t.sol",
                    "test": "test_case",
                    "test_file_sha256": "test-sha",
                    "flat_file": "/tmp/flat.sol",
                    "flat_file_sha256": "flat-sha",
                }]
            }],
            "writes": {
                "/tmp/result.json": {
                    "sha256": migrate._sha256_bytes(b"new\n"),
                    "bytes": 4,
                }
            },
        }
        plan["plan_sha256"] = migrate._sha256_bytes(migrate._json_bytes(plan))
        plan["_write_bytes"] = {Path("/tmp/result.json"): b"new\n"}
        migrate._verify_plan_seal(plan)
        plan["cases"][0]["candidate_source_seals"][0]["test_file_sha256"] = "changed"
        with self.assertRaisesRegex(migrate.MigrationError, "plan seal mismatch"):
            migrate._verify_plan_seal(plan)

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

    def test_backup_refuses_source_drift_from_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            target = root / "audit.json"
            target.write_text("old\n")
            plan = {
                "root": str(root),
                "_write_bytes": {},
                "preimages": {
                    str(target): migrate._sha256(target),
                },
            }
            target.write_text("changed\n")
            with self.assertRaisesRegex(migrate.MigrationError, "differs from sealed preimage"):
                migrate._backup(plan, bundle)

    def test_manual_rollback_rejects_changed_backup_map(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            root = self._canonical_root(directory)
            target = root / "target.json"
            backup = bundle / "rollback" / "backup.json"
            backup.parent.mkdir()
            target.write_text("committed\n")
            backup.write_text("old\n")
            backups = {
                str(target): {
                    "path": str(backup),
                    "sha256": migrate._sha256(backup),
                }
            }
            transaction = {
                "root": str(root),
                "state": "committed",
                "backups": backups,
                "backups_sha256": migrate._mapping_sha256(backups),
                "postimages": {
                    str(target): migrate._sha256(target),
                },
            }
            transaction["backups"][str(target)]["sha256"] = "tampered"
            (bundle / "transaction.json").write_text(json.dumps(transaction))
            with self.assertRaisesRegex(migrate.MigrationError, "backup map seal mismatch"):
                migrate.rollback(bundle)

    def test_manual_rollback_refuses_changed_postimage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            root = self._canonical_root(directory)
            target = root / "target.json"
            backup = bundle / "rollback" / "backup.json"
            backup.parent.mkdir()
            target.write_text("committed\n")
            backup.write_text("old\n")
            transaction = {
                "root": str(root),
                "state": "committed",
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
            transaction["backups_sha256"] = migrate._mapping_sha256(transaction["backups"])
            (bundle / "transaction.json").write_text(json.dumps(transaction))
            target.write_text("later change\n")
            with self.assertRaisesRegex(migrate.MigrationError, "changed postimage"):
                migrate.rollback(bundle)

    def test_manual_rollback_rejects_target_outside_locked_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            root = self._canonical_root(directory)
            target = bundle / "outside.json"
            backup = bundle / "rollback" / "backup.json"
            backup.parent.mkdir()
            target.write_text("committed\n")
            backup.write_text("old\n")
            backups = {
                str(target): {
                    "path": str(backup),
                    "sha256": migrate._sha256(backup),
                }
            }
            transaction = {
                "root": str(root),
                "state": "committed",
                "backups": backups,
                "backups_sha256": migrate._mapping_sha256(backups),
                "postimages": {
                    str(target): migrate._sha256(target),
                },
            }
            (bundle / "transaction.json").write_text(json.dumps(transaction))
            with self.assertRaisesRegex(migrate.MigrationError, "escapes its transaction root"):
                migrate.rollback(bundle)

    def test_manual_rollback_resumes_partially_restored_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            root = self._canonical_root(directory)
            rollback_root = bundle / "rollback"
            rollback_root.mkdir()
            first = root / "first.json"
            second = root / "second.json"
            first_backup = rollback_root / "first.json"
            second_backup = rollback_root / "second.json"
            first.write_text("old-first\n")
            second.write_text("new-second\n")
            first_backup.write_text("old-first\n")
            second_backup.write_text("old-second\n")
            backups = {
                str(first): {
                    "path": str(first_backup),
                    "sha256": migrate._sha256(first_backup),
                },
                str(second): {
                    "path": str(second_backup),
                    "sha256": migrate._sha256(second_backup),
                },
            }
            transaction = {
                "root": str(root),
                "state": "rolling-back",
                "backups": backups,
                "backups_sha256": migrate._mapping_sha256(backups),
                "allowed_recovery_hashes": {
                    str(first): [migrate._sha256(first_backup), "new-first-sha"],
                    str(second): [migrate._sha256(second_backup),
                                  migrate._sha256(second)],
                },
            }
            (bundle / "transaction.json").write_text(json.dumps(transaction))
            migrate.rollback(bundle)
            self.assertEqual(first.read_text(), "old-first\n")
            self.assertEqual(second.read_text(), "old-second\n")
            completed = json.loads((bundle / "transaction.json").read_text())
            self.assertEqual(completed["state"], "rolled-back-manually")

    def test_apply_rejects_bundle_other_than_sealed_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sealed = Path(directory) / "sealed"
            different = Path(directory) / "different"
            plan = {"bundle": str(sealed.resolve()), "root": str(Path(directory))}
            with self.assertRaisesRegex(migrate.MigrationError, "differs from sealed plan bundle"):
                migrate.apply_plan(plan, different)

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

    def test_restore_validates_all_backups_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.json"
            second = root / "second.json"
            first_backup = root / "first.backup"
            second_backup = root / "second.backup"
            first.write_text("new-first\n")
            second.write_text("new-second\n")
            first_backup.write_text("old-first\n")
            second_backup.write_text("old-second\n")
            records = {
                str(first): {
                    "path": str(first_backup),
                    "sha256": migrate._sha256(first_backup),
                },
                str(second): {
                    "path": str(second_backup),
                    "sha256": migrate._sha256(second_backup),
                },
            }
            second_backup.write_text("corrupt\n")
            with self.assertRaises(migrate.MigrationError):
                migrate._restore(records)
            self.assertEqual(first.read_text(), "new-first\n")

    def test_manual_rollback_rejects_completed_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            root = self._canonical_root(directory)
            transaction = {
                "root": str(root),
                "state": "rolled-back",
                "backups": {
                    "target": {
                        "path": "backup",
                        "sha256": "hash",
                    }
                },
            }
            transaction["backups_sha256"] = migrate._mapping_sha256(transaction["backups"])
            (bundle / "transaction.json").write_text(json.dumps(transaction))
            with self.assertRaisesRegex(migrate.MigrationError, "not rollbackable"):
                migrate.rollback(bundle)


if __name__ == "__main__":
    unittest.main()
