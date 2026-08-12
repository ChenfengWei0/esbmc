pragma solidity ^0.8.0;

contract ConstructorAssertFallback {
    bytes32 private constant SLOT =
        bytes32(uint256(keccak256("eip1967.proxy.implementation")) - 1);

    constructor() {
        assert(SLOT != bytes32(0));
    }

    function admin() public payable returns (uint256) {
        return 1;
    }
}
