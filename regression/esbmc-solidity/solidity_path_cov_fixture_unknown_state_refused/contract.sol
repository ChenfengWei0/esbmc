// The refusal half of the `--path-cov-fixture` pair (see
// solidity_path_cov_fixture_replaces_deployment for the positive one and for
// the measurement that motivates the option).
//
// A fixture entry naming a state variable the contract does not have must be a
// HARD FAILURE that says WHICH one. Silently skipping it is the worse outcome
// by far: the deployment has already been dropped, so the variable holds
// whatever the default initialiser left, while the fixture file, the run's own
// log and every downstream report all say it was pinned. Nothing anywhere
// distinguishes that from a fixture that worked.
//
// The contract is identical to the positive test's so the two differ in exactly
// one thing -- the fixture -- and this test cannot pass because of some other
// property of the source.
pragma solidity ^0.8.20;

contract Depl {
    struct Data {
        uint256[] raw;
    }

    address public owner;
    address public feeReceiver;
    Data internal items;

    constructor() {
        owner = msg.sender;
        items.raw.push(7);
    }

    function setFeeReceiver(address r) external {
        require(msg.sender == owner, "not owner");
        feeReceiver = r;
    }
}
