pragma solidity ^0.8.20;

contract BoolFixture {
    bool public initialized;

    function initialize() external {
        initialized = true;
    }
}
