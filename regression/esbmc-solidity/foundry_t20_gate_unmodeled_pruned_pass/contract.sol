// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;
interface Vm {
    function warp(uint256) external;
    function fee(uint256) external;      // declared but UNMODELED by ESBMC
}
abstract contract Test {
    Vm internal constant vm = Vm(0x7109709ECfa91a80626fF3989D68f67F5b1DD12D);
}
contract FeeTest is Test {
    function test_fee_unmodeled() public {
        vm.fee(7);
        assert(block.basefee == 7); // fee unmodeled → basefee nondet → false FAILED (taint anchor)
    }
}
