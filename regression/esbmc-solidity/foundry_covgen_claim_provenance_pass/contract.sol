// Every emitted test case must name the verification obligation it was
// reconstructed from, and that obligation must be the one this counterexample
// REFUTED.
//
// WHY IT MATTERS. Until the emitter carried this line, a generated suite could
// not be checked against the report it came from: each case was a bare
// `test_cov_N` with a call and a comment. That is not a cosmetic gap.
// "Verifier-derived" is a claim about PROVENANCE, and a test that cannot name
// its obligation makes the claim unfalsifiable.
//
// It is also what made a real mis-attribution undecidable. On 1inch aqua the
// whole-contract run witnessed 15 obligations across 6 units and emitted 4
// cases naming 3; `pull` was witnessed under exactly the same path ids as the
// focused run and appeared nowhere. Whether its counterexamples were DROPPED or
// RENAMED could not be told from the artifact. With this line it took one run:
// the cases carrying `pull` claims emitted `ship(...)` calls -- renaming.
//
// WHAT THIS FIXTURE PINS, and what it does NOT:
//
//   * pins: the `// claim:` line exists, names a complete-path obligation, and
//     names the unit whose method the case actually calls. `caller`'s cases
//     carry `caller` claims and `pub`'s carry `pub` claims -- swap the
//     attribution and this goes red.
//   * pins: only REFUTED claims are named. The first version of the provenance
//     code recorded every guard-TRUE claim, which on one model includes the many
//     that simply HOLD, and produced a case labelled with six obligations across
//     three units -- a label that says nothing about where the case came from.
//   * does NOT pin the aqua fix itself. That defect needs a claim carrying NO
//     source location (aqua's carry none: the solver line reads
//     `'pull:path:63 at'` with nothing after `at`), which is why the emitter now
//     attributes from the claim IDENTITY rather than from its location. These
//     claims have locations, so this fixture cannot exhibit it -- the regression
//     suite covering the shapes we thought of, stated rather than papered over.
//     The evidence for that fix is the aqua before/after in
//     notes/emitter-attribution.md.
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
