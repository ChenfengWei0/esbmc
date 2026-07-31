// S5 -- A BOOL COORDINATE CERTIFIES, and this is the FIRST HALF of a must-flip
// pair with solidity_path_cov_certify_bool_false_vacuous. The two specs differ
// in TWO decimals:
//
//     box `state.flag in [1,1]`, the flag==true path  ->  RESULT: CERTIFIED (here)
//     box `state.flag in [0,0]`, the SAME path        ->  RESULT: VACUOUS   (sibling)
//
// Before S5 neither ran at all: coord_expressible refused `bool` outright, so
// the query was refused by name and a contract branching on a flag had NO
// certifiable coordinate. The refusal was right about the shape -- a two-point
// domain has no interval -- and wrong about the conclusion: `{0,1}` has four
// subsets, so lo/hi/holes collapse EXACTLY to an allowed set and the entry
// constraint is a disjunction of equalities. Nothing is approximated.
//
// WHY THE ASSUMPTION IS NOT `0 <= c && c <= 1`. `>=` / `<=` on a bool operand
// falls through to the `assert(is_signedbv_type(...))` arms of
// src/solvers/smt/smt_conv.cpp (2494 / 2525 / 2556 / 2587) -- SIGABRT. Nor is a
// `constant_int2tc` built on a bool type anywhere; gen_true_expr() /
// gen_false_expr() are the only constants of that type this pipeline makes, so
// the question of whether an integer constant on a bool type is well formed at
// the SMT layer is sidestepped rather than answered.
//
// The other trap this pair covers is the TYPE RANGE. `bool_type2t::get_width()`
// returns 8 -- correctly, it is the byte of the memory model -- so the generic
// `2^width - 1` type check computes an admissible range of [0, 255] for a bool
// and `state.flag in [0, 200]` would sail through every gate in the pipeline.
// Both copies of that check (path_cov_fits_type and certify's inline one, now
// unified) special-case bool to [0, 1].
//
// PASSING THIS HALF ALONE IS NOT EVIDENCE. An implementation that ignored the
// box entirely also certifies here, because the constructor sets flag = true and
// state is not havoc'd at --solidity-max-tx 1, so EVERY execution walks this
// path. The sibling is what shows the box is actually read: the same path under
// the opposite box admits nothing at all.
pragma solidity ^0.8.0;

contract Flag {
    bool public flag;
    uint256 public sink;

    constructor() {
        flag = true;
    }

    function f() external payable returns (uint256) {
        if (flag) {
            sink = 1;
            return 1;
        }
        sink = 2;
        return 0;
    }
}
