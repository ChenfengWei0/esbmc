// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Bytes32State {
    bytes32 responseHash;

    function set(bytes32 value) external {
        responseHash = value;
    }
}
