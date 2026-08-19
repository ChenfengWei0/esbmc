#!/usr/bin/env python3
"""Focused tests for strict C-history return ABI recovery."""

# pylint: disable=import-error,protected-access,wrong-import-position

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts"))

import rq1_anchor_c_return as recovery
from solidity_path_put import source_inherited_function_returns


class ReturnAbiRecoveryTest(unittest.TestCase):
    """Exercise receiver, overload, and ordered tuple recovery."""

    def test_constructor_receiver_and_overload_preserve_abi_order(self) -> None:
        """A constructor-local receiver selects the matching overload only."""
        replay = """
contract Replay {
  function test_cov_0() public {
    Derived c0 = new Derived();
    c0.read(uint256(7));
  }
}
"""
        receiver, arity, error = recovery._target_call(replay, "test_cov_0", "read")
        self.assertIsNone(error)
        self.assertEqual((receiver, arity), ("c0", 1))
        contract, error = recovery._receiver_type(replay, "c0")
        self.assertIsNone(error)
        self.assertEqual(contract, "Derived")

        flat = """
contract Base {
  function read() external returns (bool) {}
  function read(uint256 x) external returns (uint256 first, uint256 second, address who) {}
}
contract Derived is Base {}
"""
        returns = source_inherited_function_returns(flat, contract, "read", arity=arity)
        self.assertEqual(returns, [("", "uint256"), ("", "uint256"), ("", "address")])

    def test_local_named_import_is_resolved(self) -> None:
        """A named import alias resolves to its exact local source file."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "test" / "Replay.t.sol"
            flat_path = root / "src" / "flat.sol"
            source_path.parent.mkdir()
            flat_path.parent.mkdir()
            flat_path.write_text("contract Original {}\n", encoding="utf-8")
            source = 'import {Original as Derived} from "../src/flat.sol";\n'
            resolved, declared_contract, error = recovery._imported_flat(
                source_path, source, "Derived")
            self.assertIsNone(error)
            self.assertEqual(resolved, flat_path.resolve())
            self.assertEqual(declared_contract, "Original")

    def test_duplicate_tuple_types_receive_indexed_r0_oracles(self) -> None:
        """Equal ABI types remain distinct ordered return obligations."""
        source = """
contract Replay {
  function test_cov_0() public {
    c0.read(uint256(7));
  }
}
"""
        rendered, oracles, error = recovery.add_indexed_return_oracles(
            source, "test_cov_0", "read", [("", "uint256"), ("", "uint256")], "(1, 2)")
        self.assertIsNone(error)
        self.assertEqual([oracle["return_index"] for oracle in oracles], [0, 1])
        self.assertEqual([oracle["solidity_type"] for oracle in oracles],
                         ["uint256", "uint256"])
        self.assertIn("uint256 _veriput_concrete_return_0", rendered)
        self.assertIn("assertEq(_veriput_concrete_return_1, uint256(2));", rendered)

    def test_signed_scalar_uses_twos_complement_witness(self) -> None:
        """Unsigned solver bits are normalized before scalar Solidity rendering."""
        self.assertEqual(recovery._normalized_scalar_witness("int8", "255"), "-1")
        self.assertEqual(recovery._normalized_scalar_witness("int8", "127"), "127")

    def test_apply_runner_rejects_unsealed_inventory_before_writes(self) -> None:
        """The canonical runner fails closed before delegating a stale seal."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sealed = root / "ready.json"
            sealed.write_text(json.dumps({"counts": {"ready": 1}, "ready": [{}]}),
                              encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "absent, stale, or malformed"):
                recovery.apply_ready_partition(sealed, root / "progress.json",
                                               root / "scratch", 256, 1)


if __name__ == "__main__":
    unittest.main()
