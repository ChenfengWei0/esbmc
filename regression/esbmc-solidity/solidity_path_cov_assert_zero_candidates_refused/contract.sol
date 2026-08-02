// STAGE 3 -- N1, entry condition (b): EVERY candidate was refused.
//
// The only state this contract has is a mapping, which the frontend lowers to a
// contract-scope global rather than a component of the contract object -- so
// not one scalar post-state candidate can be formed. Zero assertions are
// emitted, nothing is checked, and the run would print VERIFICATION SUCCESSFUL
// with exit 0: the SAME OUTPUT a completely successful ladder produces.
//
// ---- WHY dep RETURNS NOTHING, AND WHY THAT IS THE FIXTURE ----
//
// It used to be "returns (uint256)" with "return 1; / return 0;". Once the
// ladder gained RETURN-VALUE rungs that stopped testing what this directory is
// for: the unit's own return value IS a candidate, three rungs were emitted,
// the gate correctly did not fire, and the fixture would have been "fixed" by
// relaxing its expectation -- i.e. by deleting the property it exists to pin.
// The premise the gate needs is "this unit offers NO candidate of any kind",
// and a value-returning unit does not satisfy it. So the unit is now void,
// which RESTORES the premise instead of weakening the check.
//
// This is N1's other half. Its twin `..._empty_vars_refused` closes the case
// where the SPEC names nothing; this one closes the case where the CONTRACT
// offers nothing. One symptom, two entry conditions, two messages -- closing
// only one leaves a run whose output is identical to a fix, which is the whole
// reason they are counted by entry condition and not by symptom.
//
// The message must send the reader to the right place: here the spec is fine
// and the contract is the explanation, so it names the refused candidates AND
// the reason for each. That gate exits BEFORE solving, so the per-candidate
// table and the "carry NO candidate" warning that normally carry the reasons
// are never reached -- a bare list of names would leave the reader guessing
// which refusal applied to which name.
pragma solidity ^0.8.0;

contract OnlyMap {
    mapping(address => uint256) balances;

    function dep(uint256 a) external payable {
        if (a > 10) {
            balances[msg.sender] = a;
        }
    }
}
