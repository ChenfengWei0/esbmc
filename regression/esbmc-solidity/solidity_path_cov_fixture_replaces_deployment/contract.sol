// `--path-cov-fixture`: THE DEPLOYMENT LEAVES THE UNIT QUERY.
//
// The unit query and the constructor are forced to share one `--unwind`: the
// path enumeration's loop bound and the symex unwind bound MUST agree, so the
// pass installs one number for both (and says so: "no --unwind given; bounding
// symbolic execution at 4 to match the path enumeration's own loop bound").
//
// This contract's constructor pushes to a dynamic array inside a struct, which
// pulls in the C library's `__memcpy_impl` loop. MEASURED on the two PoC
// contracts this is reduced from (D03_StructWithDynArray, D04_AddressSetShape),
// same query, only the bound changing:
//
//     default bound (4)   rc=-6, `Generated 0 VCC(s)`, 0 of 3 instrumented
//                         path claim(s) reached the solver, SIGABRT, NO REPORT
//     --unwind 64         rc=1, F=3 U=0, 0.4s -- every path witnessed
//
// Raising the shared bound is not the fix: it is paid for in the UNIT's own
// enumeration, which is exponential in loop iterations, and the deployment is
// not what the unit query is about. It is also not replayable -- a symbolically
// constructed entry state is not something a Foundry test can reproduce.
//
// So the deployment is REPLACED by a recorded concrete state. The unit's guard
// reads `owner`, which only the constructor writes, so the fixture has to
// supply it or every non-revert path becomes unreachable for a second reason
// and this test would pass for the wrong cause.
//
// THE PAIR:
//   * this directory   fixture given -> no constructor call in the query at
//                      all, `owner` assigned, all paths witnessed AT THE
//                      DEFAULT BOUND
//   * solidity_path_cov_fixture_unknown_state_refused
//                      a fixture naming a state variable the contract does not
//                      have is REFUSED and says which one -- a silently skipped
//                      entry is a variable the report believes was pinned and
//                      which actually holds whatever the default initialiser
//                      left.
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
