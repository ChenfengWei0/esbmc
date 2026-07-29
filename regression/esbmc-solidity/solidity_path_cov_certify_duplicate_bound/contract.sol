// Stage-2 CERTIFICATION QUERY: one coordinate bounded TWICE is refused, because
// the emptiness test cannot see an emptiness that exists only in the conjunction.
//
// The twin `_certify_empty_box` catches `lo > hi` on a single bound. This is the
// hole in that test, closed in the same place: `a in [11,100]` and
// `a in [200,300]` are each perfectly well-formed and their conjunction is
// empty. A per-bound check waves both through, both assumes are emitted, the
// entry assumption is unsatisfiable, and the run certifies vacuously — arriving
// at exactly the false certificate the twin exists to prevent, by a route the
// twin does not cover.
//
// This test exists because of a pattern that showed up three times in one night:
// a check that classifies by a list is OPEN AT THE BOTTOM unless someone asks
// what happens to the cases the list does not name. The permanently-green
// certification gate was a string whitelist; the failure classifier written
// hours later TO FIX IT was a message whitelist, and a third cause walked
// straight through it. So the question was asked here before it had to be: what
// would make the box empty that `hi < lo` does not see?
//
// Refusing duplicates rather than intersecting them is deliberate. Two bounds on
// one name carry no meaning in this spec, so refusing costs nothing, and it
// leaves NO case in which "not empty by this test" and "not empty" come apart.
// Intersecting would instead put a second piece of arithmetic between the
// request and the query, which is precisely where this class of error lives.
//
// The branch is EXERCISED here rather than argued for. Its sibling is exercised
// by the twin. A branch that only ever gets reasoned about is the shape that has
// already cost this project a night.
pragma solidity ^0.8.0;

contract Box {
    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            return 1;
        }
        return 0;
    }
}
