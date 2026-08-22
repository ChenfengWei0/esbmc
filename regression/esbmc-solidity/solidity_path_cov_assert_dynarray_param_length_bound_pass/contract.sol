// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract ArrLen {
    uint256 public n;

    function set(address[] memory a) external {
        n = a.length;
    }
}
