// THE DECISION SEQUENCE BEHIND EACH WITNESSED PATH — the field that makes path
// coverage comparable with branch coverage at all.
//
// WHY IT HAD TO BE ADDED IN THE PRODUCER. `path_id` is `enc`, and `enc` is a
// pure bit accumulator: `enc_0 = 1`, `enc_{k+1} = 2*enc_k + bit`. The ARMS
// decode arithmetically from `(enc, depth)`, but NO source location is mixed
// into it, and the bit->site mapping is path-dependent (bit 3 of one path and
// bit 3 of a sibling come from different instructions once their prefixes
// diverge). So no consumer of cov-report.json could recover WHICH decisions a
// path walks. Without that, the witnessed path set cannot be projected onto
// decisions, and the two coverage metrics share no denominator.
//
// WHAT THIS FIXTURE PINS, and why each part is here:
//
//   * `require(v != 7)` — a REVERT decision. Also the case that proved the
//     `branch_claim` text is NOT a join key: under --solidity-path-coverage the
//     revert-observation gate lowers a require to a guard one `not` deeper than
//     branch coverage's, so the same decision reads "!(!(v != 7))" here and
//     "!(v != 7)" there. A plain `if` agrees verbatim. A projection joining on
//     text would therefore work on `if`s and silently drop every `require` —
//     lower number, no error. The projection joins on file+line instead, which
//     is also the comparison metric's own unit.
//   * `if (v > 10)` — a plain decision, which DOES agree verbatim.
//   * `g()`, an internal call — its decision appears inside `f`'s sequences
//     (physical inlining), tagged with its own `function`, so the many-to-one
//     projection is visible rather than inferred.
//   * every unit is non-payable, so every path carries the SYNTHESISED ABI
//     value gate as its first decision. That gate has no branch-coverage
//     counterpart AND its location is copied from the unit's first body
//     instruction, so a consumer matching on location alone would credit itself
//     with whatever real decision sits on that line. It is counted separately
//     on the summary line for exactly that reason.
//
// THE NUMBER THAT MATTERS IS `published for N of N`. An F carrying no sequence
// is a silent hole in any comparison built on this field, so the two counts are
// pinned together: if the recorder stops firing the first drops while the
// second does not, and the line goes red. `unrecorded 0` is pinned for the same
// reason — a missing prefix key would otherwise shorten a sequence, which reads
// as "this path walks fewer decisions" (a claim about the path) when it is a
// claim about the recording.
pragma solidity ^0.8.0;

contract D {
    uint256 public x;

    function g(uint256 v) internal {
        if (v > 100) {
            x = 3;
        }
    }

    function f(uint256 v) public {
        require(v != 7);
        if (v > 10) {
            x = 1;
        } else {
            x = 2;
        }
        g(v);
    }
}
