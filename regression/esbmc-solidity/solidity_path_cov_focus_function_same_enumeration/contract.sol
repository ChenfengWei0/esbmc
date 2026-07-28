// The enumeration must not depend on which entry the harness dispatcher calls.
//
// This matters because the path-count distribution used to justify degradation
// was measured under the FULL dispatcher, while the locked benchmark collector
// drives real contracts per-method with `--focus-function`. If narrowing the
// dispatcher also narrowed the enumeration, that measurement would have been
// taken under a configuration that does not match deployment and every number
// derived from it would be suspect.
//
// It should not, because enumeration is a static DFS over the goto program at
// instrumentation time, whereas `--focus-function` only changes which entry the
// harness invokes. "Should not" is an inference, so it is pinned as a test:
//
//   this test          -- `--focus-function pub`
//   internal_call_expands -- same contract, full dispatcher
//
// BOTH pin `instrumented 8 complete path(s) across 2 unit(s)` and the identical
// distribution line. Diverge and one of them goes red.
//
// What legitimately DOES differ is reachability, and the numbers here record
// that too: the full dispatcher witnesses 7 of 8 paths, focusing on `pub`
// witnesses 3 — `caller`'s paths become U because nothing calls it. That is the
// per-method mode working as intended, and it is exactly why the benchmark
// collector unions the covered set across one run per method.
//
// This test is ALSO the live case for the `unit-not-entered` U reason token,
// and for its PRIORITY. `caller`'s 5 paths have no solver verdict at all, so a
// naive ordering files them under `not-solved-this-run` — which is what the tool
// did until this was pinned, on a run whose own log line one line earlier said
// the unit had not been entered. The token has to sit directly under
// `named-obstacle` and above every verdict-derived token, because when nothing
// ran, no classification of the path itself means anything. Pinning the whole
// five-slot line makes both the count and the ORDER decisive: swap two slots and
// this goes red.
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
