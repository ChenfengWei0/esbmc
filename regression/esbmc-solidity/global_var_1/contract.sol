pragma solidity >=0.5.0;

uint constant x = 1;

contract Base {
    function test() external {
        uint z = 1;
        assert(x == z);
    }
}
