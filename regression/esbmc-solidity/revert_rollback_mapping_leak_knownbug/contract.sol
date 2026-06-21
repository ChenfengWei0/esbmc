// SPDX-License-Identifier: GPL-3.0
pragma solidity >=0.8.0;

// Adversarial-review finding #2: a `revert` only restores the contract
// struct `*this`; mapping backing stores live in a global keyed by
// `$address` and are NOT rolled back.  A mapping write performed in a
// reverted transaction therefore leaks into the next transaction of the
// bounded multi-tx harness.
//
// Real EVM: setAndRevert reverts, so m[k] is rolled back to 0, and the
// assert in checkClean always holds -> VERIFICATION SUCCESSFUL.
// Current ESBMC: m[k] == 1 survives the revert -> assert can fail ->
// VERIFICATION FAILED (spurious / false positive).  KNOWNBUG until the
// rollback covers global stores.
contract C {
    mapping(uint => uint) m;

    function setAndRevert(uint k) public {
        m[k] = 1;
        revert();
    }

    function checkClean(uint k) public view {
        assert(m[k] == 0);
    }
}
