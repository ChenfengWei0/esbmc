// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract C {
    function pair(uint256 x) internal pure returns (uint256, uint256) {
        assert(false);
        return (x, x + 1);
    }

    function check() external {
        function(uint256) internal pure returns (uint256, uint256) fn = pair;
        (uint256 first, uint256 second) = fn(0);
        first;
        second;
    }
}
