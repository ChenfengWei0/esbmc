// The decision-set census's own test — and the reason it exists is that the
// census nearly became an empty check without anyone noticing.
//
// The census flags a construct that removes executions WITHOUT a branch, which
// is fatal downstream: the removed execution is not a path, so it is not a
// sibling of anything, so the stage-3 subtraction can never remove its inputs
// from any certified region of that unit.
//
// Its original hit set was `require` inside an internal library or a free
// function. Widening the revert-observation scope FIXED that case — and in
// doing so removed the census's entire hit set. A detector that can no longer
// hit anything is green forever and indistinguishable from a working one, which
// is exactly the failure pattern (a "we don't know" or "nothing found" cell
// quietly absorbing "our tool is broken") that this pipeline has already been
// bitten by three times.
//
// So the census is kept honest by a construct that still removes executions
// without a branch: an explicitly written `__ESBMC_assume`. At the goto layer it
// is indistinguishable from a lowered `require`, and it breaks the subtraction
// in exactly the same way, so marking it is correct rather than merely
// conservative.
//
// Expected: all 3 paths of `f` are named obstacles, and coverage still reads
// 100% — an obstacle is not partial credit and must not be folded into the
// percentage.
//
// The obstacle category holds exactly TWO causes, and this test pins that only
// cause (a) fires here: `(b) 0 path(s) across 0 unit(s)` is the other one — a
// unit still calling another UNIT's own gated body unexpanded. Both are the same
// failure (the model admits an execution the chain does not have) reached by
// different routes.
//
// Goal-cap TRUNCATION used to be counted here as a third cause and is not any
// more, because it is not that failure at all: a truncated path EXISTS in the
// model and keeps its `tr` accounting, so the certification query
// `assume(interval); assert(tr == pi)` still rejects its inputs and the
// certified region merely shrinks. An execution deleted by a branch-free assume
// exists nowhere, so no query can see it and no interval can be shrunk away
// from it — only that can ship a test that is red on the unmodified contract.
// The negative lookahead pins that no assertion-strength (truncation) report is
// emitted here at all.
//
// If this ever goes green with a zero obstacle count, the census has died.
pragma solidity ^0.8.0;

function __ESBMC_assume(bool) pure {}

contract CA {
    uint256 public x;

    function f(uint256 a) public returns (uint256) {
        __ESBMC_assume(a < 10);
        if (a > 3) {
            x = 1;
        } else {
            x = 2;
        }
        return x;
    }
}
