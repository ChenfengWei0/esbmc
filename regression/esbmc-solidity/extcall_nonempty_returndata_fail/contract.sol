// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface IReturnsWord {
    function read() external returns (uint256);
}

contract ReturnsWord is IReturnsWord {
    function read() external pure returns (uint256) {
        return 7;
    }
}

contract Probe {
    function check() public {
        IReturnsWord target = new ReturnsWord();
        assert(target.read() != 7);
    }
}
