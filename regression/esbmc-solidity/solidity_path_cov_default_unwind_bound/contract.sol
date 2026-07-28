// Without --unwind, path coverage used to run until the memory limit killed it.
//
// Two independent sources of unbounded exploration:
//   * `t.call("")` — under the default --unbound model an external call is a
//     nondet RE-ENTRY into this contract's own dispatcher, so it recurses.
//     Measured before the fix: `_ESBMC_Nondet_Extcall_C` unwound 944 times and
//     the run ended in `ERROR: Out of memory`.
//   * the `for` loop — plain unbounded unwinding, same outcome.
//
// Meanwhile the OFFLINE path enumeration bounded every back-edge at its own
// `path_cov_unwind` (4). plan §3.1 requires the two bounds to be aligned, "so
// that what the enumeration judges feasible and what symex judges feasible
// agree" — they were not.
//
// The fix: with --unwind unset, adopt the enumeration's own bound as the symex
// unwind bound and SAY SO on stdout. Truncation stays visible: the run prints
// the "Coverage may be UNDER-REPORTED ... Loops truncated" warning and every
// JSON entry carries bound.unwind. This test pins the adopted bound, the
// termination, and the truncation disclosure together — if the message
// disappears the tool is back to running unbounded.
pragma solidity ^0.8.0;

contract C {
    uint256 public x;

    function loopy(uint256 n) public {
        uint256 t = 0;
        for (uint256 i = 0; i < n; i++) {
            t += i;
        }
        x = t;
    }

    function reachOut(address t) public {
        (bool ok, ) = t.call("");
        if (ok) {
            x = 1;
        }
    }
}
