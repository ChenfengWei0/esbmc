#!/usr/bin/env python3
"""Integration checks for RQ3 no-unit deploy diagnostics."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rq1_veriput_run import emit_no_unit_deploy_fallback, summarize_put_artifacts
from rq3_deploy_diagnostic_repair import RepairError, apply_transaction
from veriput_subjects import PreparedSubject


class RQ3DeployDiagnosticTest(unittest.TestCase):

    def _emit(
            self,
            root: Path,
            *,
            publish: bool,
            source_text: str = "pragma solidity >=0.8.0; contract Smoke {}\n") -> tuple[dict, dict]:
        source = root / "flat.sol"
        source.write_text(source_text)
        subject = PreparedSubject(
            benchmark="real203",
            subject_id="deploy-smoke",
            root=str(root),
            flat_sol=str(source),
            solast="",
            contract="Smoke",
            unit="",
            solc_bin=None,
            solc_extra=(),
            metadata={},
        )
        schedule = {
            "jobs": [],
            "no_unit_rows": [{
                "status": "no-units",
                "reason": "target AST has no FunctionDefinition nodes",
            }],
            "summary": {
                "no_unit_rows": 1,
            },
        }

        case_dir = root / "case"
        stage = emit_no_unit_deploy_fallback(
            subject,
            case_dir,
            schedule,
            10,
            publish_unoracled_deploy_smoke=publish,
        )
        return stage, summarize_put_artifacts(case_dir / "put")

    def test_rq3_deploy_smoke_is_diagnostic_not_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage, summary = self._emit(root, publish=False)
            put_json = json.loads(next(root.rglob("put.json")).read_text())
            put_summary = json.loads(next(root.rglob("put-summary.json")).read_text())

            self.assertEqual(put_json["kind"], "diagnostic")
            self.assertEqual(put_summary["deliverable_b"]["rows"][0]["kind"], "diagnostic")
            self.assertEqual(put_summary["emission"]["concrete_replays_emitted"], 0)
            self.assertFalse(stage["published_as_deliverable"])
            self.assertEqual(summary["raw"], 0)
            self.assertEqual(summary["valid"], 0)

    def test_non_ablation_deploy_smoke_keeps_legacy_raw_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, summary = self._emit(Path(tmp), publish=True)

            self.assertTrue(stage["published_as_deliverable"])
            self.assertEqual(summary["raw"], 1)
            self.assertEqual(summary["valid"], 0)

    def test_rq3_constructor_argument_repair_without_oracle_is_diagnostic(self):
        source = """pragma solidity >=0.8.0;
contract Smoke {
  constructor(uint256 value) { require(value > 5); }
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage, summary = self._emit(root, publish=False, source_text=source)
            put_json = json.loads(next(root.rglob("put.json")).read_text())

            self.assertEqual(stage["forge_status"], "Success")
            self.assertEqual(put_json["stage4_kind"], "constructor-arg-repair")
            self.assertEqual(put_json["kind"], "diagnostic")
            self.assertEqual(summary["raw"], 0)

    def test_rq3_source_grounded_constructor_revert_remains_valid_concrete(self):
        source = """pragma solidity >=0.8.0;
contract Smoke {
  constructor() { require(false, "expected"); }
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _stage, summary = self._emit(root, publish=False, source_text=source)
            put_json = json.loads(next(root.rglob("put.json")).read_text())

            self.assertEqual(put_json["stage4_kind"], "constructor-revert-only")
            self.assertTrue(put_json["valid_reference_test"])
            self.assertEqual(put_json["kind"], "concrete")
            self.assertEqual(summary["raw"], 1)
            self.assertEqual(summary["valid"], 1)

    def test_apply_transaction_rechecks_preimages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.json"
            second = root / "second.json"
            first.write_bytes(b"first-before")
            second.write_bytes(b"second-before")
            preimages = {first: first.read_bytes(), second: second.read_bytes()}
            writes = {first: b"first-after", second: b"second-after"}
            second.write_bytes(b"concurrent-edit")

            with self.assertRaises(RepairError):
                apply_transaction(writes, preimages, root / "archive")
            self.assertEqual(first.read_bytes(), b"first-before")
            self.assertEqual(second.read_bytes(), b"concurrent-edit")
            self.assertFalse((root / "archive").exists())

    def test_apply_transaction_archives_preimages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "result.json"
            target.write_bytes(b"before")
            archive = root / "archive"
            apply_transaction({target: b"after"}, {target: b"before"}, archive)

            self.assertEqual(target.read_bytes(), b"after")
            self.assertEqual(next(archive.iterdir()).read_bytes(), b"before")


if __name__ == "__main__":
    unittest.main()
