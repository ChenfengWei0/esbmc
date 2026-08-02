// STAGE 3 -- a tuple member the ghost CANNOT hold is REFUSED BY NAME, while
// the members it can hold still get their rungs.
//
// ---- WHAT THIS DIRECTORY USED TO PIN, AND WHY THAT PREMISE IS GONE ----
//
// It was founded on "a tuple return gets NO rung at all": a tuple emits no
// RETURN instruction, so the single per-unit ghost was never assigned and the
// whole return family was refused wholesale. That is no longer true. The
// instrumenter now recognises the frontend's own lowering -- writes into the
// contract-scope tuple_instance$<node-id> object keyed by this unit's AST node
// id, the same key bmc.cpp's counterexample harvest uses -- and builds ONE
// ghost PER MEMBER, so `return.0`, `return.1`, ... each carry their own rungs.
//
// Rewriting the expectations to match would have left a fixture whose comment
// argued for a behaviour the binary no longer has. It is re-founded instead, on
// the case where a refusal is still the correct answer.
//
// ---- WHAT IT PINS NOW ----
//
// A MIXED tuple: member 0 is a uint256 the ghost can hold, member 1 is `bytes
// memory` -- an aggregate with no scalar to copy. Both halves are checked in
// one run:
//
//   * member 0 MUST appear, as `return.0: ...` rows. If the per-member pass
//     regressed to emitting nothing, this is the half that catches it.
//   * member 1 MUST appear in the header REFUSAL naming it, and MUST NOT have
//     a single row of its own. Silent absence is the failure this directory
//     has always existed to catch: in this mode a candidate with no row reads
//     as one that needed no assertion, and for a return value that reads as
//     "measured, and unconstrained" -- the opposite of what happened.
//   * there MUST be no whole-value `return: return ...` row. On a multi-return
//     unit the whole value is not a thing the oracle can bind, and the emitter
//     refuses a PUT that carries one; a rung claiming to be about it would be
//     an assertion about nothing.
//
// The retlive witness (`return: a value IS returned on this path`) is NOT a
// member rung -- it is the shared non-vacuity witness for the whole family, and
// it stays.
//
// `total` is here so the ladder is not empty: without a scalar state variable
// the zero-candidate gate would fire first and the run would exit before the
// refusal is printed, which would make the fixture pin a different gate than
// the one it is about.
//
// ---- WHY THE SPEC NAMES enc=3 AND NOT enc=2 ----
//
// MEASURED, and it is a separate defect this fixture deliberately does NOT
// pin: on a tuple-returning unit the two arms exit differently.
//
//     enc=2  (a > 10)   exit_kind = UNDETERMINED
//     enc=3  (a <= 10)  exit_kind = normal
//
// Both arms fall to END_FUNCTION -- a tuple return emits no RETURN -- but the
// first jumps there straight from the if-body and so SKIPS the epilogue, which
// is the only positive evidence of a normal exit available at END_FUNCTION. N5
// then refuses the ladder on enc=2 before a single candidate or refusal is
// printed, so a spec naming it would pin the undetermined-exit gate instead of
// the return refusal. That whole class of tuple paths being unusable as an
// oracle is a real gap and belongs in its own fixture with its own fix; naming
// enc=3 keeps THIS directory about one thing.
//
// `payable`, so no ABI value gate is synthesised and msg.value needs no bound.
pragma solidity ^0.8.0;

contract MixTuple {
    uint256 total;

    function two(uint256 a) external payable returns (uint256, bytes memory) {
        if (a > 10) {
            total = total + a;
            return (1, "x");
        }
        return (0, "");
    }
}
