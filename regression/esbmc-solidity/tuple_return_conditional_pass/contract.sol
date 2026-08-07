// SPDX-License-Identifier: GPL-3.0
pragma solidity >=0.8.0;

contract ConditionalTupleReturn {
    function pair(bool ok, uint256 value) public pure returns (bool, uint256) {
        return ok ? (true, 0) : (false, value);
    }

    function check(bool ok, uint256 value) public pure {
        (bool success, uint256 observed) = pair(ok, value);
        if (ok) {
            assert(success);
            assert(observed == 0);
        } else {
            assert(!success);
            assert(observed == value);
        }
    }
}
