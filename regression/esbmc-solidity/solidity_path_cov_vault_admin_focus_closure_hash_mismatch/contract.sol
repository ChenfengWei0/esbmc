pragma solidity ^0.8.20;

abstract contract Limits {
    uint256 internal constant _MIN_TOKENS = 2;
    constructor(uint256) {}
}

contract VaultAdmin is Limits {
    constructor(uint256 value) Limits(value) {}

    function getMinimumPoolTokens() external pure returns (uint256) {
        return _MIN_TOKENS;
    }

    function retainedWhenHashMismatches(uint256 value) external pure returns (uint256) {
        return value + 1;
    }
}
