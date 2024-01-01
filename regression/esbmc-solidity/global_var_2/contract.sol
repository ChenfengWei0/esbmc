pragma solidity >=0.5.0;

contract Base {
    uint constant y = 1;

    function test() external {
        uint z = 1;
        assert(x == y);
        assert(x == z);
        assert(y == z);
    }
}

uint constant x = 1;
