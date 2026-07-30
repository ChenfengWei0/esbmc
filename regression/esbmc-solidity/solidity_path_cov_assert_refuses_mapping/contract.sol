// STAGE 3 -- a mapping is REFUSED BY NAME, not dropped.
//
// `total` is a component of the contract instance object and gets the full
// six-rung ladder. `balances` is NOT a component at all: the frontend lowers a
// mapping to a contract-scope global, so iterating the object's components
// would leave it out of the report entirely.
//
// THAT OMISSION WOULD NOT BE A SMALLER ANSWER, IT WOULD BE A WRONG ONE. In this
// mode a state variable with no row reads as one that needed no assertion --
// i.e. as one that does not change -- and this path writes it. Same reason
// path_ce_t::state_written_unrendered exists: "omitting them entirely would let
// a consumer infer 'unchanged', which is a silent wrong conclusion."
//
// So the test asserts the PRESENCE of the refusal line, not merely the
// candidate count. Delete the second scan and the count line still reads
// `6 candidate(s)` and looks perfectly correct; only the refusal line goes.
pragma solidity ^0.8.0;

contract MapC {
    mapping(address => uint256) balances;
    uint256 total;

    function dep(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            balances[msg.sender] = balances[msg.sender] + a;
            total = total + a;
            return 1;
        }
        return 0;
    }
}
