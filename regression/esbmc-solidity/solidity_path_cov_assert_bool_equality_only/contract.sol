// S5 -- THE CRASH GUARD. A bool state variable gets the EQUALITY rungs and NOT
// the ordering ones, and a uint256 twin on the same contract still gets all six.
//
// The twin is the whole point of the fixture. "bool got 2 rungs" is also what an
// implementation that had silently dropped ordering for EVERY variable would
// produce, and "uint256 got 6" is what one that never gated anything would
// produce. Only the two together pin the SPLIT.
//
// ---- WHAT GOES WRONG WITHOUT THE SPLIT ----
//
// Stage 3 read, before S5:
//
//     const bool interval_ok = coord_expressible(vt, why);
//     const bool equality_ok = interval_ok || is_bool_type(vt);
//     ...
//     if (!interval_ok) continue;      // skips the four ordering rungs
//
// That was CORRECT while coord_expressible refused bool: a bool came back
// interval_ok = false, equality_ok = true, and got exactly eq/ne. S5 widens the
// whitelist to accept bool -- and the moment it does, `interval_ok` is TRUE for
// a bool, the `continue` stops firing, and `post >= pre` / `post <= pre` /
// `post > pre` / `post < pre` are built over a bool. Those land in the
// `assert(is_signedbv_type(...))` arms of src/solvers/smt/smt_conv.cpp
// (2494 / 2525 / 2556 / 2587): SIGABRT, in a build with asserts live.
//
// So the derivation is inverted -- `equality_ok` is what coord_expressible
// answers, and `interval_ok = equality_ok && !is_bool_type(vt)` is derived from
// it. FAULT INJECTION, run: restoring the old two lines and rebuilding turns
// this directory into a SIGABRT inside the SMT conversion, with the ladder never
// printed. The widening and the split are one change, not two.
//
// The region additionally bounds `state.flag`, which exercises the OTHER half of
// S5: a bool region coordinate is assumed as a disjunction of equalities over
// its allowed set, never as `0 <= c && c <= 0`. `flag` defaults to false and
// nothing has run before this transaction, so `[0,0]` is satisfiable and the
// region is non-vacuous -- which the ladder's own non-vacuity witness confirms
// before a single row is printed.
pragma solidity ^0.8.0;

contract Twin {
    bool flag;
    uint256 total;

    function bump(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            flag = true;
            total = total + a;
            return 1;
        }
        return 0;
    }
}
