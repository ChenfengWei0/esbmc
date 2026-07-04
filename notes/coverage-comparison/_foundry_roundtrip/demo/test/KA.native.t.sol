// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {KA} from "../src/KA.sol";

// Stand-in for the project's own hand-written suite: it only covers the
// v>100 TRUE branch of setBig, missing the else branch (like a real suite
// that ESBMC's coverage run showed reaches fewer branches).
contract KANativeTest is Test {
    KA c;
    function setUp() public { c = new KA(); }
    function test_native_big() public {
        c.setBig(200);            // v > 100  -> TRUE branch only
        assertEq(c.hi(), 200);
    }
}
