// SPDX-License-Identifier: GPL-3.0
pragma solidity >=0.8.0;

// Test: contract-to-address type cast should preserve the address.
// In the constructor, both `owner` and `creator` are derived from msg.sender.
// Therefore `owner == address(creator)` should hold.
// ESBMC bug: address(creator) returns a different address than msg.sender.

contract OwnedToken {
    TokenCreator creator;
    address owner;
    bytes32 name;

    constructor(bytes32 name_) {
        owner = msg.sender;
        creator = TokenCreator(msg.sender);
        name = name_;
    }

    function changeName(bytes32 newName) public {
        if (msg.sender == address(creator))
            name = newName;
    }

    function transfer(address newOwner) public {
        if (msg.sender != owner) return;
        if (creator.isTokenTransferOK(owner, newOwner))
            owner = newOwner;
    }

    function test_address_cast() public view {
        assert(owner == address(creator));
    }
}

contract TokenCreator {
    function changeName(OwnedToken tokenAddress, bytes32 name) public {
        tokenAddress.changeName(name);
    }

    function isTokenTransferOK(address currentOwner, address newOwner)
        public
        pure
        returns (bool ok)
    {
        return currentOwner != newOwner;
    }
}
