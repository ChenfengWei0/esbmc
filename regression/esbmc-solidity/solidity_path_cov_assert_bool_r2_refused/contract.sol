pragma solidity ^0.8.0;

contract BoolR2 {
    bool flag;

    function set(uint256 x) external {
        if (x > 10) {
            flag = true;
        }
    }
}
