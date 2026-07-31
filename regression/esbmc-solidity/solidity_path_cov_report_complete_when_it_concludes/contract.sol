// THE MUST-NOT-FIRE HALF OF THE PARTIAL MARKER.
//
// A PARTIAL MARKER THAT IS ALWAYS SET IS WORTH NOTHING. Its two partners
// (solidity_path_cov_partial_report_on_oom, ..._on_signal) assert that a run
// which dies says so; this one asserts that a run which does NOT die says the
// opposite, on the same contract, with the same four paths, all four witnessed.
//
// It is not a formality. The marker is written into the same cov-report.json a
// complete run produces, so if it ever stuck ON, every complete report in this
// project would be discarded by its own consumers -- report_summary.py prints
// "** PARTIAL REPORT -- NOT A MEASUREMENT **" and branch_gate.py appends
// "(partial)" to the verdict. That is the mirror-image failure of the one the
// marker exists to prevent, and it is exactly as damaging: this project has
// already shipped a gate whose answer was true on every input.
//
// Three things are pinned, in both directions:
//
//   * `Report Completeness: COMPLETE` is PRESENT. The line is emitted
//     unconditionally rather than only when something is wrong, because a
//     marker that appears only on failure is indistinguishable, to a consumer
//     that has not been taught about it, from a marker that was forgotten.
//   * `Report Completeness: PARTIAL` is ABSENT anywhere in the output.
//   * `Path Status: F 4, I 0, U 0` -- so "not partial" cannot be explained away
//     as "this run did nothing". It solved every claim it had.
//
// The pair therefore establishes that the marker tracks whether the job loop
// finished, and nothing else.
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
