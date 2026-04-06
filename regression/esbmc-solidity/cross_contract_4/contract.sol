// SPDX-License-Identifier: GPL-3.0
pragma solidity >=0.8.0;

// Test: inline contract type cast + method call.
// TokenCreator(address(creator)).isTokenTransferOK(...) should work.
// ESBMC bug: assertion failure in get_contract_member_call_expr because
// the inline cast expression lacks "referencedDeclaration".

contract OwnedToken {
    TokenCreator creator;
    address owner;

    constructor() {
        owner = msg.sender;
        creator = TokenCreator(msg.sender);
    }

    function test_inline_call() public view {
        bool ok = TokenCreator(address(creator)).isTokenTransferOK(owner, address(0));
        assert(ok);
    }
}

contract TokenCreator {
    function isTokenTransferOK(address currentOwner, address newOwner)
        public
        pure
        returns (bool ok)
    {
        return currentOwner != newOwner;
    }
}
