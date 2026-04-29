// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

contract H {
    function check() public pure {
        uint r;
        assembly {
            r := div(7, 0)
        }
        assert(r == 0);
    }
}
