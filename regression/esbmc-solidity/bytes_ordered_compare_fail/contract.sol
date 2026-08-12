pragma solidity >=0.8.0;

contract C {
    function check() public pure {
        assert(bytes1(0x80) < 0x80);
    }
}
