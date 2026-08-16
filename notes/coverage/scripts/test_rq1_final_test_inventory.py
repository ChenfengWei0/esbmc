#!/usr/bin/env python3
"""Unit tests for the RQ1 CE-obligation strength partition."""

from __future__ import annotations

import json
import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import rq1_final_test_inventory as inventory


class StructuralGetterAnchorAuditTest(unittest.TestCase):
    """Structural getter anchors require a source-bound double Forge gate."""

    def test_structural_getter_anchor_is_strength_confirmed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "test" / "Getter.t.sol"
            put_dir = root / "_wd" / "row"
            source_path.parent.mkdir(parents=True)
            put_dir.mkdir(parents=True)
            put_test = "test_put_Getter_value_path0"
            anchor_test = "test_structural_anchor_deadbeef"
            source = (
                "contract GetterTest {\n"
                f"  function {put_test}(address p_msg_sender) public {{\n"
                "    vm.prank(p_msg_sender); c0.value();\n"
                "  }\n"
                f"  function {anchor_test}() public {{\n"
                f"    this.{put_test}(address(uint160(1)));\n"
                "  }\n"
                "}\n")
            source_path.write_text(source)
            put_span = inventory._solidity_function_spans(source, put_test)[0][0]
            put_source = source[put_span[0]:put_span[1]]
            suite = json.dumps({
                "test/Getter.t.sol:GetterTest": {
                    "test_results": {
                        put_test + "(address)": {"status": "Success"},
                        anchor_test + "()": {"status": "Success"},
                    },
                    "warnings": [],
                }
            })
            (put_dir / "forge-suite.json").write_text(suite)
            put_json = put_dir / "put.json"
            put_json.write_text("{}\n")
            identity = ("peer182/example", "sol:Getter.value#0", "value", "0", "")
            row = {
                "path_function": identity[1],
                "unit": identity[2],
                "enc": 0,
                "piece": None,
                "test": put_test,
                "file": str(source_path),
                "put_json": str(put_json),
                "forge_status": "Success",
                "ce_anchor_forge_status": "Success",
                "ce_anchor": {
                    "status": "embedded",
                    "binding": "structural-abi-getter/v1",
                    "basis_kind": "structural-certificate-not-solver-ce",
                    "certification_source": "structural-abi-getter-no-coordinate",
                    "test": anchor_test,
                    "destination_put_test": put_test,
                    "destination_put_function_sha256": inventory._sha256(
                        put_source),
                    "destination_source_sha256": inventory._sha256(source),
                    "fixed_arguments": ["address(uint160(1))"],
                    "region": {"msg.sender": [1, 2**160 - 1]},
                    "forge_gate": {
                        "put_test": put_test,
                        "anchor_test": anchor_test,
                        "put_status": "Success",
                        "anchor_status": "Success",
                        "suite_log": "forge-suite.json",
                        "suite_log_sha256": inventory._sha256(suite),
                    },
                },
            }
            self.assertEqual(inventory._anchor_strength_audit(row, identity, root),
                             (True, "strength-confirmed"))
            row["ce_anchor"]["forge_gate"]["suite_log_sha256"] = "0" * 64
            self.assertEqual(inventory._anchor_strength_audit(row, identity, root)[1],
                             "structural-getter-forge-suite-mismatch")


