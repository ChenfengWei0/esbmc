// `--all-witnesses` WAS FULLY WIRED AND REACHED EVERY CONSUMER BUT THIS ONE.
//
// Per witness, the enumeration loop already emitted a `--cex-output` file, a
// GraphML and a YAML witness, a testcase XML, an HTML and a JSON report, a
// pytest case, a ctest case and a FOUNDRY case. Then one line --
// `if (is_path_cov && witnesses.empty())` -- harvested the counterexample
// payload for the FIRST witness only and discarded the rest. So Foundry got N
// tests per path while cov-report.json got one set of inputs, which is exactly
// backwards for a pipeline whose stage 2 reads the report.
//
// The line was documented as a design decision ("one CE per complete path is
// what the report contracts for"). The contract WAS the limitation; it was not
// a reason for it.
//
// THEY ARE NEARLY FREE, which is why this is worth doing rather than
// approximating. N witnesses cost N-1 further `dec_solve()` calls on ONE
// already-encoded solver instance: one `push_ctx`, a blocking clause over the
// nondet input tuple per witness, one `pop_ctx`. No second encoding, no second
// symex, no second per-claim slice.
//
// AND THEY ARE THE RAW MATERIAL THE NEXT STAGE NEEDS. A sibling span is built
// from more than one point of a path's input domain; one counterexample cannot
// bracket a boundary, and the stage-2 outer-box ladder had to be handed its
// span out of band because the report could not supply a second point.
//
// WHAT IS PINNED. The census on stdout is computed from the EMITTED claims
// array rather than from the producer's own map -- this pass has already
// shipped a recorder that ran on every path and was consumed by nothing, and a
// census taken from the producer would have looked healthy the whole time. The
// regex requires the witness total to EXCEED the number of witnessed paths and
// requires at least one path to carry more than one, so a build that emitted
// the new field while still harvesting once cannot satisfy it.
//
// OPT-IN IS PART OF THE CLAIM: see the paired
// solidity_path_cov_single_witness_by_default, same contract, no flag.
pragma solidity ^0.8.0;

contract D {
    uint256 public x;

    function g(uint256 a) public {
        require(a != 0);
        if (a > 100) {
            x = 1;
        } else {
            x = 2;
        }
    }
}
