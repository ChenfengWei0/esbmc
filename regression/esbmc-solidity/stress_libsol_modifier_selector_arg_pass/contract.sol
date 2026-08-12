// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract VaultAdapter {
    modifier checkAccess(bytes4 _selector) {
        require(true);
        _;
    }

    function setSlopes(uint256 x)
        public
        checkAccess(this.setSlopes.selector)
    {
        assert(x == x);
    }
}
