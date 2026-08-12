// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface Feed {
    function latest() external view returns (uint80, int256, uint256, uint256, uint80);
}

contract C {
    Feed feed;

    function check() public view {
        (, int256 answer, , uint256 updatedAt, ) = feed.latest();
        assert(answer == answer);
        assert(updatedAt == updatedAt);
    }
}
