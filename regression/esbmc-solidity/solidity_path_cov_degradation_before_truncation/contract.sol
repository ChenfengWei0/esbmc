// DEGRADATION FIRES BEFORE TRUNCATION, and the two are different mechanisms.
//
// Same contract as solidity_path_cov_internal_call_expands, which pins that
// under the default budget it enumerates 8 complete paths across 2 units. Here
// the budget is squeezed to 4 (`--path-cov-max-goals 4`), which `caller` cannot
// fit: expansion puts BOTH `pub`'s decision and `helper`'s decision into its
// path identity, so it has 4 body paths plus the ABI gate.
//
// There are two ways to make it fit and they are NOT interchangeable:
//
//   * TRUNCATION stops enumerating at the cap. The dropped paths still EXIST in
//     the model and symex will execute them, so they are missing from the
//     sibling set and nothing downstream can subtract their inputs from a
//     surviving path's certified region.
//   * DEGRADATION withdraws a call point BEFORE enumeration. The callee stays a
//     call, so it still executes; it just stops contributing decisions. The
//     path classes get coarser but still partition the whole input space, so
//     the enumeration stays complete and only assertion STRENGTH is lost —
//     and lost at a named place rather than everywhere.
//
// The pinned order is: degradation first, truncation only as a last-resort
// backstop. This test pins that the backstop NEVER RUNS here, with two negative
// lookaheads over the whole output (no "per-function path/length cap" warning
// and no "NAMED OBSTACLE" block) plus the positive counts that would move if it
// had.
//
// Expected shape at budget 4:
//   caller -- `helper` withdrawn (it is private, hence withdrawable), so its
//             decision leaves `caller`'s identity: 2 body paths + gate = 3
//   pub    -- untouched: 2 body paths + gate = 3
//   total 6 across 2 units, all feasible.
//
// The assertion-strength loss is visible in the numbers rather than asserted in
// prose: at the default budget this contract reports `U 1` because
// `caller:path:14` combines `pub`'s `a <= 1` with `helper`'s `a > 3` and is
// infeasible. Withdraw `helper` and that combination is no longer a path at all,
// so the run reports `F 6, U 0`. That is exactly what "coarser classes, weaker
// assertions, still a partition" means, and it is why 6/6 here is NOT better
// coverage than 7/8 there.
pragma solidity ^0.8.0;

contract C {
    uint256 public x;

    function pub(uint256 a) public {
        if (a > 1) {
            x = 1;
        } else {
            x = 2;
        }
    }

    function helper(uint256 a) private {
        if (a > 3) {
            x = 3;
        }
    }

    function caller(uint256 a) public {
        pub(a);
        helper(a);
    }
}
