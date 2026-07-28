// Stage-2 CERTIFICATION QUERY — direction one: a box INSIDE the path's domain
// must certify.
//
// One decision, so `f` has exactly two paths:
//   enc=2, depth=1  -> the `a > 10` branch (guard value 0)
//   enc=3, depth=1  -> the fall-through    (guard value 1)
// `payable`, so no ABI value gate is synthesised and msg.value need not be
// bounded — one interval over one input is the smallest shape in which the
// query can be wrong.
//
// The query is `assume(box); assert(tr == enc && cnt == depth)`, asserted at
// EVERY exit of the unit. That placement is the whole point: an input inside
// the box that walks the OTHER path leaves through the other exit, so with the
// assert on this path's own exit alone it would never be checked and the query
// would hold vacuously — permanently green in the one place where green has to
// mean something.
//
// PASSING THIS TEST ON ITS OWN IS NOT EVIDENCE OF ANYTHING. An implementation
// that checks nothing at all also reports SUCCESSFUL here. What this half
// establishes is only that the check does not fire ALWAYS; the `_straddles`
// twin establishes that it fires at all. The test is the PAIR, and the property
// being tested is that the two verdicts are consistently OPPOSITE. Deleting
// either half leaves something that still looks like it is protecting
// something and is not.
//
// exit0 (this path's own exit) PASSES in both halves; only exit1 (the other
// path's exit) flips. So the wrong implementation — asserting on this path's
// exit alone — is precisely the run in which the pair comes out all green.
//
// This is also why neither half needs fault injection: the check comes with a
// direction that MUST flip. A detector that has no such direction (the exit
// census, the decision-set census: they never fire on a correct run) cannot
// testify for itself and has to be injected. One-directional detectors need
// injection; a detector carrying its own must-flip control does not.
//
// The certify spec `cert.json` is read-only: the tool never writes it back, so
// this fixture cannot be polluted by the run that consumes it (the discipline
// that came out of the cross-run covered-set fixture).
pragma solidity ^0.8.0;

contract Box {
    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            return 1;
        }
        return 0;
    }
}
