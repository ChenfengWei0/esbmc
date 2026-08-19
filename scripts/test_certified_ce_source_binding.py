#!/usr/bin/env python3
"""Focused negative tests for complete certified-CE source binding."""

import hashlib
import json
import unittest

from solidity_path_put import (ABI_VALUE_GATE_PROJECTION, CONSTRUCTOR_SETUP_PROJECTION,
                               abi_value_gate_ce_projection, bind_emitted_claim_to_certified_ce,
                               bind_emitted_source_to_certified_ce)


def _gate_body(unit, args="", value=1, sender=1):
    signature = unit + ("(uint256)" if args else "()")
    encoded_args = (", " + args) if args else ""
    return [
        f"    vm.prank(address(uint160({sender})));",
        (f"    (bool ok, ) = address(c0).call{{value: uint256({value})}}("
         f'abi.encodeWithSignature("{signature}"{encoded_args}));'),
        "    assertFalse(ok);",
    ]


class CertifiedCeSourceBindingTests(unittest.TestCase):

    def test_wst_full_ce_is_projected_coordinate_by_coordinate(self):
        ce = {
            "msg.sender": "0",
            "msg.value": "1",
            "msg.data": "0",
            "msg.sig": "0",
            "block.timestamp": "0xffff",
            "tx.origin": "0",
            "state.decimals": "255",
            "state.privateFeed": "1",
            "state.immutableScale": "1",
        }
        body = _gate_body("latestRoundData")
        evidence = abi_value_gate_ce_projection(ce, [], stage4_kind="abi-value-gate")
        audit = {}
        digest, error = bind_emitted_source_to_certified_ce(body,
                                                            1,
                                                            "latestRoundData", [],
                                                            ce,
                                                            coordinate_evidence=evidence,
                                                            audit=audit)
        expected_digest = hashlib.sha256(
            json.dumps({
                name: int(value, 0)
                for name, value in ce.items()
            },
                       sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest()
        self.assertIsNone(error)
        self.assertEqual(digest, expected_digest)
        self.assertEqual(audit["ce_sha256"], digest)
        self.assertEqual(
            audit["coordinates"]["msg.sender"], {
                "kind": "path-irrelevant",
                "certificate": ABI_VALUE_GATE_PROJECTION,
                "certified": 0,
                "rendered": 1,
            })
        self.assertEqual(audit["coordinates"]["msg.data"]["kind"], "calldata-determined")
        self.assertEqual(audit["coordinates"]["state.privateFeed"]["kind"], "path-irrelevant")

    def test_bob_and_lido_gate_arguments_remain_exact(self):
        for unit, state_name in (("getExpiry", "state._expiry"), ("setYieldFee",
                                                                  "state._yieldFee")):
            with self.subTest(unit=unit):
                ce = {
                    "amount": "7",
                    "msg.sender": "9",
                    "msg.value": "1",
                    "msg.data": "0",
                    "msg.sig": "0",
                    state_name: ["1", "2"],
                }
                body = _gate_body(unit, "uint256(7)", sender=9)
                evidence = abi_value_gate_ce_projection(ce, [("amount", "uint256")],
                                                        stage4_kind="abi-value-gate")
                digest, error = bind_emitted_source_to_certified_ce(body,
                                                                    1,
                                                                    unit, [("amount", "uint256")],
                                                                    ce,
                                                                    coordinate_evidence=evidence)
                self.assertIsNone(error)
                self.assertIsNotNone(digest)
                bad_body = _gate_body(unit, "uint256(8)", sender=9)
                digest, error = bind_emitted_source_to_certified_ce(bad_body,
                                                                    1,
                                                                    unit, [("amount", "uint256")],
                                                                    ce,
                                                                    coordinate_evidence=evidence)
                self.assertIsNone(digest)
                self.assertIn("amount", error)

    def test_projection_never_hides_msg_value_mismatch(self):
        ce = {"msg.sender": "1", "msg.value": "2", "state.x": "3"}
        evidence = abi_value_gate_ce_projection(ce, [], stage4_kind="abi-value-gate")
        evidence["msg.value"] = {
            "kind": "path-irrelevant",
            "certificate": ABI_VALUE_GATE_PROJECTION,
        }
        digest, error = bind_emitted_source_to_certified_ce(_gate_body("f", value=1),
                                                            1,
                                                            "f", [],
                                                            ce,
                                                            coordinate_evidence=evidence)
        self.assertIsNone(digest)
        self.assertIn("msg.value governs", error)

    def test_unproved_or_forged_projection_fails_closed(self):
        ce = {"msg.sender": "1", "msg.value": "1", "state.x": "3"}
        body = _gate_body("f")
        digest, error = bind_emitted_source_to_certified_ce(body, 1, "f", [], ce)
        self.assertIsNone(digest)
        self.assertIn("state.x", error)
        forged = {"state.x": {"kind": "path-irrelevant", "certificate": "trust-me"}}
        digest, error = bind_emitted_source_to_certified_ce(body,
                                                            1,
                                                            "f", [],
                                                            ce,
                                                            coordinate_evidence=forged)
        self.assertIsNone(digest)
        self.assertIn("unsupported projection certificate", error)

    def test_non_gate_stage4_kind_cannot_authorize_projection(self):
        ce = {"msg.sender": "1", "msg.value": "1", "state.x": "3"}
        self.assertIsNone(abi_value_gate_ce_projection(ce, [], stage4_kind="certified-region"))

    def test_explicit_foundry_block_and_tx_setters_bind_without_projection(self):
        body = [
            "    vm.warp(uint256(11));",
            "    vm.roll(uint256(12));",
            "    vm.chainId(uint256(13));",
            "    vm.fee(uint256(14));",
            "    vm.prevrandao(uint256(15));",
            "    vm.blobBaseFee(uint256(16));",
            "    vm.difficulty(uint256(17));",
            "    vm.txGasPrice(uint256(18));",
            "    vm.coinbase(address(uint160(19)));",
            "    vm.prank(address(uint160(20)), address(uint160(21)));",
            "    c0.f();",
        ]
        ce = {
            "block.timestamp": "11",
            "block.number": "12",
            "block.chainid": "13",
            "block.basefee": "14",
            "block.prevrandao": "15",
            "block.blobbasefee": "16",
            "block.difficulty": "17",
            "tx.gasprice": "18",
            "block.coinbase": "19",
            "msg.sender": "20",
            "tx.origin": "21",
            "msg.value": "0",
        }
        audit = {}
        digest, error = bind_emitted_source_to_certified_ce(body, 10, "f", [], ce, audit=audit)
        self.assertIsNone(error)
        self.assertIsNotNone(digest)
        for name in ce:
            self.assertEqual(audit["coordinates"][name]["kind"], "call-environment-literal")

    def test_unset_environment_and_bool_prank_overload_fail_closed(self):
        body = [
            "    vm.prank(address(uint160(20)), true);",
            "    c0.f();",
        ]
        ce = {
            "msg.sender": "20",
            "msg.value": "0",
            "tx.origin": "1",
            "block.gaslimit": "30",
        }
        digest, error = bind_emitted_source_to_certified_ce(body, 1, "f", [], ce)
        self.assertIsNone(digest)
        self.assertTrue("block.gaslimit" in error or "tx.origin" in error)

    def test_constructor_setup_binding_requires_exact_value_and_source_digest(self):
        ce = {"msg.sender": "1", "msg.value": "1", "state.immutableScale": "7"}
        body = _gate_body("f")
        exact = {
            "state.immutableScale": {
                "kind": "constructor-or-setup",
                "certificate": CONSTRUCTOR_SETUP_PROJECTION,
                "value": "7",
                "source_sha256": "a" * 64,
            }
        }
        digest, error = bind_emitted_source_to_certified_ce(body,
                                                            1,
                                                            "f", [],
                                                            ce,
                                                            coordinate_evidence=exact,
                                                            setup_source_sha256="a" * 64)
        self.assertIsNone(error)
        self.assertIsNotNone(digest)
        exact["state.immutableScale"]["value"] = "8"
        digest, error = bind_emitted_source_to_certified_ce(body,
                                                            1,
                                                            "f", [],
                                                            ce,
                                                            coordinate_evidence=exact,
                                                            setup_source_sha256="a" * 64)
        self.assertIsNone(digest)
        self.assertIn("exact CE value", error)

    def test_constructor_setup_binding_rejects_another_setup_source(self):
        ce = {"msg.sender": "1", "msg.value": "0", "state.x": "7"}
        evidence = {
            "state.x": {
                "kind": "constructor-or-setup",
                "certificate": CONSTRUCTOR_SETUP_PROJECTION,
                "value": "7",
                "source_sha256": "a" * 64,
            }
        }
        digest, error = bind_emitted_source_to_certified_ce(
            ["    vm.prank(address(uint160(1)));", "    c0.f();"],
            1,
            "f", [],
            ce,
            coordinate_evidence=evidence,
            setup_source_sha256="b" * 64)
        self.assertIsNone(digest)
        self.assertIn("not bound to this setup source", error)

    def test_complete_nested_claim_is_hashed_and_compared(self):
        claim = {
            "inputs": {},
            "env": {
                "msg.sender": "1",
                "msg.value": "1"
            },
            "entry_storage": {
                "records": ["1", {
                    "tag": "0x02"
                }]
            },
        }
        expected = {
            "msg.sender": "1",
            "msg.value": "1",
            "state.records": ["1", {
                "tag": "2"
            }],
        }
        binding, error = bind_emitted_claim_to_certified_ce(claim, expected)
        self.assertIsNone(error)
        self.assertEqual(binding["status"], "exact")
        expected["state.records"][1]["tag"] = "3"
        binding, error = bind_emitted_claim_to_certified_ce(claim, expected)
        self.assertIsNone(binding)
        self.assertIn("different=state.records", error)


if __name__ == "__main__":
    unittest.main()
