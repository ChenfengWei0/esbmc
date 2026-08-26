// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// A modifier-wrapped value-returning unit exits through the RETURN the
// modifier lowering synthesises (`return aux_var` in the chain helper and
// `return chain()` in the wrapper).  Both must carry `sol_source_return`, or
// the coverage exit census reports every such path as `undetermined` and
// Stage 4 refuses the oracle ladder for the unit.
contract M {
    address public admin;
    uint256 public v;
    bool locked;

    modifier guard() {
        require(!locked, "locked");
        locked = true;
        _;
        locked = false;
    }

    function getV(uint256 x) public guard returns (uint256) {
        if (x > 10) return v + 1;
        return v;
    }

    function setV(uint256 x) public guard {
        if (x == 0) revert("zero");
        v = x;
    }
}
