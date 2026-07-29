// Stage-2 OUTER BOX: a coordinate the tool CANNOT EXPRESS is refused by name,
// and the round keeps going.
//
// This is the must-flip half of the B1 fix. Before it, asking for a bound on a
// coordinate that resolves to something other than a bit-vector reached the SMT
// layer and died there — `Projecting from non-tuple based AST`, or the
// `Tuple AST mismatch` assertion — i.e. SIGABRT. Unattended that turns a fact
// worth recording ("this coordinate is outside the current coordinate set")
// into a core dump, and the whole batch measures NOTHING: measured on three
// separate real projects, on three DIFFERENT types (a mapping, a string, a
// calldata struct), with the identical shape each time.
//
// `_name` is a string, so `state._name` RESOLVES (it is a component of the
// contract object) and then cannot be bounded. That combination is the one the
// old code could not survive: a name that fails to resolve at least reached a
// readable error first.
//
// What this test pins:
//   * the run does NOT abort — it completes and reports;
//   * the refused coordinate is named, with a reason, BOTH where it is refused
//     and again in the report;
//   * no probe is emitted for it, so it appears in no box;
//   * `a` is measured exactly as it would have been on its own.
//
// The last two together are the property that matters. Omitting a coordinate
// from a box and bounding it by its whole type are the SAME constraint to the
// solver, so the tempting implementation — pass `[0, 2^256-1]` through and carry
// on — costs nothing at the query and is wrong at the reader: it attributes a
// MEASURED bound to a coordinate nothing was measured on, and every region
// quoting it is then wider than anything that was established. The refusal has
// to stay visible precisely because the solver cannot tell the difference.
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
