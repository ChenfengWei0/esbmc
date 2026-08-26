// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// The focused unit `f` calls `g()`; solc's referencedDeclaration for that
// call is Base.g, but the executed body is the override Derived.g.  The
// focus-closure prune must keep Derived.g (same-name rule) so the assertion
// inside it is still found.  `Unrelated` must be prunable without harm.
contract Base {
    uint256 public x;
    function f() public { g(); }
    function g() internal virtual { x = 1; }
}

contract Unrelated {
    uint256 public y;
    function heavy(uint256 a) public { y = a * 3 + 1; }
    function heavier(uint256 a) public { for (uint256 i = 0; i < a; i++) y += i; }
}

contract Derived is Base {
    function g() internal override { x = 2; assert(x == 1); }
    function untouched() public pure returns (uint256) { return 7; }
}
