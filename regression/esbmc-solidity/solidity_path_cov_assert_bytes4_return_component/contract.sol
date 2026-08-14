// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract HookTarget {
    function isHookTarget() external pure returns (bytes4) {
        return this.isHookTarget.selector;
    }
}
