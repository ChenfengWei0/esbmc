"""Field-level closure for aggregate state coordinates (euler `vaultStorage`)."""
import importlib.util
import json
import os
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


deps = _load("solidity_ast_dependencies")
gen = _load("solidity_path_generalise")

SRC = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;
contract V {
    struct S { address admin; uint256 fee; uint8 locked; }
    S internal vs;
    uint256 public other;
    modifier reentrantOK() { require(vs.locked == 0); _; }
    function admin() public view reentrantOK returns (address) { return vs.admin; }
    function fee() public view returns (uint256) { return _fee(); }
    function _fee() internal view returns (uint256) { return vs.fee; }
    function reset() public { vs = S(address(0), 0, 0); }
    function useOther() public view returns (uint256) { return other; }
}
"""


def _ast():
    d = tempfile.mkdtemp()
    sol = os.path.join(d, "v.sol")
    open(sol, "w").write(SRC)
    out = subprocess.run(["solc", "--ast-compact-json", sol], capture_output=True, text=True, check=True).stdout
    path = os.path.join(d, "v.solast")
    open(path, "w").write(out)
    return path


AST = _ast()


def test_getter_names_only_the_members_it_touches():
    got, _ev = deps.unit_state_member_dependencies(AST, "V", "admin")
    assert got == {"vs": ["admin", "locked"]}


def test_members_reached_through_internal_calls():
    got, _ev = deps.unit_state_member_dependencies(AST, "V", "fee")
    assert got == {"vs": ["fee"]}


def test_whole_variable_use_fails_closed():
    got, _ev = deps.unit_state_member_dependencies(AST, "V", "reset")
    assert got == {"vs": None}


def test_unrelated_units_do_not_mention_the_struct():
    got, _ev = deps.unit_state_member_dependencies(AST, "V", "useOther")
    assert got == {"other": None}


def test_filter_drops_unaccessed_fields_and_pads_only():
    coords = ["state.vs$5.admin", "state.vs$5.fee", "state.vs$5.anon_pad$1", "state.vs$5.locked",
              "state.other$9", "state.m[0].fee"]
    kept, dropped = gen.filter_unreferenced_aggregate_fields(coords, {"vs": ["admin", "locked"]})
    assert kept == ["state.vs$5.admin", "state.vs$5.locked", "state.other$9", "state.m[0].fee"]
    assert dropped == ["state.vs$5.fee", "state.vs$5.anon_pad$1"]


def test_filter_keeps_everything_for_whole_use_or_unknown_variables():
    coords = ["state.vs$5.admin", "state.vs$5.anon_pad$1", "state.q$2.x"]
    kept, dropped = gen.filter_unreferenced_aggregate_fields(coords, {"vs": None})
    assert kept == coords and dropped == []