class MembershipForgeJsonAuditTest(unittest.TestCase):
    """Membership evidence must prove one exact Foundry execution."""

    def _run(self, stdout, test="test_ce_membership_deadbeef"):
        source = "test/Exact.t.sol"
        return {
            "command": [
                "forge", "test", "--json", "--match-path", source, "--match-test",
                rf"^{test}(\(|$)", "--fuzz-runs", "256"
            ],
            "status": "Success",
            "returncode": 0,
            "source": source,
            "source_sha256": "a" * 64,
            "log_tail": stdout,
            "log_sha256": inventory._sha256(stdout),
        }

    def test_membership_forge_json_requires_one_exact_success(self):
        test = "test_ce_membership_deadbeef"
        stdout = json.dumps({
            "test/Exact.t.sol:ExactTest": {
                "test_results": {test + "()": {"status": "Success"}},
                "warnings": [],
            }
        })
        self.assertTrue(
            inventory._membership_forge_json_audit(self._run(stdout), test, "a" * 64))

    def test_membership_forge_json_rejects_zero_or_extra_matches(self):
        test = "test_ce_membership_deadbeef"
        empty = json.dumps({})
        extra = json.dumps({
            "test/Exact.t.sol:ExactTest": {
                "test_results": {
                    test + "()": {"status": "Success"},
                    "test_other()": {"status": "Success"},
                },
                "warnings": [],
            }
        })
        self.assertFalse(
            inventory._membership_forge_json_audit(self._run(empty), test, "a" * 64))
        self.assertFalse(
            inventory._membership_forge_json_audit(self._run(extra), test, "a" * 64))

    def test_membership_rejects_changed_anchor_body(self):
        replay = ("", "{ call(); assertTrue(ok); }")
        self.assertTrue(inventory._membership_replay_anchor_matches(replay, replay))
        self.assertFalse(inventory._membership_replay_anchor_matches(
            replay, ("", "{ assertTrue(true); }")))

    def test_membership_rejects_self_reported_fixed_point(self):
        sender = {"kind": "test-contract-address", "lower_bound": 1, "upper_bound": 9}
        environment = {"msg.value": 7, "msg.sender": sender}
        self.assertTrue(inventory._membership_environment_matches(environment, 7, sender))
        self.assertFalse(inventory._membership_environment_matches(environment, 1, sender))

    def test_membership_rejects_cross_contract_flat_source(self):
        inputs = [{"flat_sha256": "a" * 64}]
        self.assertTrue(inventory._membership_flat_binding(
            "a" * 64, inputs, "a" * 64, "a" * 64))
        self.assertFalse(inventory._membership_flat_binding(
            "a" * 64, inputs, "b" * 64, "a" * 64))

    def test_membership_rejects_self_reported_fair_wall(self):
        self.assertTrue(inventory._membership_wall_binding(599.5, 599.5))
        self.assertFalse(inventory._membership_wall_binding(1, 601))
        self.assertFalse(inventory._membership_wall_binding(599, 600))
        self.assertFalse(inventory._membership_wall_binding(True, True))
        self.assertFalse(inventory._membership_wall_binding(-1, -1))
        self.assertFalse(inventory._membership_wall_binding(float("nan"), float("nan")))


