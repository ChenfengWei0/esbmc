// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: exit kinds — the R0 observable, which costs ZERO extra queries
/// because it is read straight off the path identity.
///
/// Four exits, deliberately all different, in one function:
///   a plain `revert` with a string reason;
///   a `revert` with a custom error;
///   a `require` failure (a rollback, which is not the same shape as `revert`);
///   a normal return.
///
/// EXPECTED: the report classifies all four distinctly, and the emitted test
/// carries the matching Foundry form — a bare call for the normal exit, and
/// `vm.expectRevert` with the RIGHT selector or string for each failing one.
///
/// WHY IT IS WORTH A CONTRACT OF ITS OWN: an exit census that says "normal"
/// when the chain reverts produces a test asserting the call succeeds, which is
/// red on the unmodified contract. That has been measured once already, on
/// aqua's `pull`, where the census confirmed a normal exit and forge reported
/// `[FAIL: SafeTransferFromFailed()]`. Here every exit is one the author chose,
/// so a mismatch is unambiguous rather than a question about how an external
/// call was modelled.
contract P13_Exits {
    error TooBig(uint256 got);

    uint256 public tag;

    function classify(uint256 x) external {
        require(x != 0, "zero");
        if (x == 1) {
            revert("one");
        }
        if (x > 1000) {
            revert TooBig(x);
        }
        tag = x;
    }
}
