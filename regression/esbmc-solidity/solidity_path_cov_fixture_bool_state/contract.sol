pragma solidity ^0.8.20;

contract BoolFixture {
    bool public initialized;

    constructor() {
        initialized = true;
    }

    function initialize() external {
        require(!initialized, "initialized");
        initialized = true;
    }
}
