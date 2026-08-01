// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// `--overflow-check` MISSES NARROW-WIDTH ARITHMETIC IN SOLIDITY, BECAUSE THE
// OPERANDS ARE PROMOTED TO `signed int` BEFORE THE CHECK IS BUILT.
//
// HOW IT WAS FOUND. `--path-cov-arith-resolve` proved `uint256` paths reachable
// only by overflowing and failed to prove the SAME SHAPE at `uint8`
// (D20_FalseRevertOnly.sol). `--show-claims` on that contract prints the two
// claims side by side and the difference is in the claim itself:
//
//     addGate256   !overflow("+", this->big, a)
//     addGate      !overflow("+", (signed int)b, (signed int)a)
//
// The uint8 operands are widened to a 32-bit SIGNED int first, so the claim asks
// whether 200 + 248 overflows an int32. It does not -- 448 fits -- and the claim
// is vacuously true. C's integer-promotion rule has been applied to Solidity
// operands, and Solidity has no such rule: `uint8 + uint8` is uint8 arithmetic
// and reverts Panic(0x11) at 256.
//
// MEASURED, and the positive control is what makes the negative one mean
// anything (D20, `--focus-function`, `--overflow-check`, no path coverage):
//
//     addGate256 (uint256)   VERIFICATION FAILED, "arithmetic overflow on add"
//     addGate    (uint8)     VERIFICATION SUCCESSFUL
//
// D19_PanicSemantics.t.sol already measured the chain for exactly this shape:
// uint8 `200 + 200` reverts Panic(0x11), forge 1.7.1 / solc 0.8.34.
//
// WHY IT MATTERS TWICE OVER.
//
//   1. As a VERIFIER result: `VERIFICATION SUCCESSFUL` under `--overflow-check`
//      on a contract using narrow types is not a statement about overflow. Every
//      ERC20 with a `uint8 decimals`, every packed struct, every `uint96` stake
//      amount is in this class.
//   2. As a TEST GENERATOR result: the re-solve cannot assume what was never
//      meaningfully emitted, so a path reachable only by wrapping a uint8 is
//      witnessed at a wrapping value, classified `exit_kind: normal`, and
//      rendered as a bare call asserting the call succeeds -- RED on the
//      unmodified contract.
//
// THIS FILE'S JOB IS THE BOUNDARY, not the existence. "Narrow types are broken"
// is not actionable; which widths, and whether signedness matters, is. Every
// function is the same shape at a different declared type, and each is run
// separately with `--focus-function`.
//
// EXPECTED IF THE PROMOTION EXPLANATION IS RIGHT: every type strictly narrower
// than the promotion target passes (the check is vacuous), and every type at or
// above it fails. If instead uint128 passes and uint256 fails, the boundary is
// not the promotion and this explanation is wrong.
//
// Run, per function:
//   esbmc D21_NarrowOverflowMissed.solast --sol D21_NarrowOverflowMissed.sol \
//     --contract D21_NarrowOverflowMissed --focus-function <name> \
//     --solidity-max-tx 1 --overflow-check
//
// A function FAILS = the overflow is detected = correct.
// A function SUCCEEDS = the overflow is invisible = the defect at that width.
//
// ---- MEASURED 2026-08-01. THE BOUNDARY IS EXACTLY 32 BITS ----
//
// Read from each run's VERDICT LINE, not its exit code -- exit codes are not
// comparable across configurations and this project has already misread one.
//
//   function   verdict                  result
//   add8       VERIFICATION SUCCESSFUL  MISSED
//   add16      VERIFICATION SUCCESSFUL  MISSED
//   addS8      VERIFICATION SUCCESSFUL  MISSED
//   mul8       VERIFICATION SUCCESSFUL  MISSED
//   sub8       VERIFICATION SUCCESSFUL  MISSED     <- unsigned UNDERFLOW
//   add32      VERIFICATION FAILED      detected
//   add64      VERIFICATION FAILED      detected
//   add128     VERIFICATION FAILED      detected
//   add256     VERIFICATION FAILED      detected
//   addS256    VERIFICATION FAILED      detected
//   sub256     VERIFICATION FAILED      detected
//
// The cut is at 32 bits, which is exactly C's promotion target (`int`). The
// explanation is therefore not merely consistent with the boundary -- the
// boundary is the prediction the explanation made, and it held. Had uint128
// passed and uint256 failed, the promotion story would have been wrong.
//
// It is NOT specific to `+`: `mul8` and `sub8` are missed too, so it is the
// operand lowering rather than one operator's check. And `sub8` matters most in
// practice -- unsigned underflow is the commonest real Solidity panic, and a
// promotion to a SIGNED 32-bit type hides it perfectly, because 0 - 1 is a
// perfectly ordinary -1 in int32.
//
// ---- WHAT THIS INVALIDATES ----
//
// Any `VERIFICATION SUCCESSFUL` produced under `--overflow-check` on a Solidity
// contract that does arithmetic at a width below 32 bits is not a statement
// about overflow at that width. `uint8 decimals`, packed structs, `uint16`
// basis-point fees and `uint8` indices are ordinary Solidity, not corner cases.
//
// For this pipeline specifically it is the reason D20's uint8 wrapping path is
// witnessed, classified `exit_kind: normal`, and rendered as a bare call
// asserting success -- RED on the unmodified contract, since D19 measured that
// the chain answers that very input with Panic(0x11).
contract D21_NarrowOverflowMissed {
    uint8 public t8;
    uint16 public t16;
    uint32 public t32;
    uint64 public t64;
    uint128 public t128;
    uint256 public t256;
    int8 public s8;
    int256 public s256;

    function add8(uint8 a) external {
        uint8 b = 200;
        t8 = b + a;
    }

    function add16(uint16 a) external {
        uint16 b = 60000;
        t16 = b + a;
    }

    function add32(uint32 a) external {
        uint32 b = 4000000000;
        t32 = b + a;
    }

    function add64(uint64 a) external {
        uint64 b = 18000000000000000000;
        t64 = b + a;
    }

    function add128(uint128 a) external {
        uint128 b = type(uint128).max - 10;
        t128 = b + a;
    }

    function add256(uint256 a) external {
        uint256 b = type(uint256).max - 10;
        t256 = b + a;
    }

    // SIGNEDNESS as a separate axis: if the promotion target is `signed int`,
    // a narrow SIGNED type may behave differently from a narrow unsigned one.
    function addS8(int8 a) external {
        int8 b = 100;
        s8 = b + a;
    }

    function addS256(int256 a) external {
        int256 b = type(int256).max - 10;
        s256 = b + a;
    }

    // MULTIPLICATION at a narrow width, because `overflow_check` dispatches per
    // operator and add is not evidence about mul.
    function mul8(uint8 a) external {
        uint8 b = 100;
        t8 = b * a;
    }

    // SUBTRACTION underflow at a narrow width -- the most common real Solidity
    // panic, and the one a promotion to a SIGNED type would hide most cleanly
    // (0 - 1 is representable as -1 in int32 and is not an int32 overflow).
    function sub8(uint8 a) external {
        uint8 b = 0;
        t8 = b - a;
    }

    function sub256(uint256 a) external {
        uint256 b = 0;
        t256 = b - a;
    }
}
