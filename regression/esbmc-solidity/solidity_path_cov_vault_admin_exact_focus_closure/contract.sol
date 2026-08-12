pragma solidity ^0.8.20;

abstract contract Limits {
    uint256 internal constant _MIN_TOKENS = 2;
    uint256 internal immutable seed;

    constructor(uint256 value) {
        seed = value;
    }

    function unrelated(uint256 value) external pure returns (uint256) {
        if (value > 7) {
            return value + 1;
        }
        return value * 2;
    }
}

contract VaultAdmin is Limits {
    constructor(uint256 value) Limits(value) {}

    function getMinimumPoolTokens() external pure returns (uint256) {
        return _MIN_TOKENS;
    }
}
