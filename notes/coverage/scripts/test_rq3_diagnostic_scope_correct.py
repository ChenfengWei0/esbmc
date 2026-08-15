#!/usr/bin/env python3
"""Tests for the RQ3 unauthenticated-diagnostic scope correction."""

# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import rq3_diagnostic_scope_correct as repair


def artifact(name: str, *, kind: str = "concrete", tags=None, valid=False) -> dict:
    return {
        "file": f"/root/{name}.t.sol",
        "test": f"test_{name}",
        "kind": kind,
        "oracle_tags": list(tags or []),
        "oracle_combo_tag": "+".join(tags or []),
        "oracle_classes": [],
        "forge_status": "Success" if valid else "Failure",
        "valid_reference_test": valid,
    }


def target(row: dict) -> dict:
    return {"artifact_identity": {"file": row["file"], "test": row["test"]}}


class ScopeCorrectionTests(unittest.TestCase):

    def test_result_moves_exact_raw_only_artifact_to_diagnostics(self) -> None:
        removed = artifact("removed", tags=["R0"])
        retained = artifact("retained", tags=["R1"])
        valid = artifact("valid", tags=["R2"], valid=True)
        summary = {
            "raw_artifacts": [removed, retained, valid],
            "raw_tests": [removed, retained, valid],
            "valid_artifacts": [valid],
            "valid_tests": [valid],
            "unpublished_valid_tests": [removed],
            "valid_put_with_R1": 0,
            "valid_put_with_R2": 0,
            "valid_put_with_R1_or_R2": 0,
            "valid_put_without_R1R2": 0,
        }
        corrected = repair.scope_correct_result_summary(summary, {repair._artifact_key(removed)})
        self.assertEqual(corrected["raw"], 2)
        self.assertEqual(corrected["valid"], 1)
        self.assertEqual(corrected["raw_oracle_tag_counts"], {"R1": 1, "R2": 1})
        self.assertEqual(corrected["valid_oracle_tag_counts"], {"R2": 1})
        self.assertEqual(len(corrected["diagnostic_artifacts"]), 1)
        self.assertEqual(corrected["unpublished_valid_tests"], [])
        diagnostic = corrected["diagnostic_artifacts"][0]
        self.assertEqual(diagnostic["kind"], "diagnostic")
        self.assertEqual(diagnostic["diagnostic_original_kind"], "concrete")
        self.assertFalse(diagnostic["published_as_deliverable"])
        self.assertEqual(corrected["diagnostic_scope_correction"]["partition_sha256"],
                         repair.PARTITION_SHA256)

    def test_result_rejects_target_that_is_currently_valid(self) -> None:
        row = artifact("valid", valid=True)
        summary = {
            "raw_artifacts": [row],
            "raw_tests": [row],
            "valid_artifacts": [row],
            "valid_tests": [row],
        }
        with self.assertRaisesRegex(repair.ScopeCorrectionError, "intersects current valid"):
            repair.scope_correct_result_summary(summary, {repair._artifact_key(row)})

    def test_result_document_keeps_top_level_and_row_persistence_equal(self) -> None:
        removed = artifact("removed")
        summary = {
            "raw_artifacts": [removed],
            "raw_tests": [removed],
            "valid_artifacts": [],
            "valid_tests": [],
            "status": "persistence-error",
            "completion_status": "persistence-error",
            "reason": "old failure",
            "persistence_failure_reason": "old failure",
            "concrete_replay_persistence": {
                "valid_concrete_count": 1
            },
        }
        document = {
            "put": copy.deepcopy(summary),
            "row": copy.deepcopy(summary),
            "concrete_replay_persistence": {
                "valid_concrete_count": 1
            },
            "persistence_publication_failure": "old failure",
        }

        def refresh(_case_dir, _old, corrected, _targets):
            corrected["concrete_replay_persistence"] = {
                "valid_concrete_count": 0,
                "valid_concrete_missing_count": 0,
            }
            corrected["persistence_failure_reason"] = None

        with mock.patch.object(repair, "_refresh_persistence_coverage", side_effect=refresh):
            corrected = repair.scope_correct_result_document(document, Path("/case"),
                                                             {repair._artifact_key(removed)})
        self.assertEqual(corrected["concrete_replay_persistence"],
                         corrected["row"]["concrete_replay_persistence"])
        self.assertIsNone(corrected["persistence_publication_failure"])
        self.assertEqual(corrected["row"]["status"], "no-output")
        self.assertEqual(corrected["put"]["status"], "no-output")
        self.assertEqual(corrected["row"]["reason"], corrected["put"]["reason"])
        self.assertIsNone(corrected["put"]["persistence_failure_reason"])

    def test_put_summary_retains_evidence_outside_deliverable_rows(self) -> None:
        row = artifact("candidate", valid=True)
        document = {
            "emission": {
                "concrete_replays_emitted": 1
            },
            "deliverable_b": {
                "valid_reference_tests": {
                    "concrete": 1,
                    "put": 0,
                    "total": 1
                },
                "rows": [row],
            },
        }
        corrected = repair.scope_correct_put_summary(document, target(row))
        self.assertEqual(corrected["deliverable_b"]["rows"], [])
        self.assertEqual(corrected["emission"]["concrete_replays_emitted"], 0)
        self.assertEqual(corrected["deliverable_b"]["valid_reference_tests"]["total"], 0)
        self.assertEqual(corrected["diagnostic_rows"][0]["forge_status"], "Success")
        self.assertEqual(corrected["diagnostic_rows"][0]["kind"], "diagnostic")

    def test_put_json_requires_exact_identity(self) -> None:
        row = artifact("candidate")
        corrected = repair.scope_correct_put_json(row, target(row))
        self.assertEqual(corrected["kind"], "diagnostic")
        self.assertEqual(corrected["diagnostic_partition_sha256"], repair.PARTITION_SHA256)
        wrong = target(artifact("other"))
        with self.assertRaisesRegex(repair.ScopeCorrectionError, "sealed concrete"):
            repair.scope_correct_put_json(row, wrong)

    def test_canonical_partition_seal_and_population(self) -> None:
        document, rows = repair._load_partition(repair.DEFAULT_PARTITION, repair.DEFAULT_ROOT)
        self.assertEqual(document["partition_sha256"], repair.PARTITION_SHA256)
        self.assertEqual(len(rows), 66)
        self.assertEqual(
            len({(row["identity"]["dataset"], row["identity"]["case"])
                 for row in rows}), 17)

    def test_plan_seal_detects_staged_write_mutation(self) -> None:
        path = Path("/root/result.json")
        writes = {path: b"after\n"}
        serializable = {
            "root": "/root",
            "writes": {
                str(path): {
                    "sha256": repair.transaction._sha256_bytes(writes[path]),
                    "bytes": len(writes[path]),
                }
            },
        }
        plan = copy.deepcopy(serializable)
        plan["plan_sha256"] = repair.transaction._sha256_bytes(
            repair.transaction._json_bytes(serializable))
        plan["_write_bytes"] = writes
        repair._verify_plan(plan)
        plan["_write_bytes"] = {path: b"changed\n"}
        with self.assertRaisesRegex(repair.ScopeCorrectionError, "write bytes"):
            repair._verify_plan(plan)

    def test_compare_before_write_covers_full_result_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result.json"
            path.write_text("before\n")
            result_snapshot = {str(path): repair.transaction._sha256(path)}
            plan = {
                "preimages": dict(result_snapshot),
                "result_snapshot": result_snapshot,
                "result_snapshot_sha256": repair.transaction._mapping_sha256(result_snapshot),
            }
            repair._verify_preimages(plan)
            path.write_text("changed\n")
            with self.assertRaisesRegex(repair.ScopeCorrectionError, "compare-before-write"):
                repair._verify_preimages(plan)

    def test_failed_post_audit_restores_every_preimage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target_path = root / "target.json"
            audit_path = root / "audit.json"
            target_path.write_text("before-target\n")
            audit_path.write_text("before-audit\n")
            writes = {target_path: b"after-target\n"}
            preimages = {
                str(target_path): repair.transaction._sha256(target_path),
                str(audit_path): repair.transaction._sha256(audit_path),
            }
            serializable = {
                "root": str(root),
                "partition_sha256": repair.PARTITION_SHA256,
                "preimages": preimages,
                "result_snapshot": {},
                "result_snapshot_sha256": repair.transaction._mapping_sha256({}),
                "writes": {
                    str(target_path): {
                        "sha256": repair.transaction._sha256_bytes(writes[target_path]),
                        "bytes": len(writes[target_path]),
                    }
                },
            }
            plan = copy.deepcopy(serializable)
            plan["plan_sha256"] = repair.transaction._sha256_bytes(
                repair.transaction._json_bytes(serializable))
            plan["_write_bytes"] = writes
            bundle = root / "bundle"
            with mock.patch.object(repair,
                                   "_post_audit",
                                   side_effect=repair.ScopeCorrectionError("forced")):
                with self.assertRaisesRegex(repair.ScopeCorrectionError, "forced"):
                    repair._apply_locked(plan, bundle)
            self.assertEqual(target_path.read_text(), "before-target\n")
            self.assertEqual(audit_path.read_text(), "before-audit\n")
            tx_doc = json.loads((bundle / "transaction.json").read_text())
            self.assertEqual(tx_doc["state"], "rolled-back")

    def test_interrupted_automatic_restore_is_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Results" / "RQ3" / "VeriExploit" / "No_Cer_Reg"
            root.mkdir(parents=True)
            target_path = root / "target.json"
            audit_path = root / "audit.json"
            target_path.write_text("before-target\n")
            audit_path.write_text("before-audit\n")
            writes = {target_path: b"after-target\n"}
            preimages = {
                str(target_path): repair.transaction._sha256(target_path),
                str(audit_path): repair.transaction._sha256(audit_path),
            }
            serializable = {
                "root": str(root),
                "partition_sha256": repair.PARTITION_SHA256,
                "preimages": preimages,
                "result_snapshot": {},
                "result_snapshot_sha256": repair.transaction._mapping_sha256({}),
                "writes": {
                    str(target_path): {
                        "sha256": repair.transaction._sha256_bytes(writes[target_path]),
                        "bytes": len(writes[target_path]),
                    }
                },
            }
            plan = copy.deepcopy(serializable)
            plan["plan_sha256"] = repair.transaction._sha256_bytes(
                repair.transaction._json_bytes(serializable))
            plan["_write_bytes"] = writes
            bundle = Path(temporary) / "bundle"
            with mock.patch.object(repair,
                                   "_post_audit",
                                   side_effect=repair.ScopeCorrectionError("forced")):
                with mock.patch.object(repair.transaction,
                                       "_restore",
                                       side_effect=RuntimeError("interrupted")):
                    with self.assertRaisesRegex(RuntimeError, "interrupted"):
                        repair._apply_locked(plan, bundle)
            self.assertEqual(target_path.read_text(), "after-target\n")
            self.assertEqual(
                json.loads((bundle / "transaction.json").read_text())["state"], "rolling-back")
            repair.rollback(bundle)
            self.assertEqual(target_path.read_text(), "before-target\n")
            self.assertEqual(audit_path.read_text(), "before-audit\n")
            self.assertEqual(
                json.loads((bundle / "transaction.json").read_text())["state"],
                "rolled-back-manually")


if __name__ == "__main__":
    unittest.main()
