// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Test} from "forge-std/Test.sol";
import {stdError} from "forge-std/StdError.sol";

// WHICH ARITHMETIC OPERATIONS ACTUALLY PANIC ON CHAIN -- ANSWERED BY THE CHAIN,
// NOT BY THE DOCUMENTATION AND NOT FROM MEMORY.
//
// WHY THIS EXISTS. `--path-cov-arith-resolve` proves a path is reachable only
// through a checked-arithmetic revert, and task #25 wants to render that as
// `vm.expectRevert(stdError.arithmeticError)` rather than refusing the case. To
// name the panic code it has to attribute the UNSAT to a specific operation, and
// the discriminator on offer was `location.property() == "overflow"`.
//
// That discriminator does not work, and finding out why is what produced this
// file. `property() == "overflow"` has THREE producers live in a Solidity
// path-coverage run:
//
//   goto_check.cpp:373-378    "arithmetic overflow on ..."      (--overflow-check)
//   goto_check.cpp:319-320    "Narrowing cast overflow on ..."  (--narrowing-check)
//   goto_check.cpp:1252-1261  "Narrowing assignment overflow"   (plain --overflow-check)
//
// and `shift_check` (goto_check.cpp:604-609) routes LEFT SHIFTS through
// `overflow_check`, so `a << b` stamps `overflow` too.
//
// So a singleton `{"overflow"}` set can be composed entirely of claims whose
// operations may not Panic on chain at all. Stamping them 0x11 would render
// `vm.expectRevert(stdError.arithmeticError)` on a call that does not revert --
// a RED test on the unmodified contract, which is the one outcome this pipeline
// must not produce. Which producers deserve a panic stamp is therefore a
// SOLIDITY SEMANTICS question, and this project does not answer those from
// memory.
//
// THE POSITIVE CONTROLS ARE NOT OPTIONAL. "No panic observed" and "the test
// never ran" look identical, and this project has shipped a green test that
// asserted nothing more than once. `test_add_*` and `test_div_*` must FAIL if
// removed from the expectRevert form -- they are what proves the harness can see
// a panic at all.
//
// Run:
//   cd regression/foundry-harness && forge test --match-contract D19
//
// ---- MEASURED 2026-08-01, forge 1.7.1, solc 0.8.34, evm cancun. 7/7 PASS ----
//
//   test_add_overflow_panics_0x11        PASS   <- control fires
//   test_div_by_zero_panics_0x12         PASS   <- control fires
//   test_negMin_panics_0x11              PASS   <- control fires
//   test_shl8_does_not_panic             PASS   200 << 1 == 144
//   test_shl256_does_not_panic           PASS
//   test_narrowCast_does_not_panic       PASS   uint8(300) == 44
//   test_narrowAssign_does_not_panic     PASS
//
// SHIFTS AND NARROWING DO NOT REVERT ON CHAIN. They truncate, silently. Only
// genuine checked arithmetic (+ - * / % and unary negation) Panics.
//
// ---- WHAT THAT MEANS FOR THE VERIFIER, AND IT IS WORSE THAN A LABELLING BUG --
//
// `--path-cov-arith-resolve` converts EVERY assert whose `property()` is
// "overflow" or "division-by-zero" into an ASSUME. A narrowing claim caught by
// that filter makes the query assume "this conversion does not truncate". If the
// path REQUIRES the truncation, the conjunction is UNSAT -- and the code reads
// that UNSAT as a PROOF that the path is reachable only through a
// checked-arithmetic revert, and REFUSES to emit the case.
//
// On chain that conversion truncates and the path is perfectly reachable. So the
// proof is FALSE and a good test case is discarded. Not a red test -- a silently
// missing one, which is the harder kind to notice, because a refusal that is
// counted still reads as the mechanism working.
//
// The fix is therefore NOT "label the panic code at the consumer". It is to stop
// converting the claims that do not correspond to a revert at all: stamp the
// panic kind at the PRODUCER (`add_guarded_claim`, goto_check.cpp:1023-1028) and
// let the re-solve convert only stamped claims. Unstamped = not a revert = leave
// it as an assert. Fail-closed.
//
// WHAT THIS FILE DOES NOT ESTABLISH: whether a narrowing claim is actually
// PRESENT under plain `--overflow-check` in a path-coverage run. That is a fact
// about goto_check's gating, not about Solidity, and it decides whether the
// defect above is live or latent. It has to be measured with ESBMC, separately.
contract D19_Subject {
    // POSITIVE CONTROL: Solidity >=0.8 checks + and reverts Panic(0x11).
    function add(uint8 a, uint8 b) external pure returns (uint8) {
        return a + b;
    }

    // POSITIVE CONTROL: division by zero reverts Panic(0x12).
    function div(uint8 a, uint8 b) external pure returns (uint8) {
        return a / b;
    }

    // QUESTION 1: does a left shift that loses high bits panic?
    function shl8(uint8 a, uint8 b) external pure returns (uint8) {
        return a << b;
    }

    function shl256(uint256 a, uint256 b) external pure returns (uint256) {
        return a << b;
    }

    // QUESTION 2: does an explicit narrowing cast panic?
    function narrowCast(uint256 x) external pure returns (uint8) {
        return uint8(x);
    }

    // QUESTION 3: the same narrowing, but as an ASSIGNMENT to a narrower
    // variable -- the shape ESBMC calls "Narrowing assignment overflow".
    function narrowAssign(uint256 x) external pure returns (uint8) {
        uint8 v = uint8(x);
        return v;
    }

    // QUESTION 4: signed negation of the minimum, the other classic 0x11.
    function negMin(int8 a) external pure returns (int8) {
        return -a;
    }
}

contract D19_PanicSemantics is Test {
    D19_Subject internal s;

    function setUp() public {
        s = new D19_Subject();
    }

    // ---- positive controls: the harness CAN see a panic ----

    function test_add_overflow_panics_0x11() public {
        vm.expectRevert(stdError.arithmeticError);
        s.add(200, 200);
    }

    function test_div_by_zero_panics_0x12() public {
        vm.expectRevert(stdError.divisionError);
        s.div(1, 0);
    }

    // ---- the questions. Each asserts the WRAPPED/TRUNCATED value, so if the
    // operation did panic the test fails loudly with the panic rather than
    // silently passing. ----

    // 200 << 1 = 400; low 8 bits = 144.
    function test_shl8_does_not_panic() public view {
        assertEq(s.shl8(200, 1), 144);
    }

    // (2^256 - 1) << 1 keeps the low 256 bits = 2^256 - 2.
    function test_shl256_does_not_panic() public view {
        assertEq(s.shl256(type(uint256).max, 1), type(uint256).max - 1);
    }

    // 300 mod 256 = 44.
    function test_narrowCast_does_not_panic() public view {
        assertEq(s.narrowCast(300), 44);
    }

    function test_narrowAssign_does_not_panic() public view {
        assertEq(s.narrowAssign(300), 44);
    }

    // -(-128) is not representable in int8.
    function test_negMin_panics_0x11() public {
        vm.expectRevert(stdError.arithmeticError);
        s.negMin(type(int8).min);
    }
}
