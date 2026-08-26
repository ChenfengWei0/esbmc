#!/usr/bin/env python3
"""Stage-4 scalar return-value extcall pins: site discovery, typed literals, markers."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import solidity_path_put as sp

SRC = """
interface ENS { function owner(bytes32 node) external view returns (address); }
interface IOracle { function price() external view returns (uint256); function ok() external view returns (bool); }
contract R {
    ENS public immutable ens;
    IOracle internal oracle;
    constructor(ENS e) { ens = e; }
    function f(bytes32 node) external returns (address) {
        address owner = ens.owner(node);
        return owner;
    }
    function g() external view returns (uint256) {
        uint256 p = IOracle(0x1111111111111111111111111111111111111111).price();
        return p;
    }
    function h() external view returns (uint256) {
        uint256 q = oracle.price();
        return q;
    }
    function k() external view returns (bool) {
        bool okv = IOracle(0x1111111111111111111111111111111111111111).ok();
        return okv;
    }
}
"""

def main():
    sites = sp._high_level_scalar_call_sites(SRC, "R")
    assert sites["owner"][0][1:4] == ("state", "ens", "owner"), sites["owner"]
    assert sites["p"][0][0:3] == ("IOracle", "cast", "0x1111111111111111111111111111111111111111")
    lines, why = sp._render_high_level_scalar_pin(SRC, "R", "owner", 4294967295, "  ")
    assert lines == ["  // VERIPUT_EXTCALL_IFACE_STATE ens owner(bytes32) address(uint160(4294967295))"], (lines, why)
    lines, why = sp._render_high_level_scalar_pin(SRC, "R", "p", 7, "")
    assert lines == ['vm.mockCall(address(0x1111111111111111111111111111111111111111), abi.encodeWithSignature("price()"), abi.encode(uint256(7)));'], (lines, why)
    lines, why = sp._render_high_level_scalar_pin(SRC, "R", "q", 7, "")
    assert lines is None and "no public getter" in why, (lines, why)
    lines, why = sp._render_high_level_scalar_pin(SRC, "R", "okv", 1, "")
    assert lines and "abi.encode(true)" in lines[0], (lines, why)
    assert sp._scalar_pin_value_expr("int8", 255) == "int8(-1)"
    assert sp._scalar_pin_value_expr("bytes32", 3) == "bytes32(uint256(3))"
    assert sp._scalar_pin_value_expr("string", 3) is None
    print("all checks passed")

if __name__ == "__main__":
    main()
