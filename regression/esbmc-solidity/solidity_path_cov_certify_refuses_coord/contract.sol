// Stage-2 CERTIFICATION QUERY: a coordinate the tool cannot express makes the
// QUERY refuse — the opposite disposition to the outer box, deliberately.
//
// Its twin `solidity_path_cov_outer_box_refuses_coord` refuses the COORDINATE
// and keeps measuring the others, because an outer box is a per-coordinate
// containment statement and one missing coordinate costs information. Here the
// same refusal has to stop the whole query, and the reason is that the two
// stages answer different questions:
//
//     certification asks "does EVERY input in THIS box walk this path?"
//
// A box missing one of its requested bounds is a strictly WIDER box. Answering
// SUCCESSFUL about it would certify a region nobody asked about — and a caller
// reading that verdict has no way to tell, because "the box omits c" and "c is
// unconstrained" are the same constraint to the solver. Answering about a
// different box is worse than not answering.
//
// So the DISPOSITION is unchanged from before this fix — the query was always
// refused here. What changed is the MECHANISM. It used to abort: for a name
// that failed to resolve, after a readable error; for a name that resolved to
// something unboundable, as a bare SIGABRT out of the SMT layer with no message
// of ours at all. Both left a core dump where an unattended driver needed a
// datum. It is now a clean non-zero exit carrying the coordinate's name.
//
// The second regex is the part worth stating plainly: it pins that the refusal
// line is the LAST thing the run prints, i.e. that no VERIFICATION SUCCESSFUL /
// FAILED line is printed at all. That is what a caller depends on. A run that
// refused the query and still printed a verdict would be read as an answer —
// and the verdict it would plausibly print is the dangerous one, since with the
// requested bound dropped a box that does not actually separate the paths
// certifies cleanly.
//
// It has to be written as "nothing follows" rather than "no verdict line",
// because the runner has no negative patterns: every regex in a test.desc must
// MATCH. An end-of-output anchor is the positive form of that negative, and it
// is strictly stronger, so it can go red for a reason other than the one it is
// aimed at — which is the safe direction for a tripwire.
//
// One thing this run does NOT get to rely on: the banner announcing the mode
// contains the words "VERIFICATION SUCCESSFUL" inside a sentence explaining what
// the verdicts mean. A caller matching that phrase as a SUBSTRING would read
// this refusal as a success. That is the same shape as the defect which made the
// driver's certification gate permanently green, met here a second time and from
// the other side — which is why the verdict is read as a whole LINE.
pragma solidity ^0.8.0;

contract Box {
    string private _name;

    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            return 1;
        }
        return 0;
    }
}
