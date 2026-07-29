// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// R0 reaching the emitted test: a call whose path is CONFIRMED to exit normally
// is emitted bare, and that bareness is the assertion -- a revert at run time
// fails the test. Before this, every generated call was wrapped in a
// revert-tolerant try/catch, i.e. the suite asserted nothing at all, which is
// exactly the baseline this work exists to beat.
//
// Three units, three exit shapes, so the pin is per-shape rather than a total:
//
//   ok       every path exits normally          -> bare calls
//   guarded  a `require` failure reverts        -> the reverting path is NOT
//            asserted; the emitter cannot yet reproduce the reverting
//            condition for every revert shape (the ABI non-payable gate
//            reverts only when msg.value != 0, and a typed call sends none), so
//            revert paths keep the honest try/catch
//   both     a branch, both arms normal         -> bare on both
//
// The counts in test.desc are what must move when the remaining undetermined
// exit entry is fixed: try/catch down, bare up. A change that only relabels
// moves one count without the other.
contract R0 {
    uint256 public x;

    function ok(uint256 v) external {
        x = v;
    }

    function guarded(uint256 v) external {
        require(v > 10, "small");
        x = v;
    }

    function both(uint256 v) external {
        if (v > 10) {
            x = 1;
        } else {
            x = 2;
        }
    }
}
