// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract SignedReturn {
    function value() external pure returns (int8) {
        return -1;
    }
}
