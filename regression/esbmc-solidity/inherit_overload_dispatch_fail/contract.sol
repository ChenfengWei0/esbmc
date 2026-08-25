// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Inherited OVERLOADS: Base declares f(uint) and f(uint,uint). The merge
// into D used to compare inherited functions by name only, so the second
// overload was dropped as if D had overridden it, and D's dispatcher could
// never reach the assert below (the run reported VERIFICATION SUCCESSFUL).
contract Base {
    function f(uint a) external pure returns (uint) { return a; }
    function f(uint a, uint b) external pure returns (uint) {
        assert(a + b != 3);
        return a + b;
    }
}

contract D is Base {
    function g() external pure returns (uint) { return 1; }
}
