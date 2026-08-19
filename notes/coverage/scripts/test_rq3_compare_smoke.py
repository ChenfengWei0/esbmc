#!/usr/bin/env python3
"""Tests for strict Full-versus-ablation smoke accounting."""

import json
import tempfile
import unittest
from pathlib import Path

import rq3_compare_smoke


class CompareSmokeTest(unittest.TestCase):

    def _full(self, root: Path, put_count: int, r1r2_count: int) -> dict:
        rows = []
        for index in range(put_count):
            record = root / f"put-{index}.json"
            record.write_text(json.dumps({
                "stats": {
                    "oracle_class_counts": ({"R1": 1} if index < r1r2_count else {}),
                    "r2_subfamily_counts": ({"R2.1": 1} if index < r1r2_count else {}),
                }
            }))
            rows.append({"kind": "put", "test": f"test_put_{index}",
                         "put_json": str(record), "valid_reference_test": True})
        rows.append({"kind": "concrete", "test": "test_concrete",
                     "valid_reference_test": True})
        (root / "result.json").write_text(json.dumps({
            "timing": {"wall_total_s": 12.5}, "valid_tests": rows
        }))
        return rq3_compare_smoke.full_metrics(root)

    def test_full_metrics_and_expected_ablation_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            full = self._full(root, 3, 2)
            self.assertEqual(full, {"valid": 4, "put": 3, "concrete": 1,
                                    "put_with_r1r2": 2, "r2_1": 2,
                                    "r2_2": 0, "r2_3": 0, "wall_s": 12.5})
            report = rq3_compare_smoke.compare(full, {
                "no-selection": {**full, "put": 2, "put_with_r1r2": 1},
                "no-test-oracle": {**full, "put": 3, "put_with_r1r2": 0},
                "no-cer-reg": {**full, "put": 0, "put_with_r1r2": 0},
            })
            self.assertTrue(report["passes"])

    def test_ablation_exceeding_full_is_a_failed_smoke(self):
        full = {"put": 2, "put_with_r1r2": 1}
        report = rq3_compare_smoke.compare(full, {
            "no-selection": {"put": 3, "put_with_r1r2": 2},
        })
        self.assertFalse(report["passes"])
        self.assertEqual(len(report["failures"]), 2)


if __name__ == "__main__":
    unittest.main()
