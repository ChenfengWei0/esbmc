// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.0;

contract Overloaded {
    function approve(address) external returns (bool) {
        return true;
    }

    function approve(address, uint256 amount) external returns (bool) {
        return amount != 0;
    }
}
