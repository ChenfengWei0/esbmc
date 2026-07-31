// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// ISOLATES: the EXIT dimension, crossed exhaustively instead of by example.
//
// This project has been caught out three times by return shapes, and its own
// rule says shapes must be enumerated as a cross product of syntactic
// dimensions rather than by picking the ones that came to mind. P13 has four
// exits and P24 has a revert inside a callee; neither is a cross product.
//
// The dimensions crossed here:
//
//   WHERE the exit happens : unit body / modifier / inlined internal callee
//   HOW it exits           : normal fallthrough, return-with-value,
//                            early return-no-value, bare revert(),
//                            revert with string, custom error,
//                            require rollback (with and without a reason)
//
// EXPECTED: every one of these is a DISTINCT exit kind in the report, and the
// emitted test carries the matching Foundry form — a bare call for a normal
// exit, `vm.expectRevert()` for a bare revert, `vm.expectRevert(bytes)` for a
// string, `vm.expectRevert(Selector.selector)` for a custom error.
//
// FOUR EXITS THAT CANNOT APPEAR HERE, AND THAT IS THE POINT OF SAYING SO:
// arithmetic overflow (Panic 0x11), division by zero (0x12) and array
// out-of-bounds (0x32) have NO Panic modelling anywhere in the tree, so they
// are not exits in the model at all — with the division check off, `a / 0`
// evaluates to `type(uintN).max` rather than reverting. Out-of-gas is not
// modelled either. A test generated from a path that "returns" such a value
// asserts something no chain can produce, and no exit census can catch it
// because there is no exit to census.
contract P25_ExitMatrix {
    error Custom(uint256 got);

    uint256 public tag;

    modifier gate(uint256 x) {
        if (x == 5) {
            revert("modifier-five");
        }
        require(x != 6, "modifier-six");
        _;
    }

    function calleeExits(uint256 x) internal pure returns (uint256) {
        if (x == 1) {
            revert();
        }
        if (x == 2) {
            revert("callee-two");
        }
        if (x == 3) {
            revert Custom(x);
        }
        require(x != 4);
        return x;
    }

    // normal fallthrough, no return statement at all
    function fallthroughExit(uint256 x) external gate(x) {
        tag = calleeExits(x);
    }

    // return with a value, plus an early return with none on another arm
    function valueExit(uint256 x) external returns (uint256) {
        if (x > 1000) {
            tag = 1;
            return 0;
        }
        tag = calleeExits(x);
        return tag;
    }

    // every failing shape in the unit's OWN body, for contrast with the callee
    function ownBodyExits(uint256 x) external {
        if (x == 11) {
            revert();
        }
        if (x == 12) {
            revert("own-twelve");
        }
        if (x == 13) {
            revert Custom(x);
        }
        require(x != 14, "own-fourteen");
        tag = x;
    }
}
