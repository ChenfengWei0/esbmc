// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract C {
    struct Callback {
        function(uint256) internal pure returns (uint256, uint256) fn;
    }

    function pair(uint256 x) internal pure returns (uint256, uint256) {
        return (x, x + 1);
    }

    function check() external {
        Callback memory callback = Callback(pair);
        uint256 i;
        (uint256 first, uint256 second) = callback.fn(i++);
        first;
        second;
    }
}
