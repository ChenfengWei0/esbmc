#!/usr/bin/env python3
"""Tests for fail-closed Fair600 membership projection."""

import unittest
from pathlib import Path
import tempfile

from rq1_membership_projection import (Refusal, _empty_function_body, _fixed_environment,
                                       _function_with_body_prefix, _membership, _run_forge)
from rq1_membership_projection import (_exact_prefunding, _require_plain_public_function,
                                       _setup_target_deployment)


class MembershipProjectionTest(unittest.TestCase):
    """Exercise accepted fixed coordinates and important refusal boundaries."""

    def test_literal_value_default_sender_is_member(self):
        """A fixed nonzero value and the test contract sender fit full domains."""
        function = """function test_cov_1() public {
          (bool ok, ) = address(c0).call{value: 1}(abi.encodeWithSignature("f()"));
          assertFalse(ok);
        }"""
        env = _fixed_environment(function, "f", {
            "kind": "call-status",
            "expected": False,
            "observed": "ok",
            "target_receiver": "c0",
        })
        result = _membership(
            {
                "msg.value": ["1", str(2**256 - 1)],
                "msg.sender": ["1", str(2**160 - 1)],
            }, {}, env)
        self.assertEqual(result["verdict"], "MEMBER")
        self.assertEqual(result["coordinates"]["msg.value"]["fixed"], 1)

    def test_outside_region_refused(self):
        """A fixed value outside the certified interval is never projected."""
        env = {"msg.value": 0, "msg.sender": {"kind": "literal", "value": 1}}
        with self.assertRaisesRegex(Refusal, "msg.value"):
            _membership({"msg.value": ["1", "2"], "msg.sender": ["1", "2"]}, {}, env)

    def test_holes_refused(self):
        """Unimplemented hole membership is refused instead of approximated."""
        env = {"msg.value": 1, "msg.sender": {"kind": "literal", "value": 1}}
        with self.assertRaisesRegex(Refusal, "holes"):
            _membership({
                "msg.value": ["1", "2"],
                "msg.sender": ["1", "2"]
            }, {"msg.value": [1]}, env)

    def test_dynamic_value_refused(self):
        """Dynamic replay values are not guessed by the literal extractor."""
        with self.assertRaisesRegex(Refusal, "literal"):
            _fixed_environment(
                "function f(uint x) public { (bool ok,) = address(c0).call{value: x}(hex\"\"); }",
                "f", {
                    "kind": "call-status",
                    "expected": False,
                    "observed": "ok",
                    "target_receiver": "c0",
                })

    def test_decoy_string_does_not_supply_value(self):
        """A string decoy cannot turn a dynamic target value into a member."""
        source = """function f(uint x) public {
          string memory decoy = ".call{value:1}";
          (bool ok,) = address(c0).call{value:x}(abi.encodeWithSignature("f()"));
          assertFalse(ok, "must fail");
        }"""
        with self.assertRaisesRegex(Refusal, "literal"):
            _fixed_environment(source, "f", {
                "kind": "call-status",
                "expected": False,
                "observed": "ok",
                "target_receiver": "c0"
            })

    def test_unrelated_literal_call_is_refused(self):
        """A literal on another receiver cannot establish target membership."""
        source = """function f(uint x) public {
          (bool decoy,) = address(c1).call{value:1}(hex"");
          (bool ok,) = address(c0).call{value:x}(abi.encodeWithSignature("f()"));
          assertFalse(ok, "must fail");
        }"""
        with self.assertRaisesRegex(Refusal, "literal"):
            _fixed_environment(source, "f", {
                "kind": "call-status",
                "expected": False,
                "observed": "ok",
                "target_receiver": "c0"
            })

    def test_forge_zero_match_is_failure(self):
        """Foundry rc=0 with no matching test cannot satisfy the exact gate."""
        with tempfile.TemporaryDirectory(prefix="membership-forge-test-") as scratch:
            project = Path(scratch)
            (project / "test").mkdir()
            (project / "foundry.toml").write_text('[profile.default]\ntest = "test"\n',
                                                  encoding="utf-8")
            source = project / "test" / "Exact.t.sol"
            source.write_text(
                "pragma solidity >=0.8.0; contract ExactTest { function test_real() public {} }\n",
                encoding="utf-8")
            self.assertEqual(_run_forge(project, source, "test_real")["status"], "Success")
            self.assertEqual(_run_forge(project, source, "test_absent")["status"], "Failure")

    def test_setup_bodies_can_be_materialized_per_test(self):
        """The validation transform keeps PUT and replay setup bodies disjoint."""
        setup = "function setUp() public { c0 = new C(1); }"
        test = "function test_put(uint x) public { assertGt(x, 0); }"
        self.assertEqual(_empty_function_body(setup), "function setUp() public {\n  }")
        materialized = _function_with_body_prefix(test, " c0 = new C(2); ")
        self.assertIn("{ c0 = new C(2);  assertGt", materialized)

    def test_setup_materialization_rejects_modifier_or_early_return(self):
        """Inlining cannot cross modifier or function-return boundaries."""
        with self.assertRaisesRegex(Refusal, "unmodified public"):
            _require_plain_public_function(
                "function test_put() public onlyOwner { assertTrue(true); }", "test_put")
        with self.assertRaisesRegex(Refusal, "control transfer"):
            _setup_target_deployment("c0 = new C(); return;", "c0", "C")

    def test_setup_receiver_must_deploy_certified_contract(self):
        """A false call to an unrelated receiver cannot borrow the ABI-gate proof."""
        with self.assertRaisesRegex(Refusal, "certified contract"):
            _setup_target_deployment("c0 = new Other();", "c0", "C")
        with self.assertRaisesRegex(Refusal, "certified contract"):
            _setup_target_deployment("holder.c0 = new C();", "c0", "C")
        with self.assertRaisesRegex(Refusal, "outside its unique deployment"):
            _setup_target_deployment("c0 = new C(); vm.etch(address(c0), hex\"00\");", "c0", "C")
        with self.assertRaisesRegex(Refusal, "non-whitelisted"):
            _setup_target_deployment("c0 = new C(); poison();", "c0", "C")
        with self.assertRaisesRegex(Refusal, "non-literal constructor"):
            _setup_target_deployment("c0 = new C(poison());", "c0", "C")
        with self.assertRaisesRegex(Refusal, "control transfer"):
            _setup_target_deployment("c0 = new C(); vm.startHoax(address(0));", "c0", "C")
        with self.assertRaisesRegex(Refusal, "outside its unique deployment"):
            _setup_target_deployment("$c0 = new C(); delete $c0;", "$c0", "C")

    def test_replay_funding_must_precede_the_target_call(self):
        """Insufficient-balance failure cannot masquerade as the certified value gate."""
        valid = ("function test_ce() public { vm.deal(address(this), 1); "
                 "(bool ok,) = address(c0).call{value: 1}(hex\"\"); assertFalse(ok); }")
        self.assertEqual(_exact_prefunding(valid, 1)["value"], 1)
        invalid = ("function test_ce() public { (bool ok,) = address(c0).call{value: 1}(hex\"\"); "
                   "vm.deal(address(this), 1); assertFalse(ok); }")
        with self.assertRaisesRegex(Refusal, "pre-fund"):
            _exact_prefunding(invalid, 1)

    def test_forge_duplicate_test_name_is_failure(self):
        """Two contracts in one suite cannot collapse into one accepted JSON result."""
        with tempfile.TemporaryDirectory(prefix="membership-forge-duplicate-") as scratch:
            project = Path(scratch)
            (project / "test").mkdir()
            (project / "foundry.toml").write_text('[profile.default]\ntest = "test"\n',
                                                  encoding="utf-8")
            source = project / "test" / "Duplicate.t.sol"
            source.write_text(
                "pragma solidity >=0.8.0; contract A { function test_same() public {} } "
                "contract B { function test_same() public {} }\n",
                encoding="utf-8")
            self.assertEqual(_run_forge(project, source, "test_same")["status"], "Failure")


if __name__ == "__main__":
    unittest.main()
