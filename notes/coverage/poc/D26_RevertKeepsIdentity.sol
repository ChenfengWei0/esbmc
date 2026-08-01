// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// DOES A REVERT ERASE THE PATH IDENTITY ACCUMULATED BEFORE IT?
///
/// The question, asked precisely: the enumeration identifies a complete path by
/// `tr == enc && cnt == depth`, where `tr` is built one bit per decision. A
/// Solidity `revert` undoes every state modification the transaction made. If
/// `tr` lived in that state, a path that records three decisions and THEN
/// reverts would arrive at its exit assert with `tr` reset to its entry value --
/// the claim could never match, the path would come back U, and the report would
/// read exactly like "the solver could not decide these".
///
/// That failure would be invisible in the aggregate: reverting paths are common,
/// U is common, and nothing distinguishes "undecided" from "identity erased".
///
/// This contract makes the two readings produce DIFFERENT, countable outputs.
/// `f` has three decisions before a fourth that reverts:
///
///     d1  a > 0
///     d2  b > 0
///     d3  c > 0
///     d4  a + b + c > 100   -> require, so the FALSE arm reverts
///
/// So there are paths of depth 4 whose last bit is the reverting arm, and paths
/// of depth 4 that exit normally. `g` is the CONTROL: one decision, no revert
/// anywhere, so it must be witnessed under any hypothesis. A run where `g` also
/// fails to be witnessed measured nothing about reverts.
///
/// READINGS, FIXED BEFORE THE RUN:
///
///   A  the reverting paths are enumerated AND witnessed (F), with depth 4
///        -> `tr` is NOT in the rolled-back state; identity survives the revert;
///           no rollback modelling is needed for the accumulator.
///   B  the reverting paths are enumerated but ALL come back U, while `g` and
///      the normal-exit paths of `f` are F
///        -> consistent with the identity being erased at the revert. NOT proof
///           on its own -- it is also what a solver limit looks like -- so the
///           next step would be to read the claim, not to conclude.
///   C  the reverting paths are not enumerated at all
///        -> a different defect: the revert arm is not a path exit here, which
///           contradicts the three-state exit census.
contract D26 {
    uint256 public sink;

    function f(uint256 a, uint256 b, uint256 c) external {
        uint256 t = 0;
        if (a > 0) {
            t += 1;
        }
        if (b > 0) {
            t += 2;
        }
        if (c > 0) {
            t += 4;
        }
        require(a + b + c > 100, "too small");
        sink = t;
    }

    // CONTROL. No revert on any path. If this is not witnessed the run measured
    // nothing, and the reverting paths' status says nothing about reverts.
    function g(uint256 x) external {
        if (x > 5) {
            sink = 1;
        } else {
            sink = 2;
        }
    }
}