class FinalTestInventoryTests(unittest.TestCase):

    def test_source_grounded_constructor_rejects_identity_and_region_drift(self):
        flat_sha, proof = next(iter(inventory._SOURCE_GROUNDED_CONSTRUCTOR_PROOFS.items()))
        row = {
            "contract": proof["contract"],
            "unit": "__deploy__",
            "enc": 0,
            "test": proof["put_test"],
            "region": proof["region"],
            "source_proof": {"flat_source_sha256": flat_sha, "oracle_classes": ["R0"]},
        }
        metadata = {
            "status": "embedded",
            "identity": proof["identity"],
            "test": proof["anchor_test"],
            "basis_test": proof["basis_test"],
        }
        wrong_identity = tuple([*proof["identity"][:1], "constructor:drift",
                                *proof["identity"][2:]])
        self.assertEqual(
            inventory._source_grounded_constructor_anchor_audit(
                row, metadata, wrong_identity),
            (False, "constructor-identity-or-region-mismatch"))
        row["region"] = {"a": ["1", "1"]}
        self.assertEqual(
            inventory._source_grounded_constructor_anchor_audit(
                row, metadata, tuple(proof["identity"])),
            (False, "constructor-identity-or-region-mismatch"))

    def test_source_grounded_constructor_proof_table_seals_source_and_bodies(self):
        self.assertEqual(len(inventory._SOURCE_GROUNDED_CONSTRUCTOR_PROOFS), 3)
        for flat_sha, proof in inventory._SOURCE_GROUNDED_CONSTRUCTOR_PROOFS.items():
            self.assertTrue(inventory._is_sha256(flat_sha))
            for field in ("source_sha256", "put_function_sha256", "put_body_sha256",
                          "anchor_function_sha256", "anchor_body_sha256",
                          "basis_source_sha256", "basis_record_sha256",
                          "concrete_ce_sha256", "evidence_sha256"):
                self.assertTrue(inventory._is_sha256(proof[field]), field)
            self.assertTrue(Path(proof["basis_source"]).is_absolute())
            self.assertTrue(Path(proof["basis_record"]).is_absolute())
            self.assertEqual(len(proof["identity"]), 5)

    def test_source_grounded_constructor_rejects_substituted_rq3_basis(self):
        flat_sha, proof = next(iter(inventory._SOURCE_GROUNDED_CONSTRUCTOR_PROOFS.items()))
        row = {
            "contract": proof["contract"],
            "unit": "__deploy__",
            "enc": 0,
            "test": proof["put_test"],
            "region": proof["region"],
            "kind": "put",
            "stats": {"fuzz_params": 1, "oracle_classes": ["R0"]},
            "materialization": {"is_put": True},
            "source_proof": {"flat_source_sha256": flat_sha, "oracle_classes": ["R0"]},
        }
        metadata = {
            "status": "embedded",
            "identity": proof["identity"],
            "test": proof["anchor_test"],
            "basis_test": proof["basis_test"],
            "basis_source": proof["basis_source"],
            "basis_source_sha256": proof["basis_source_sha256"],
            "basis_record": proof["basis_record"],
            "basis_record_sha256": proof["basis_record_sha256"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            substitute = Path(temporary) / "substitute.t.sol"
            substitute.write_bytes(Path(proof["basis_source"]).read_bytes())
            metadata["basis_source"] = str(substitute)
            self.assertEqual(
                inventory._source_grounded_constructor_anchor_audit(
                    row, metadata, tuple(proof["identity"])),
                (False, "constructor-basis-mismatch"))

    def test_structural_projection_binding_is_exact_and_tamper_evident(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "basis.t.sol"
            source.write_text("contract T {}\n", encoding="utf-8")
            ce_sha = "a" * 64
            projection = {
                "schema": "veriput-certified-ce-source-projection/v1",
                "ce_sha256": ce_sha,
                "coordinate_binding": {
                    "schema": "veriput-certified-ce-source-binding/v1",
                    "ce_sha256": ce_sha,
                    "coordinates": {
                        "msg.value": {
                            "kind": "call-environment-literal",
                            "certified": 1,
                            "rendered": 1,
                            "source": "{value: 1}",
                        }
                    },
                },
            }
            document = {
                "file": str(source),
                "path_function": "pf",
                "unit": "f",
                "enc": 2,
                "piece": None,
                "certification_source": "structural-abi-gate-no-coordinate",
                "certified_ce_binding": {
                    "status": "exact",
                    "projection_certificate": "abi-value-gate-before-body/v1",
                    "rendered_source_verified": True,
                    "ce_sha256": ce_sha,
                    "rendered_source_ce_sha256": ce_sha,
                    "source_projection_preserved": projection,
                },
            }
            put_json = root / "basis.json"
            rendered = json.dumps(document, indent=2, sort_keys=True) + "\n"
            put_json.write_text(rendered, encoding="utf-8")
            binding = {
                "kind": "structural-abi-gate-certified-projection",
                "certification_source": "structural-abi-gate-no-coordinate",
                "claim_exit_kind": "revert",
                "claim_return_value": None,
                "basis_put_json_path": put_json.name,
                "basis_put_json_sha256": inventory._sha256(rendered),
                "source_projection_sha256": inventory._sha256(
                    json.dumps(projection, sort_keys=True, separators=(",", ":"))),
            }
            identity = ("suite/subject", "pf", "f", "2", "")
            self.assertTrue(
                inventory._report_binding_audit(root, binding, identity,
                                                inventory._sha256(source.read_text()), ce_sha))
            document["certified_ce_binding"]["source_projection_preserved"][
                "coordinate_binding"]["coordinates"]["msg.value"]["rendered"] = 2
            put_json.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
            self.assertFalse(
                inventory._report_binding_audit(root, binding, identity,
                                                inventory._sha256(source.read_text()), ce_sha))

    def test_setup_is_scoped_to_selected_put_contract(self):
        source = ("contract Helper { function setUp() public { helper = 1; } }\n"
                  "contract Selected {\n"
                  "  function setUp() public { selected = 2; }\n"
                  "  function test_put_selected() public {}\n"
                  "}\n")
        function = inventory._scoped_solidity_function(source, "test_put_selected", "setUp")
        self.assertIsNotNone(function)
        self.assertIn("selected = 2", function[1])
        self.assertNotIn("helper", function[1])

    @staticmethod
    def _reseal_evidence(row: dict) -> None:
        metadata = row["ce_anchor"]
        evidence = {
            "schema": "veriput-certified-ce-anchor-evidence/v1",
            "identity": metadata["identity"],
            "certification_record_sha256": metadata["certification_record_sha256"],
            "certified_ce_sha256": metadata["certified_ce_sha256"],
            "basis_source_sha256": metadata["basis_source_sha256"],
            "basis_setup_sha256": metadata["basis_setup_sha256"],
            "basis_test_body_sha256": metadata["basis_test_body_sha256"],
            "oracles": metadata["oracles"],
            "report_binding": metadata["report_binding"],
        }
        metadata["evidence_sha256"] = inventory._sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")))

    def _put_row(self, root: Path, name: str, enc: str, *, confirmed: bool) -> dict:
        anchor = "test_ce_anchor_0123456789abcdef"
        source = ("contract T {\n"
                  "  function setUp() public { target = target; }\n"
                  f"  function test_put_{name}(uint256 x) public {{ assert(x == x); }}\n"
                  f"  function {anchor}() public {{ uint256 observed = "
                  f"target.{name}(7); assertEq(observed, 7); }}\n"
                  "}\n")
        source_path = root / f"{name}.t.sol"
        source_path.write_text(source, encoding="utf-8")
        row = {
            "path_function": f"sol:@F@{name}",
            "unit": name,
            "enc": enc,
            "piece": "",
            "file": str(source_path),
            "test": f"test_put_{name}",
        }
        if not confirmed:
            return row
        function = inventory._solidity_function(source, anchor)
        self.assertIsNotNone(function)
        identity = ["bench/subject", row["path_function"], name, enc, ""]
        ce = {"x": "7"}
        ce_sha = inventory.certified_ce_sha256(ce)
        cert_record = {
            "unit": name,
            "path_function": row["path_function"],
            "stage2_observed_certified_details": {
                enc: {
                    "piece": None,
                    "ce": ce,
                    "verdict": "CERTIFIED",
                }
            },
            "certified": {
                enc: "region"
            },
        }
        cert_dir = root / "cert"
        cert_dir.mkdir(exist_ok=True)
        cert_path = cert_dir / "certify-results.jsonl"
        prior = cert_path.read_text(encoding="utf-8") if cert_path.is_file() else ""
        cert_path.write_text(prior + json.dumps(cert_record) + "\n", encoding="utf-8")
        cert_sha = inventory._sha256(json.dumps(cert_record, sort_keys=True, separators=(",", ":")))
        oracles = [{
            "class": "concrete-value",
            "kind": "return-value",
            "observed": "observed",
            "expected": "7",
            "solidity_type": "uint256",
            "target_receiver": "target",
            "assertion": "assertEq(observed, 7);",
            "provenance": "stage2-witness",
        }]
        claim = {
            "path_function": row["path_function"],
            "path_id": enc,
            "exit_kind": "normal",
            "return_value": "7",
            "foundry_testcase_fingerprint_sha256": "9" * 64,
        }
        report_path = root / f"{name}-cov-report.json"
        report_text = json.dumps({"claims": [claim]}, indent=2, sort_keys=True) + "\n"
        report_path.write_text(report_text, encoding="utf-8")
        report_binding = {
            "cov_report_path": report_path.name,
            "cov_report_sha256": inventory._sha256(report_text),
            "claim_sha256":
            inventory._sha256(json.dumps(claim, sort_keys=True, separators=(",", ":"))),
            "claim_exit_kind": "normal",
            "claim_return_value": "7",
            "solver_witness_fingerprint_sha256": "9" * 64,
        }
        evidence = {
            "schema": "veriput-certified-ce-anchor-evidence/v1",
            "identity": identity,
            "certification_record_sha256": cert_sha,
            "certified_ce_sha256": ce_sha,
            "basis_source_sha256": "3" * 64,
            "basis_setup_sha256":
            inventory._sha256(inventory._solidity_function(source, "setUp")[1]),
            "basis_test_body_sha256": "5" * 64,
            "oracles": oracles,
            "report_binding": report_binding,
        }
        anchor_span, _reason = inventory._solidity_test_span(source, anchor)
        put_function = inventory._solidity_function(source, row["test"])
        setup_function = inventory._solidity_function(source, "setUp")
        metadata = {
            "status":
            "embedded",
            "binding":
            "certified-exact-basis/v1",
            "test":
            anchor,
            "identity":
            identity,
            "evidence_sha256":
            inventory._sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":"))),
            "certification_record_sha256":
            evidence["certification_record_sha256"],
            "certified_ce_sha256":
            evidence["certified_ce_sha256"],
            "basis_source_sha256":
            evidence["basis_source_sha256"],
            "basis_setup_sha256":
            evidence["basis_setup_sha256"],
            "basis_test_body_sha256":
            evidence["basis_test_body_sha256"],
            "report_binding":
            report_binding,
            "destination": {
                "anchor_body_sha256": inventory._sha256(function[1]),
                "anchor_function_sha256": inventory._sha256(source[anchor_span[0]:anchor_span[1]]),
                "source_after_sha256": inventory._sha256(source),
                "put_body_before_sha256": inventory._sha256(put_function[1]),
                "put_body_after_sha256": inventory._sha256(put_function[1]),
                "setup_body_sha256": inventory._sha256(setup_function[1]),
            },
            "oracles":
            oracles,
        }
        put_json = root / f"{name}.put.json"
        runs = {}
        for role, test in (("put", row["test"]), ("anchor", anchor)):
            stdout = json.dumps(
                {f"{source_path.name}:T": {
                    "test_results": {
                        f"{test}()": {
                            "status": "Success"
                        }
                    }
                }})
            command = [
                "forge", "test", "--json", "--match-path", source_path.name, "--match-test",
                rf"^{test}(\(|$)"
            ]
            if role == "put":
                command += ["--fuzz-runs", "256"]
            record = {
                "schema": "veriput-exact-forge-run/v1",
                "command": command,
                "project": str(root.resolve()),
                "source": source_path.name,
                "source_sha256": inventory._sha256(source),
                "test": test,
                "returncode": 0,
                "stdout": stdout,
                "stderr": "",
            }
            record_text = json.dumps(record, indent=2, sort_keys=True) + "\n"
            record_path = root / f"{name}-{role}.json"
            record_path.write_text(record_text, encoding="utf-8")
            runs[f"{role}_run"] = {
                "record_path": record_path.name,
                "record_sha256": inventory._sha256(record_text),
            }
        metadata["forge_gate"] = {
            "schema": "veriput-put-anchor-forge-gate/v1",
            "put_test": row["test"],
            "anchor_test": anchor,
            "put_status": "Success",
            "anchor_status": "Success",
            "source_sha256": inventory._sha256(source),
            **runs,
        }
        row["ce_anchor"] = metadata
        put_json.write_text(json.dumps({"ce_anchor": metadata}), encoding="utf-8")
        row["put_json"] = str(put_json)
        return row

    def test_strength_audit_requires_source_bound_double_green(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row = self._put_row(root, "good", "1", confirmed=True)
            self.assertEqual(inventory._anchor_strength_audit(row, subject_dir=root),
                             (True, "strength-confirmed"))

            row["ce_anchor"]["forge_gate"]["anchor_status"] = "Failure"
            self.assertEqual(
                inventory._anchor_strength_audit(row, subject_dir=root)[1],
                "missing-or-stale-double-forge-gate")

    def test_strength_audit_rejects_corrupt_body_and_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corrupt_body = self._put_row(root, "body", "1", confirmed=True)
            path = Path(corrupt_body["file"])
            path.write_text(path.read_text(encoding="utf-8").replace("assertEq(observed, 7);",
                                                                     "assertEq(observed, 8);"),
                            encoding="utf-8")
            self.assertFalse(inventory._anchor_strength_audit(corrupt_body, subject_dir=root)[0])

            corrupt_provenance = self._put_row(root, "provenance", "2", confirmed=True)
            corrupt_provenance["ce_anchor"]["evidence_sha256"] = "not-a-hash"
            self.assertEqual(
                inventory._anchor_strength_audit(corrupt_provenance, subject_dir=root)[1],
                "incomplete-anchor-provenance")

    def test_forged_success_without_exact_log_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row = self._put_row(root, "nolog", "1", confirmed=True)
            run = row["ce_anchor"]["forge_gate"]["anchor_run"]
            (Path(row["put_json"]).parent / run["record_path"]).unlink()
            self.assertEqual(
                inventory._anchor_strength_audit(row, subject_dir=root)[1],
                "invalid-double-forge-evidence")

    def test_red_forge_log_cannot_be_hidden_by_green_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row = self._put_row(root, "redlog", "1", confirmed=True)
            binding = row["ce_anchor"]["forge_gate"]["put_run"]
            record_path = Path(row["put_json"]).parent / binding["record_path"]
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["stdout"] = record["stdout"].replace("Success", "Failure")
            rendered = json.dumps(record, indent=2, sort_keys=True) + "\n"
            record_path.write_text(rendered, encoding="utf-8")
            binding["record_sha256"] = inventory._sha256(rendered)
            Path(row["put_json"]).write_text(json.dumps({"ce_anchor": row["ce_anchor"]}),
                                             encoding="utf-8")
            self.assertEqual(
                inventory._anchor_strength_audit(row, subject_dir=root)[1],
                "invalid-double-forge-evidence")

    def test_evidence_identity_and_hash_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row = self._put_row(root, "tamper", "1", confirmed=True)
            identity = tuple(row["ce_anchor"]["identity"])
            row["ce_anchor"]["identity"][3] = "99"
            self.assertEqual(
                inventory._anchor_strength_audit(row, identity, root)[1],
                "anchor-identity-mismatch")

            row = self._put_row(root, "hash", "2", confirmed=True)
            row["ce_anchor"]["report_binding"]["claim_return_value"] = "8"
            self.assertEqual(
                inventory._anchor_strength_audit(row, subject_dir=root)[1],
                "report-binding-mismatch")

    def test_cross_identity_cert_and_claim_swaps_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self._put_row(root, "first", "1", confirmed=True)
            second = self._put_row(root, "second", "2", confirmed=True)

            cert_swap = copy.deepcopy(first)
            cert_swap["ce_anchor"]["certification_record_sha256"] = (
                second["ce_anchor"]["certification_record_sha256"])
            self._reseal_evidence(cert_swap)
            self.assertEqual(
                inventory._anchor_strength_audit(cert_swap, subject_dir=root)[1],
                "certification-record-mismatch")

            claim_swap = copy.deepcopy(first)
            claim_swap["ce_anchor"]["report_binding"] = copy.deepcopy(
                second["ce_anchor"]["report_binding"])
            self._reseal_evidence(claim_swap)
            self.assertEqual(
                inventory._anchor_strength_audit(claim_swap, subject_dir=root)[1],
                "report-binding-mismatch")

    def test_noncertified_detail_cannot_supply_certification_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row = self._put_row(root, "notcert", "1", confirmed=True)
            cert_path = root / "cert" / "certify-results.jsonl"
            records = [json.loads(line) for line in cert_path.read_text().splitlines()]
            record = records[-1]
            record["stage2_observed_certified_details"]["1"]["verdict"] = "NOT-CERTIFIED"
            rendered = json.dumps(record, sort_keys=True, separators=(",", ":"))
            cert_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            metadata = row["ce_anchor"]
            metadata["certification_record_sha256"] = inventory._sha256(rendered)
            self._reseal_evidence(row)
            self.assertEqual(
                inventory._anchor_strength_audit(row, subject_dir=root)[1],
                "certification-record-mismatch")

    def test_obligation_grain_is_identity_not_artifact_row(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            confirmed = self._put_row(root, "confirmed", "1", confirmed=True)
            duplicate = copy.deepcopy(confirmed)
            duplicate["ce_anchor"]["forge_gate"]["anchor_status"] = "Failure"
            unresolved = self._put_row(root, "unresolved", "2", confirmed=False)
            red = self._put_row(root, "red", "4", confirmed=True)
            red["ce_anchor"]["forge_gate"]["put_status"] = "Failure"
            concrete_file = root / "concrete.t.sol"
            concrete_file.write_text(
                "contract T { function test_concrete() public { assert(true); } }\n",
                encoding="utf-8")
            concrete = {
                "id": "concrete-key",
                "path_function": "sol:@F@concrete",
                "unit": "concrete",
                "enc": "3",
                "piece": "",
                "file": str(concrete_file),
                "test": "test_concrete",
            }
            rows = [confirmed, duplicate, unresolved, red, concrete]
            patches = (
                mock.patch.object(inventory, "_case_dirs", return_value=[("bench/subject", root)]),
                mock.patch.object(inventory, "_strict_valid_tests", return_value=rows),
                mock.patch.object(inventory,
                                  "load_manifest",
                                  return_value={"entries": [{
                                      "id": "concrete-key"
                                  }]}),
                mock.patch.object(inventory,
                                  "_entry_is_currently_not_generalized",
                                  return_value=True),
                mock.patch.object(inventory, "audit_manifest", return_value=[]),
                mock.patch.object(inventory,
                                  "_concrete_test_key",
                                  side_effect=lambda row: row["id"]),
                mock.patch.object(inventory,
                                  "_entry_test_keys",
                                  side_effect=lambda entry: {entry["id"]}),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], \
                    patches[5], patches[6]:
                generalized, unresolved_strength, not_generalized = inventory.obligations(root)

            self.assertEqual(len(generalized), 1)
            self.assertEqual(len(unresolved_strength), 2)
            self.assertEqual(len(not_generalized), 1)
            self.assertEqual(len(generalized | unresolved_strength | not_generalized), 4)

    def test_frozen_missing_identity_becomes_unresolved(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "ledger.json"
            visible = ("case", "path", "unit", "1", "")
            corrupt = ("case", "path", "unit", "2", "")
            inventory.freeze_ledger(ledger, {visible, corrupt})
            generalized = {visible}
            unresolved_strength = set()
            not_generalized = set()
            inventory.reconcile_ledger(ledger, generalized, unresolved_strength, not_generalized)
            self.assertEqual(unresolved_strength, {corrupt})


if __name__ == "__main__":
    unittest.main()
