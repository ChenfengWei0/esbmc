pragma solidity ^0.8.0;

contract C {
    fallback() external payable {
        assert(false);
    }

    function unrelated() external pure {
        assert(true);
    }
}
