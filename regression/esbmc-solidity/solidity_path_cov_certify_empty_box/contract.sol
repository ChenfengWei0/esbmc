// Stage-2 CERTIFICATION QUERY: an EMPTY box must be refused BEFORE the query,
// because an empty box certifies vacuously and does it while looking perfect.
//
// The contract is the `_certify_box_inside` fixture verbatim, and that is the
// point of reusing it: the only difference between the two tests is a `lo` and a
// `hi` swapped in cert.json. `a in [11, 100]` certifies for a real reason;
// `a in [100, 11]` used to certify for no reason at all.
//
// MEASURED on this fixture immediately before the check was added: with
// `a in [100, 11]` the run printed VERIFICATION SUCCESSFUL and exited 0. The
// mechanism is not subtle — `assume(100 <= a <= 11)` is unsatisfiable, so no
// execution reaches any exit, so every exit assert holds for want of an
// execution. Nothing is wrong with the reasoning; the question was empty.
//
// This is the SECOND route to a false certificate found in one night. The first
// was the driver deciding certification by substring-matching a phrase that
// appears in an ESBMC warning, so its gate was permanently green. Both produce a
// certificate no one asked a real question for, and the two are independent:
// fixing either leaves the other. This one is worse to look at, because the
// output is not merely green — it is a certificate printed next to a named box
// that contains no inputs.
//
// WHY THE TOOL AND NOT JUST THE DRIVER. The driver already refuses empty boxes.
// But certification is the only reliability gate this whole method has, and a
// gate that depends on its caller to guard it is guarded by exactly one thing —
// which, in this pipeline, has already failed once. The check here costs one
// comparison per bound.
//
// WHY BEFORE THE QUERY AND NOT AT THE ANSWER. An unsatisfiable assumption does
// not make the question hard to answer, it makes the question meaningless. There
// is no verdict to reinterpret afterwards, so the refusal belongs where the
// question is formed. That also makes the run's shape identical to the other
// refusal in this stage: a clean non-zero exit, the reason named, and no verdict
// line at all — so a caller reading SUCCESSFUL/FAILED as whole lines gets its
// explicit third state instead of an answer it would have to distrust.
//
// The second regex anchors the refusal at end-of-output, which is how "no
// verdict line is printed" has to be written: the runner has no negative
// patterns, every regex must match.
pragma solidity ^0.8.0;

contract Box {
    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            return 1;
        }
        return 0;
    }
}
