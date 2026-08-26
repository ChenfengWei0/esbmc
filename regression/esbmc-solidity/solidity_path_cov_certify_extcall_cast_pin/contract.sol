// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// `extcall.<local>` names a local assigned from an external call's nondet
// return.  When the source converts the return on the spot the assignment is
// `p = (uint256)(NONDET(int256))` -- a typecast around the nondet -- and the
// coordinate used to be refused ("no ASSIGN of a NONDET value").  The bound
// sits on the local itself, so the cast changes nothing: pinned to 1, the
// division below cannot panic and the path certifies.
interface IOracle { function latestAnswer() external view returns (int256); }
contract Priced {
    IOracle public immutable oracle;
    constructor(IOracle o) { oracle = o; }
    function toWei(uint256 amount) external view returns (uint256) {
        uint256 p = uint256(oracle.latestAnswer());
        return (amount * 1e8) / p;
    }
}
