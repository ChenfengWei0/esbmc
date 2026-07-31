// ONE PATHOLOGICAL QUERY MUST NOT COST THE WHOLE RUN.
//
// MEASURED, on St1inch.setFeeReceiver: 275 paths instrumented, 10 VCCs, and the
// run spent ~166 of its 180 s inside the FIRST solver query, which never
// returned. The other nine claims were never asked, the run was killed from
// outside, and it produced nothing at all. A few hundred MB were in use against
// 40 GB free -- this was never a memory problem and never a path-count problem.
//
// That is the case against every mitigation tried before it. Raising --memlimit
// and lengthening the outer timeout route AROUND a query that does not finish;
// keeping partial results makes the loss cheaper without making it smaller.
// Path coverage decides one INDEPENDENT claim per job, so the right bound is
// per claim, and there was none.
//
// WHAT THIS FIXTURE IS. `hard` asks the solver to prove that no pair of
// 128-bit factors multiplies to a fixed 256-bit constant. Bit-blasted 256-bit
// multiplication is where bit-vector solvers are worst, and the bounds make it
// an UNSAT obligation rather than a search that gets lucky -- so it reliably
// outlives a 1 s budget without needing a specific solver version to be slow in
// a specific way. `easy` is next to it precisely so the run has something to
// decide AFTER abandoning the expensive claim.
//
// WHAT IS PINNED, and why each line is load-bearing:
//
//   * `claim-budget-exceeded N` with N >= 1, read off the U-reason breakdown.
//     Its own token, and the whole line is matched with the zeros in it so the
//     other five buckets are asserted to be empty. It is NOT `solver-unknown`
//     (that is the solver answering "I do not know" -- information), NOT
//     `bounded-holds` (it answering "no witness"), NOT `not-solved-this-run`
//     (never asked), and NOT `run-died-before-solving` (the run did not die).
//     We asked, the solver was still working, and WE stopped it: nothing at all
//     is known about that path, and the fix is a bigger budget rather than a
//     different bound or a different query.
//
//   * `Report Completeness: COMPLETE`. THIS IS THE PROOF THAT THE RUN
//     CONTINUED. The marker is only COMPLETE when the per-claim job loop ran to
//     its end, so a build that abandoned the query by dying -- or by stopping
//     the loop -- cannot produce it. It is a stronger statement than counting
//     the surviving claims, because it is a fact about the loop rather than
//     about how many claims happened to be cheap.
//
//   * `F 1` and the `Claim Budget:` line. The first says a claim really was
//     decided and witnessed on the same run in which another was abandoned; the
//     second says the budget was applied and BY WHAT -- a budget the tool
//     accepted and could not enforce would otherwise print an unenforced number
//     and read exactly like an enforced one.
//
// The budget is set to 1 s here only so the fixture is fast; the shipped
// default is 120.
pragma solidity ^0.8.0;

contract T {
    uint256 public x;

    function hard(uint256 a, uint256 b) public {
        require(a > 1);
        require(b > 1);
        require(a < 0x100000000000000000000000000000000);
        require(b < 0x100000000000000000000000000000000);
        if (a * b == 0xB0F2E1A75C4D3E9F8A6B5C4D3E2F1A0B9C8D7E6F5A4B3C2D1E0F9A8B7C6D5E4F) {
            x = 1;
        } else {
            x = 2;
        }
    }

    function easy(uint256 c) public {
        if (c > 5) {
            x = 3;
        } else {
            x = 4;
        }
    }
}
