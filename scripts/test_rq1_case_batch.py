#!/usr/bin/env python3
"""Self-contained tests for strict RQ1 case accounting."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "notes/coverage/scripts/rq1_case_batch.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("rq1_case_batch", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
RQ1 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RQ1)
PROGRESS_SPEC = importlib.util.spec_from_file_location(
    "rq1_no_valid_progress", MODULE_PATH.with_name("rq1_no_valid_progress.py"))
assert PROGRESS_SPEC is not None and PROGRESS_SPEC.loader is not None
PROGRESS = importlib.util.module_from_spec(PROGRESS_SPEC)
sys.modules[PROGRESS_SPEC.name] = PROGRESS
PROGRESS_SPEC.loader.exec_module(PROGRESS)


def artifact(*,
             stage2_source: str = "certified-region",
             stage4_kind: str = "certified-region",
             kind: str = "concrete",
             valid: bool | None = True) -> dict:
    return {
        "file": f"{stage4_kind}.t.sol",
        "test": f"test_{stage4_kind}",
        "unit": "Target::hit",
        "kind": kind,
        "stage2_source": stage2_source,
        "stage4_kind": stage4_kind,
        "valid_reference_test": valid,
        "oracle_classes": ["R1"],
    }


class ResultNumbersTests(unittest.TestCase):

    def test_detailed_rows_override_stale_positive_aggregate(self) -> None:
        for row in (artifact(stage2_source="no_unit_deploy_fallback", stage4_kind="deploy-only"),
                    artifact(stage2_source="structural-deploy-only",
                             stage4_kind="creation-code-only")):
            result = {
                "row": {
                    "valid": 1,
                    "put_valid": 1,
                    "valid_put_with_R1_or_R2": 1
                },
                "put": {
                    "valid_tests": [row]
                },
            }
            self.assertEqual(RQ1.result_numbers(result)["valid"], 0)

    def test_progress_ledger_uses_the_same_strict_gate(self) -> None:
        accepted = artifact(kind="put")
        missing_flag = artifact(stage4_kind="missing-flag", valid=None)
        deploy_kind = artifact(stage4_kind="creation-code-only")
        deploy_source = artifact(stage2_source="structural_deploy_only")
        self.assertTrue(PROGRESS._is_valid_reference_test(accepted))
        for row in (missing_flag, deploy_kind, deploy_source):
            self.assertFalse(PROGRESS._is_valid_reference_test(row))
        self.assertEqual(PROGRESS.DENOMINATOR, 205)

    def test_constructor_revert_deploy_unit_remains_behavioral(self) -> None:
        constructor_revert = artifact(stage2_source="source_constructor_revert_fallback",
                                      stage4_kind="constructor-revert-only")
        constructor_revert["unit"] = "__deploy__"
        self.assertTrue(RQ1._is_valid_reference_test(constructor_revert))
        self.assertTrue(PROGRESS._is_valid_reference_test(constructor_revert))

    def test_only_explicitly_valid_non_deploy_rows_count(self) -> None:
        stale = artifact(stage4_kind="stale")
        stale["stale"] = True
        refused = artifact(stage4_kind="refused")
        refused["refused"] = True
        rows = [
            artifact(kind="put"),
            artifact(stage4_kind="missing-flag", valid=None),
            artifact(stage4_kind="explicit-invalid", valid=False),
            stale,
            refused,
        ]
        self.assertEqual(RQ1.result_numbers({"put": {
            "valid_tests": rows
        }}), {
            "valid": 1,
            "put": 1,
            "r1r2": 1,
            "quality_bucket": "VALID_PUT_R1R2",
        })

    def test_legacy_aggregate_is_used_without_detailed_rows(self) -> None:
        self.assertEqual(
            RQ1.result_numbers({
                "row": {
                    "valid": 2,
                    "put_valid": 1,
                    "valid_put_with_R1_or_R2": 0
                },
            })["valid"], 2)

    def test_stage4_summary_does_not_promote_deploy_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            subject_dir = Path(temp_dir)
            summary = subject_dir / "put/deploy_only/put-summary.json"
            summary.parent.mkdir(parents=True)
            summary.write_text(
                json.dumps({
                    "deliverable_b": {
                        "b":
                        True,
                        "quality": {
                            "valid_reference_rows": 1,
                            "put_rows": 0,
                            "r1r2_rows": 0,
                        },
                        "rows": [
                            artifact(stage2_source="no_unit_deploy_fallback",
                                     stage4_kind="deploy-only")
                        ],
                    }
                }))
            rows = RQ1.latest_put_summaries(subject_dir)
            self.assertEqual(RQ1.put_summary_numbers(rows), {
                "valid": 0,
                "put": 0,
                "r1r2": 0,
            })


class FixedInventoryTests(unittest.TestCase):

    def test_state_and_sync_use_unique_inventory_denominator(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inventory = root / "inventory.json"
            case_state = root / "case-state.json"
            results = root / "results"
            rows = [
                {
                    "bench": "bench",
                    "subject": "one"
                },
                {
                    "bench": "bench",
                    "subject": "two"
                },
                {
                    "bench": "bench",
                    "subject": "two"
                },
            ]
            inventory.write_text(json.dumps({"rows": rows}))
            case_state.write_text(
                json.dumps({
                    "cases": {
                        "bench/one": {
                            "state": "VALID_NO_PUT"
                        },
                        "outside/extra": {
                            "state": "VALID_PUT_R1R2"
                        },
                    }
                }))
            args = SimpleNamespace(
                inventory=inventory,
                case_state=case_state,
                results_root=results,
                batch_id="test",
            )
            summary = RQ1.state_summary(args)
            self.assertEqual(summary["case_count"], 2)
            self.assertEqual(summary["state_counts"], {
                "NO_VALID": 1,
                "VALID_NO_PUT": 1,
            })
            sync = RQ1.sync_state_from_results(args)
            self.assertEqual(sync["case_count"], 2)
            self.assertEqual(sum(sync["state_counts"].values()), 2)

    def test_progress_actual_scope_comes_from_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inventory = root / "inventory.json"
            results = root / "results"
            inventory.write_text(
                json.dumps({
                    "rows": [
                        {
                            "bench": "bench",
                            "subject": "valid"
                        },
                        {
                            "bench": "bench",
                            "subject": "invalid"
                        },
                    ]
                }))
            valid_result = results / "bench/subjects/valid/result.json"
            valid_result.parent.mkdir(parents=True)
            valid_result.write_text(json.dumps({
                "put": {
                    "valid_tests": [artifact()]
                },
            }))
            invalid_result = results / "bench/subjects/invalid/result.json"
            invalid_result.parent.mkdir(parents=True)
            invalid_result.write_text(
                json.dumps({
                    "put": {
                        "valid_tests": [
                            artifact(stage2_source="no_unit_deploy_fallback",
                                     stage4_kind="deploy-only")
                        ]
                    },
                }))
            actual = PROGRESS.actual_rq1_progress(results, inventory)
            self.assertEqual(actual["subjects"], 2)
            self.assertEqual(actual["valid_cases"], 1)
            self.assertEqual(actual["no_valid_cases"], 1)


if __name__ == "__main__":
    unittest.main()
