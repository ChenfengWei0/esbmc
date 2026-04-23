// SPDX-License-Identifier: UNLICENSED
pragma solidity >=0.8.0 <0.9.0;

// Round-trip test for `mapping(K => uint256[N])` in default unbound
// mode (no --bound, no `new Store()`).
//
// Currently KNOWNBUG: the unbound access path for mapping-of-fixed-array
// crashes with a Bitwuzla sort mismatch during SMT encoding:
//   "terms with mismatching sort at indices 0 and 1"
// The working path (regression/esbmc-solidity/map_fixed_array_value_pass)
// requires --bound and `new Store()` so the access routes through
// `map_fixed_arr_get` via `should_treat_as_new()`. Direct state-var
// access without those conditions never reaches that helper.
//
// Independent of the `uint[N][M]` value-set issue.
// See docs/claude/solidity/language-support.md §F.3.
contract MappingFixedArrayUnboundPass {
    mapping(address => uint256[3]) public m;

    function check(address k) external {
        m[k][0] = 10;
        m[k][1] = 20;
        m[k][2] = 30;
        assert(m[k][0] == 10);
        assert(m[k][1] == 20);
        assert(m[k][2] == 30);
    }
}
