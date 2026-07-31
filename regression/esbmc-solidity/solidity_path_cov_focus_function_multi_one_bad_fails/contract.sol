// A --focus-function LIST in which one name is wrong must fail, and must say
// WHICH name.
//
// THE FAILURE THIS PREVENTS is specific to lists and does not exist for a single
// name. The obvious way to extend a one-name check to many is to stop at the
// first name that matches:
//
//     for (name : names) if (exists(name)) { found = true; break; }
//
// With `--focus-function a,nosuchfn` that loop succeeds on `a`, the run
// proceeds, the dispatcher and the pass both narrow to `a` alone, and the report
// is a perfectly well-formed 100% for one unit. The user asked for two units and
// got a clean, confident measurement of one, with nothing on stdout saying so.
// That is strictly worse than the pre-list behaviour, where a bad value was a
// loud CONVERSION ERROR.
//
// So EVERY name is checked and every name that matched nothing is collected into
// ONE message. Reporting only the first bad name would also be wrong in a
// smaller way: a ten-function list with three typos would take three runs to
// fix.
//
// The message is pinned to name `'nosuchfn'` and NOT `'a'` -- the point of the
// test is that the good name is absent from the complaint, and that direction is
// what a stop-at-first-failure implementation would get wrong in the other
// direction (complaining about the whole raw value `'a,nosuchfn'`, which sends
// the reader to check a spelling that is correct).
//
// The check lives in the FRONTEND (solidity_convert.cpp, at the top of
// convert()), before any GOTO program exists, which is why the expected exit is
// a conversion error rather than a coverage diagnostic. The path-coverage pass
// has its own second-line gate for a focus that matches no UNIT; that one is
// documented as currently having no reproducer, precisely because this check
// catches the reachable cases first.
pragma solidity ^0.8.0;

contract C {
    uint256 public x;

    function a(uint256 v) public {
        if (v > 1) {
            x = 1;
        } else {
            x = 2;
        }
    }

    function b(uint256 v) public {
        x = v;
    }
}
