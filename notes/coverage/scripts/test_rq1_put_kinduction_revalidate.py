#!/usr/bin/env python3

import json
import tempfile
import unittest
from pathlib import Path

import rq1_put_kinduction_revalidate as runner


class ClassificationTest(unittest.TestCase):

    def test_certify_spec_keeps_only_materialized_state_pins(self):
        source = {"unit": "f", "enc": 3, "depth": 2}
        put = {
            "region": {"arg": [0, 10], "state.owner": [1, 1]},
            "pins": {
                "state.owner": "1",
                "state.balance": "7",
                "state.unmaterialized": "9",
                "block.timestamp": "12",
            },
            "stats": {"state_stored": ["state.balance := 7"]},
        }

        spec = runner.certify_spec(source, put)
        names = [entry["name"] for entry in spec["box"]]

        self.assertEqual(
            names,
            ["arg", "state.owner", "state.balance", "block.timestamp"],
        )

    def test_certify_spec_preserves_legacy_state_pins_without_manifest(self):
        source = {"unit": "f", "enc": 3}
        put = {"pins": {"state.balance": "7", "msg.sender": "1"}}

        spec = runner.certify_spec(source, put)

        self.assertEqual(
            [entry["name"] for entry in spec["box"]],
            ["state.balance", "msg.sender"],
        )

    def test_certify_spec_normalizes_historical_abi_value_gate_marker(self):
        source = {"unit": "f", "enc": 1, "depth": 0}
        put = {"stage4_kind": "abi-value-gate", "region": {"msg.value": [1, 9]}}

        spec = runner.certify_spec(source, put)

        self.assertEqual((spec["enc"], spec["depth"]), (2, 1))

    def test_certify_spec_does_not_rewrite_arbitrary_abi_gate_path(self):
        source = {"unit": "f", "enc": 6, "depth": 2}
        put = {"stage4_kind": "abi-value-gate"}

        spec = runner.certify_spec(source, put)

        self.assertEqual((spec["enc"], spec["depth"]), (6, 2))

    def test_final_assert_spec_removes_irrelevant_variable(self):
        expected = [{
            "var": "return",
            "text": "return == true",
            "layer": "return",
        }]
        return_spec = {
            "name": "return",
            "equals": [{"id": "src1", "term": {"kind": "literal", "value": "1"}}],
            "abs": [],
            "deltas": [],
        }
        source = {
            "unit": "f",
            "vars": [
                return_spec,
                {"name": "balances[msg.sender]", "equals": [{"id": "e0"}]},
            ],
        }

        filtered = runner.final_assert_spec(source, expected)

        self.assertIsNotNone(filtered)
        self.assertEqual(filtered["vars"], [return_spec])

    def test_final_assert_spec_skips_spec_without_expected_variable(self):
        source = {"unit": "f", "vars": [{"name": "state.x", "equals": []}]}
        expected = [{"var": "return", "text": "return == true", "layer": "return"}]

        self.assertIsNone(runner.final_assert_spec(source, expected))

    def test_authoritative_assert_spec_runs_before_r2_batches(self):
        paths = [Path("spec.r2_batch_s1.json"), Path("spec.json"),
                 Path("spec.r2_source_assign_s1.json")]

        ordered = runner.ordered_assert_specs(paths)

        self.assertEqual(ordered[0], Path("spec.json"))
        self.assertEqual(ordered[1:], sorted(paths[:1] + paths[2:]))

    def test_matching_revert_oracle_is_discharged_by_certified_path(self):
        item = {
            "exit_kind": "revert",
            "expected_ladder": [{
                "var": "exit",
                "text": "path exits through revert",
                "layer": "exit",
                "classes": ["R0"],
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "certify.log"
            log.write_text("certified")
            status, observed = runner.classify_obligation(
                item, [{
                    "status": "proved",
                    "path_exit_kind": "revert",
                    "log": str(log),
                }])

        self.assertEqual(status, "proved")
        self.assertEqual(observed[0]["status"], "proved")

    def test_current_exit_kind_refutes_stale_exit_oracle(self):
        item = {
            "expected_ladder": [{
                "var": "exit",
                "text": "path exits through revert",
                "layer": "exit",
                "classes": ["R0"],
            }],
        }
        status, observed = runner.classify_obligation(
            item, [{"status": "proved", "path_exit_kind": "normal"}])

        self.assertEqual(status, "refuted")
        self.assertEqual(observed[0]["status"], "refuted")

    def test_report_path_exit_kind_reads_nonvacuity_row(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "cov-report.json"
            report.write_text(json.dumps({"claims": [
                {"path_id": "6#exit0", "exit_kind": "normal"},
                {"path_id": "6#nonvacuous", "exit_kind": "revert"},
            ]}))

            self.assertEqual(runner.report_path_exit_kind(report, "6"), "revert")

    def test_undetermined_exit_oracle_is_not_discharged(self):
        item = {
            "exit_kind": "normal",
            "expected_ladder": [{
                "var": "exit",
                "text": "path exits through revert",
                "layer": "exit",
                "classes": ["R0"],
            }],
        }
        status, observed = runner.classify_obligation(
            item, [{
                "status": "proved",
                "path_exit_kind": "undetermined",
                "log": "/missing",
            }])

        self.assertEqual(status, "inconclusive")
        self.assertEqual(observed[0]["status"], "inconclusive")

    def test_nonzero_oracle_is_implied_by_proved_nonzero_exact_value(self):
        item = {
            "expected_ladder": [{
                "var": "return",
                "text": "return != 0",
                "layer": "return",
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "assert.log"
            log.write_text(
                "--path-cov-assert: return: return == 18  HOLDS\n")
            status, observed = runner.classify_obligation(
                item, [{"status": "proved", "log": str(log)}, {
                    "status": "proved",
                    "log": str(log),
                }])

        self.assertEqual(status, "proved")
        self.assertEqual(observed[0]["status"], "proved")
        self.assertEqual(
            observed[0]["proof_basis"],
            "implied by return == 18 HOLDS",
        )

    def test_zero_exact_value_does_not_imply_nonzero_oracle(self):
        item = {
            "expected_ladder": [{
                "var": "return",
                "text": "return != 0",
                "layer": "return",
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "assert.log"
            log.write_text(
                "--path-cov-assert: return: return == 0  HOLDS\n")
            status, observed = runner.classify_obligation(
                item, [{"status": "proved", "log": str(log)}, {
                    "status": "proved",
                    "log": str(log),
                }])

        self.assertEqual(status, "inconclusive")
        self.assertEqual(observed[0]["status"], "inconclusive")


if __name__ == "__main__":
    unittest.main()
