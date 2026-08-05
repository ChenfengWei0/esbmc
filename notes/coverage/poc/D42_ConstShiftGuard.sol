// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ONE QUESTION: does a Yul shift with a COMPILE-TIME-CONSTANT shift amount
/// produce a path decision that no input can ever take?
///
/// The frontend lowers `shr(s, v)` to `s < 256 ? (v >> s) : 0`, because EVM
/// really does clamp a shift of 256 or more to zero. That ternary lands in an
/// ASSIGN right-hand side, and an ASSIGN right-hand side's folded conditions
/// ARE path decisions -- the path enumerator fans out on every one of them with
/// no feasibility check and no constant folding. So a LITERAL shift amount
/// still doubles the path count, and exactly half of the resulting paths are
/// unreachable by construction: they need `248 < 256` to be false.
///
/// The two functions below differ in ONE thing and nothing else: whether the
/// shift amount is a literal or a parameter. That is the whole experiment.
///
/// PRE-REGISTERED, written before the frontend was touched:
///   * `constShift` MUST lose paths once constant shift amounts are folded at
///     construction, and MUST NOT lose any witnessed one.
///   * `varShift` MUST keep exactly the paths it has. Its clamp is real: a
///     caller can pass k >= 256 and EVM returns 0.
///   * Both unchanged  => the edited branch is never reached (a wiring fault).
///   * constShift unchanged and varShift changed => the condition is inverted.
contract ShiftPoc
{
  uint256 public a;
  uint256 public b;

  function constShift(uint256 x) external
  {
    assembly ("memory-safe") {
      let v := shr(248, x)
      sstore(a.slot, v)
    }
  }

  function varShift(uint256 x, uint256 k) external
  {
    assembly ("memory-safe") {
      let v := shr(k, x)
      sstore(b.slot, v)
    }
  }
}
