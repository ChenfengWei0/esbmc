// ENTRY STORAGE as a box coordinate, and the limit that comes with it.
//
// `f`'s guard is `bal >= amt`: one parameter, one STATE variable, tied together
// — the shape a parameter-only box could say nothing about, since the region it
// certified would be a statement about the parameter axis only while the path
// actually taken still depended on state the box never mentioned.
//
// SCOPE, and it is narrower than it looks: `state.<field>` covers SCALAR state
// variables only. A mapping or dynamic array is not a field of the contract
// object — the frontend lowers those to contract-scope globals — so
// `state.balances` does not resolve at all (MEASURED: the run aborts by name).
// The commonest real guard, `balances[msg.sender] >= amt`, is therefore NOT
// covered by this test or by the feature it pins; supporting it needs a
// coordinate that denotes a SLOT, which is a design question rather than a
// lookup change. Failing by name is the right failure — a silently dropped
// bound would certify a wider region than the one asked for — but this test
// must not be read as evidence for the mapping case.
//
// `set` exists so the entry state can vary at all: with ONE transaction the
// entry state is whatever the constructor left, so `bal` is the constant 0 and
// the coordinate is degenerate. Hence --solidity-max-tx 2.
//
// WHAT THIS HALF MEASURES: with both coordinates free, the two path domains are
// the two sides of a DIAGONAL (`amt <= bal` and `amt > bal`). No box contains
// either of them tightly, so both outer boxes come back as the whole type range
// on both coordinates, and no single-coordinate cut can separate them. Both
// paths are therefore reported as unseparated.
//
// That warning is the result, not a failure. Keeping either region would ship
// an interval provably containing the other path's inputs. The `_pinned` twin
// shows the prescribed way out — pin all but one coordinate and the problem is
// one-dimensional again, where the interval is exact — so the pair is the test:
// this half shows the box cannot express a diagonal, that half shows what to do
// about it.
pragma solidity ^0.8.0;

contract St {
    uint256 public bal;

    function set(uint256 v) external payable {
        bal = v;
    }

    function f(uint256 amt) external payable returns (uint256) {
        if (bal >= amt) {
            return 1;
        }
        return 0;
    }
}
