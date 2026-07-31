// THE OUTER BOX MEASURES AN OBSTRUCTED UNIT, AND SAYS SO ON EVERY REGION LINE.
//
// Same contract and same `--unwind 1` as
// solidity_path_cov_residual_unit_call_obstacle: the chain a -> b -> c is three
// units deep, one expansion pass reaches `b`, and the call to `c` that `b`
// brought in stays a call to `c`'s OWN body -- which carries the synthesised ABI
// value gate that models an EXTERNAL entry. Every path of `a` is therefore a
// named obstacle.
//
// THE ASYMMETRY WITH CERTIFY IS THE POINT, and it is deliberate:
//
//   certify     REFUSES the query outright
//               (solidity_path_cov_certify_obstacle_gated_unit_refused)
//   outer box   MEASURES and LABELS
//
// An outer box is a CONTAINMENT statement per coordinate and stays true on an
// obstructed unit; certification is an assertion ABOUT the box and can come back
// SUCCESSFUL over executions the chain does not have. Refusing the measurement
// would throw away information that is not wrong; letting the region travel
// WITHOUT the caveat would hand a driver a candidate it feeds straight to the
// certification query. So the label goes on the region line itself rather than
// once at the top -- the region line is what gets quoted.
//
// WHAT THIS PINS THAT NOTHING ELSE DOES. The gate reads the per-unit LOCALS, not
// `named_obstacle_paths`: that map is filled by the insertion loop the outer-box
// branch `continue`s past, so in this mode it is EMPTY. A reader of it would
// print no caveat at all while looking exactly like a reader that had checked --
// and every region here would then read as clean. Both region lines are pinned,
// not just one, because the label is applied per line and a half-applied caveat
// is worse than none.
pragma solidity ^0.8.0;

contract C {
    uint256 public x;

    function a(uint256 v) public {
        if (v > 1) {
            x = 1;
        }
        b(v);
    }

    function b(uint256 v) public {
        if (v > 2) {
            x = 2;
        }
        c(v);
    }

    function c(uint256 v) public {
        if (v > 3) {
            x = 3;
        }
    }
}
