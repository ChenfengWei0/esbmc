// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: the DOUBLE IDENTITY of a unit body — the hazard, in ten lines.
///
/// `helper` is PUBLIC, so it is a unit with its own ABI/payable guard AND it is
/// called internally by `caller`. The same body therefore has to appear twice
/// with different meanings: once as an entry with its guard, once inlined into
/// `caller`'s paths WITHOUT the guard.
///
/// EXPECTED: two units; `caller`'s paths contain `helper`'s decision; and
/// `helper`'s own paths still carry its synthesised ABI gate.
///
/// THE FAILURE MODE THIS EXISTS TO CATCH is a RED TEST, not a wrong number: if
/// the inlined copy keeps the entry guard, `caller`'s paths acquire a condition
/// that is false on the real chain, and a test generated from one of them fails
/// on the unmodified contract. That is the worst outcome the method can
/// produce, and the project's own note calls this shape a "red test hole".
///
/// It is also the acceptance criterion for any change to WHICH calls get
/// expanded — including the pending `--focus-function` narrowing, where
/// suppressing a non-focused unit must suppress it as an ENTRY and never as an
/// inlined callee.
contract P12_UnitCallee {
    uint256 public tag;

    function helper(uint256 y) public returns (uint256) {
        if (y > 50) {
            return 10;
        }
        return 20;
    }

    function caller(uint256 x) external {
        require(x > 0);
        tag = helper(x);
    }
}
