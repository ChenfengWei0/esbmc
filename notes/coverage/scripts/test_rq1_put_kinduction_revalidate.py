#!/usr/bin/env python3

import json
import tempfile
import unittest
from pathlib import Path

import rq1_put_kinduction_revalidate as runner


class ClassificationTest(unittest.TestCase):

    def test_guard_free_historical_result_is_not_resume_compatible(self):
        self.assertFalse(runner.guard_cache_valid({"status": "proved"}, "abc"))

    def test_current_guard_semantics_result_is_resume_compatible(self):
        self.assertTrue(runner.guard_cache_valid({
            "status": "proved",
            "guard_semantics": runner.GUARD_SEMANTICS,
            "path_guard_digest": "abc",
        }, "abc"))

    def test_changed_guard_invalidates_resume_cache(self):
        self.assertFalse(runner.guard_cache_valid({
            "guard_semantics": runner.GUARD_SEMANTICS,
            "path_guard_digest": "old",
        }, "new"))

    def test_vacuous_historical_path_is_remapped(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "certify.log"
            log.write_text("--path-cov-certify: RESULT: VACUOUS\n")
            self.assertTrue(
                runner.certification_needs_remap("inconclusive", log))

    def test_solver_inconclusive_without_vacuity_is_not_remapped(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "certify.log"
            log.write_text("VERIFICATION UNKNOWN\n")
            self.assertFalse(
                runner.certification_needs_remap("inconclusive", log))

    def test_vacuous_text_does_not_override_proved_status(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "certify.log"
            log.write_text("--path-cov-certify: RESULT: VACUOUS\n")
            self.assertFalse(runner.certification_needs_remap("proved", log))

    def test_materialize_fixtures_copies_fixture_to_audit_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "retained.json"
            source.write_text('{"contract":"C","state":{}}')

            command = runner.materialize_fixtures(
                ["esbmc", "a.solast", "--path-cov-fixture", str(source)],
                root / "audit")

            isolated = Path(command[-1])
            self.assertTrue(isolated.is_file())
            self.assertEqual(isolated.read_bytes(), source.read_bytes())
            self.assertTrue(isolated.is_relative_to(root / "audit" / "fixtures"))

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
            ["arg", "state.owner", "state.balance", "block.timestamp", "msg.value"],
        )

    def test_certify_spec_preserves_legacy_state_pins_without_manifest(self):
        source = {"unit": "f", "enc": 3}
        put = {"pins": {"state.balance": "7", "msg.sender": "1"}}

        spec = runner.certify_spec(source, put)

        self.assertEqual(
            [entry["name"] for entry in spec["box"]],
            ["state.balance", "msg.sender", "msg.value"],
        )

    def test_certify_spec_restores_implicit_zero_call_value(self):
        source = {"unit": "f", "enc": 3}
        put = {"region": {"msg.sender": [1, 9]}}

        spec = runner.certify_spec(source, put)

        self.assertEqual(spec["box"][-1], {
            "name": "msg.value", "lo": "0", "hi": "0"
        })

    def test_certify_spec_carries_materialized_path_guards(self):
        guard = {"any": [{
            "lhs": {"kind": "coord", "name": "state.balance[msg.sender]"},
            "op": "<",
            "rhs": {"kind": "coord", "name": "amount"},
        }]}

        spec = runner.certify_spec(
            {"unit": "f", "enc": 3}, {"_audit_path_guards": [guard]})

        self.assertEqual(spec["guards"], [guard])

    def test_materialized_guard_normalizes_metacoin_state_and_parameter(self):
        put = {
            "region": {"state.balances[msg.sender]": [0, 10]},
            "stats": {"assertion_oracles": []},
        }

        guard = runner.parse_materialized_guard(
            "_pre_balances_msg_sender < amount", put)

        self.assertEqual(guard, {"any": [{
            "lhs": {
                "kind": "coord", "name": "state.balances[msg.sender]",
            },
            "op": "<",
            "rhs": {"kind": "coord", "name": "amount"},
        }]})

    def test_materialized_guard_preserves_disjunction_and_sender_cast(self):
        put = {
            "pins": {"state.owner": "1"},
            "stats": {"assertion_oracles": []},
            "_audit_guard_address_names": ["p_msg_sender"],
        }

        guard = runner.parse_materialized_guard(
            "(uint256(uint160(p_msg_sender)) == _pre_owner || amount == 0)", put)

        self.assertEqual(len(guard["any"]), 2)
        self.assertEqual(guard["any"][0]["lhs"], {
            "kind": "coord", "name": "msg.sender",
        })
        self.assertEqual(guard["any"][0]["rhs"], {
            "kind": "coord", "name": "state.owner",
        })

    def test_unstructured_materialized_guard_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            runner.parse_materialized_guard("hash(amount) != 0", {})

    def test_truncating_cast_without_address_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "truncating address cast"):
            runner.parse_materialized_guard("uint160(amount) == 0", {})

    def test_guard_count_mismatch_is_rejected_instead_of_widened(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Put.t.sol"
            source.write_text(
                "contract Put { function test_put() public { uint256 x = 0; } }\n")
            put_json = root / "put.json"
            put = {
                "file": str(source),
                "test": "test_put",
                "stats": {"path_guard_assumes": 1},
            }

            with self.assertRaisesRegex(ValueError, "count mismatch"):
                runner.materialized_path_guards(put_json, put)

    def test_valid_row_source_overrides_stale_put_json_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale = root / "stale.t.sol"
            current = root / "current.t.sol"
            stale.write_text("""
contract Put { function test_put(uint256 amount) public {
// complete-path guard recovered from the emit report
vm.assume(amount < 1);
} }
""")
            current.write_text("""
contract Put { function test_put(uint256 amount) public {
// complete-path guard recovered from the emit report
vm.assume(amount < 5);
} }
""")
            put = {
                "file": str(stale), "test": "test_put",
                "stats": {"path_guard_assumes": 1},
            }

            guards = runner.materialized_path_guards(
                root / "put.json", put, current)

        self.assertEqual(
            guards[0]["any"][0]["rhs"], {"kind": "literal", "value": "5"})

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

    def test_resumed_certify_spec_keeps_proved_remap_identity(self):
        source = {"unit": "f", "enc": 8191, "depth": 12}
        retained = {"remapped": True, "enc": 63, "depth": 5}

        spec = runner.resumed_certify_spec(source, {}, retained)

        self.assertEqual((spec["enc"], spec["depth"]), (63, 5))

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
        self.assertEqual(filtered["candidate_policy"], "exact")

    def test_final_assert_spec_skips_spec_without_expected_variable(self):
        source = {"unit": "f", "vars": [{"name": "state.x", "equals": []}]}
        expected = [{"var": "return", "text": "return == true", "layer": "return"}]

        self.assertIsNone(runner.final_assert_spec(source, expected))

    def test_final_assert_spec_makes_frame_equality_exact(self):
        source = {"unit": "f", "vars": [{"name": "owner"}]}
        expected = [{"var": "owner", "text": "post == pre", "layer": "state"}]

        filtered = runner.final_assert_spec(source, expected)

        self.assertEqual(filtered["vars"], [{
            "name": "owner",
            "equals": [{"id": "rq1pre", "term": {"kind": "pre"}}],
            "abs": [],
            "deltas": [],
        }])
        self.assertEqual(filtered["candidate_policy"], "exact")

    def test_final_assert_spec_drops_generic_var_owned_only_by_r2(self):
        source = {"unit": "f", "vars": [{"name": "balance"}]}
        expected = [{
            "var": "balance", "text": "post == 42", "layer": "state",
        }]

        self.assertIsNone(runner.final_assert_spec(source, expected))

    def test_final_assert_spec_keeps_only_emitted_structured_candidates(self):
        kept = {
            "id": "kept",
            "term": {"kind": "op", "op": "mul",
                     "lhs": {"kind": "pre"},
                     "rhs": {"kind": "coord", "name": "amount"}},
        }
        source = {
            "unit": "f",
            "vars": [{
                "name": "balance",
                "equals": [
                    kept,
                    {"id": "dropped", "term": {"kind": "literal", "value": "7"}},
                ],
                "abs": [{
                    "id": "dropped-abs",
                    "lo": {"kind": "literal", "value": "0"},
                    "hi": {"kind": "literal", "value": "7"},
                }],
                "deltas": [],
            }],
        }
        expected = [{
            "var": "balance",
            "text": "post == (pre * amount)",
            "layer": "state",
        }]

        filtered = runner.final_assert_spec(source, expected)

        self.assertEqual(filtered["vars"], [{
            "name": "balance", "equals": [kept], "abs": [], "deltas": [],
        }])

    def test_final_assert_spec_matches_boolean_state_literal_spelling(self):
        candidate = {
            "id": "true", "term": {"kind": "literal", "value": "1"},
        }
        source = {"unit": "f", "vars": [{
            "name": "enabled", "equals": [candidate], "abs": [], "deltas": [],
        }]}
        expected = [{
            "var": "enabled", "text": "post == true", "layer": "state",
        }]

        filtered = runner.final_assert_spec(source, expected)

        self.assertEqual(filtered["vars"][0]["equals"], [candidate])

    def test_final_assert_spec_recovers_emitted_literal_missing_from_source_spec(self):
        source = {"unit": "f", "vars": [{
            "name": "return",
            "equals": [{
                "id": "state", "term": {"kind": "coord", "name": "state.x"},
            }],
            "abs": [],
            "deltas": [],
        }]}
        expected = [
            {"var": "return", "text": "return == 0", "layer": "return"},
            {"var": "return", "text": "return == state.x", "layer": "return"},
        ]

        filtered = runner.final_assert_spec(source, expected)

        terms = [entry["term"] for entry in filtered["vars"][0]["equals"]]
        self.assertIn({"kind": "literal", "value": "0"}, terms)
        self.assertIn({"kind": "coord", "name": "state.x"}, terms)

    def test_authoritative_assert_spec_runs_before_r2_batches(self):
        paths = [Path("spec.r2_batch_s1.json"), Path("spec.json"),
                 Path("spec.r2_source_assign_s1.json")]

        ordered = runner.ordered_assert_specs(paths)

        self.assertEqual(ordered[0], Path("spec.json"))
        self.assertEqual(ordered[1:], sorted(paths[:1] + paths[2:]))

    def test_generic_assert_ladders_are_split_per_variable(self):
        variables = [{"name": "a"}, {"name": "b"}, {"name": "c"}]

        self.assertEqual(
            runner.assertion_var_chunks(variables),
            [[{"name": "a"}], [{"name": "b"}], [{"name": "c"}]],
        )

    def test_structured_assert_ladders_still_use_five_variable_chunks(self):
        variables = [{"name": str(index), "equals": []}
                     for index in range(6)]

        chunks = runner.assertion_var_chunks(variables)

        self.assertEqual([len(chunk) for chunk in chunks], [5, 1])

    def test_synthesized_frame_equalities_are_split_per_variable(self):
        variables = [{
            "name": name,
            "equals": [{"id": "rq1pre", "term": {"kind": "pre"}}],
        } for name in ("a", "b")]

        self.assertEqual(
            [len(chunk) for chunk in runner.assertion_var_chunks(variables)],
            [1, 1],
        )

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

    def test_proved_post_constant_and_entry_pin_imply_strict_increase(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            put_json = root / "put.json"
            put_json.write_text(json.dumps({"pins": {"state.x": "0"}}))
            log = root / "assert.log"
            log.write_text("--path-cov-assert: x: post == 42  HOLDS\n")
            item = {
                "put_json": str(put_json),
                "expected_ladder": [{
                    "var": "x", "text": "post > pre", "layer": "state",
                }],
            }

            status, observed = runner.classify_obligation(item, [
                {"status": "proved", "log": str(log)},
                {"status": "proved", "log": str(log)},
            ])

        self.assertEqual(status, "proved")
        self.assertEqual(observed[0]["status"], "proved")
        self.assertIn("entry pin state.x == 0", observed[0]["proof_basis"])


if __name__ == "__main__":
    unittest.main()
