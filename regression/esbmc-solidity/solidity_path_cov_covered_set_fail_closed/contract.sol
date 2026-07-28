// THE CROSS-RUN COVERED SET MUST FAIL CLOSED, and this test exists because the
// whole mechanism was dead for an unknown length of time with nothing noticing.
//
// Background, so the next person does not have to rediscover it. Complete-path
// coverage keys its cross-run cover on a CONTENT-ADDRESSED stable id (the
// decision sequence), guarded by a fingerprint over everything that can change
// what a path IS: source bytes, key schema, decision-set version, the
// short-circuit cap, the loop / call / re-entry bounds, the goal cap and
// --contract. On a mismatch the file is DISCARDED WHOLE and recomputed — with no
// migration, deliberately, because migration logic is exactly where a wrong
// "already covered" would hide.
//
// When that content-addressed scheme replaced the older ordinal keys, the
// write-back in bmc.cpp was not switched with it: it still called the BRANCH
// metric's writer, gated on a variable complete-path coverage never sets. So the
// file was never written, and the "already witnessed" test read an always-empty
// set — meaning that even if a file HAD existed, every carried-over path would
// have been reported U, i.e. a path with a counterexample in hand filed under
// "we could not decide". Both directions broken, no crash, no warning.
//
// The lesson pinned here is a discipline, not just a behaviour: A REGRESSION MUST
// OBSERVE THE ARTIFACT, not merely that the program did not crash. This test
// hands the tool a covered-set file and asserts on what the tool does with it.
//
// `cov_stale.json` carries a syntactically valid cover with a deliberately WRONG
// fingerprint. Expected: the tool says so, names both fingerprints, carries over
// NOTHING, and instruments the full 8 paths — identical to a first run.
//
// Two properties make this the robust half of the pair:
//   * it can never rot. The paired "happy path" test would need a file whose
//     fingerprint MATCHES, and the fingerprint covers the source bytes, so
//     editing even a comment in the contract would break it. This one requires a
//     mismatch, so any edit keeps it valid.
//   * it covers the more dangerous direction. Silently reusing a stale cover
//     marks paths as covered that nothing has covered — the failure the
//     fail-closed rule exists to prevent.
//
// `--skip-bmc` is load-bearing, not incidental: the run stops after
// instrumentation, so it never reaches the end-of-run write-back that would
// OVERWRITE this fixture with a valid cover. Without it the test destroys its
// own input — the second ctest run would find a matching fingerprint, skip 7
// paths, and go red. Everything this test asserts happens before BMC anyway.
//
// Still NOT covered, and recorded rather than glossed: that the skip actually
// works on a MATCHING fingerprint (round 2 instruments fewer paths while the
// coverage percentage is unchanged). That needs two runs sharing one file, and
// test.desc describes a single invocation with a fixed tool binary — there is no
// hook for a wrapper script. Verified by hand — round 1 wrote 7 ids, round 2
// instrumented 1 path and still reported 8 / 7 / 87.5% — but it is unpinned,
// which is precisely how the original defect survived.
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
