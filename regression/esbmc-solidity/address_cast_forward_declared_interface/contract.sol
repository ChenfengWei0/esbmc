// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;
contract C {
    // Regression: `IV` is declared AFTER this contract. address(v_) used to
    // lower to _ESBMC_enclosing_contract_address (0 during construction),
    // so every deployment reverted and check() was unreachable.

    IV public v;
    error VaultNotSet();
    constructor(IV v_) {
        if (address(v_) == address(0)) revert VaultNotSet();
        v = v_;
    }
    function owner() public pure returns (uint) { return 1; }
    function deployed() public pure {
        // Reachable only if the constructor did NOT revert. With the old
        // lowering the constructor always reverted (assume(false)), so this
        // assert was vacuous and the run printed VERIFICATION SUCCESSFUL.
        assert(false);
    }
}
interface IV { function f() external; }
