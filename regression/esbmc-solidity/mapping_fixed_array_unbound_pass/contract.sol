// SPDX-License-Identifier: UNLICENSED
pragma solidity >=0.8.0 <0.9.0;

// Round-trip test for `mapping(K => uint256[N])` in default unbound
// mode (no --bound, no `new Store()`).
//
// KNOWNBUG (architectural, tracked): the unbound path lowers this to
// `array<array<uint256, 3>, inf>` — same shape as the outer-dyn +
// inner-fixed case in `outer_dyn_inner_fixed_array_*`. ESBMC's flat
// array encoder `array_convt` cannot represent unbounded arrays whose
// element sort is itself an array sort
// (`src/solvers/smt/array_conv.cpp:92-95`). Bitwuzla reports
//   "terms with mismatching sort at indices 0 and 1"
// during `mk_eq` on the nested WITH chain.
//
// The working path (regression/esbmc-solidity/map_fixed_array_value_pass)
// avoids this by requiring --bound and `new Store()` so the access
// routes through `map_fixed_arr_get` via `should_treat_as_new()`, which
// side-steps the nested SMT-array shape entirely. Direct state-var
// access without those conditions never reaches that helper.
//
// Fixes under consideration are the same three listed in
// `outer_dyn_inner_fixed_array_pass`; none are in scope here.
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
