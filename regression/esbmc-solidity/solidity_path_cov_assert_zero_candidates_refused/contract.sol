// STAGE 3 -- N1, entry condition (b): EVERY state variable was refused.
//
// The only state this contract has is a mapping, which the frontend lowers to a
// contract-scope global rather than a component of the contract object -- so
// not one scalar post-state candidate can be formed. Zero assertions are
// emitted, nothing is checked, and the run would print VERIFICATION SUCCESSFUL
// with exit 0: the SAME OUTPUT a completely successful ladder produces.
//
// This is N1's other half. Its twin `..._empty_vars_refused` closes the case
// where the SPEC names nothing; this one closes the case where the CONTRACT
// offers nothing. One symptom, two entry conditions, two messages -- closing
// only one leaves a run whose output is identical to a fix, which is the whole
// reason they are counted by entry condition and not by symptom.
//
// The message must send the reader to the right place: here the spec is fine
// and the contract is the explanation, so it names the refused variables.
pragma solidity ^0.8.0;

contract OnlyMap {
    mapping(address => uint256) balances;

    function dep(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            balances[msg.sender] = a;
            return 1;
        }
        return 0;
    }
}
