// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.0;

library Math {
    function max(uint a, uint b) internal pure returns (uint) {
        return a > b ? a : b;
    }

    function min(uint a, uint b) internal pure returns (uint) {
        return a < b ? a : b;
    }

    function add(uint a, uint b) internal pure returns (uint) {
        return a + b;
    }
}

contract C {
    using Math for uint;

    function f() public pure {
        uint a = 10;
        uint b = 20;
        assert(a.max(b) == 20);
        assert(b.min(a) == 10);
        assert(a.add(b) == 30);
        assert(Math.max(5, 15) == 15);
    }
}
