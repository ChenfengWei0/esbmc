pragma solidity ^0.8.0;

contract C {
    receive() external payable {
        assert(msg.value == msg.value);
    }

    fallback() external payable {
        assert(false);
    }
}
