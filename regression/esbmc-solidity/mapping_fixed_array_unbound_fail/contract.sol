// SPDX-License-Identifier: UNLICENSED
pragma solidity >=0.8.0 <0.9.0;

// Violation test for `mapping(K => uint256[N])` in default unbound mode.
// Under correct semantics the `m[k][0] != 10` assertion is violated.
//
// KNOWNBUG: same `array_convt` array-of-array limitation as the _pass
// sibling; see that file for the full root-cause write-up.
// See docs/claude/solidity/language-support.md §F.3.
contract MappingFixedArrayUnboundFail {
    mapping(address => uint256[3]) public m;

    function check(address k) external {
        m[k][0] = 10;
        assert(m[k][0] != 10);
    }
}
