pragma solidity ^0.8.0;

contract ExactState {
    uint256 unrelated = 9;

    function f(uint256 x) external returns (uint256) {
        if (x > 10) {
            return x + 1;
        }
        return 0;
    }
}
