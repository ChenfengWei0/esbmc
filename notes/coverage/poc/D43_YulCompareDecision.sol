// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ONE QUESTION: does a Yul COMPARISON -- a pure value computation with no
/// branch anywhere in the source -- create a path decision?
///
/// `convert_yul_expression` lowers `lt/gt/eq/slt/sgt/iszero` through one lambda,
/// `bool_to_u256(cond) = if_exprt(cond, 1, 0)`
/// (src/solidity-frontend/solidity_convert_stmt.cpp:2106, used at 2193, 2215,
/// 2229). A ternary's condition in an ASSIGN right-hand side IS a path decision:
/// `collect_short_circuit_decisions` emits `to_if2t(e).cond` and the enumerator
/// fans out on it with no feasibility check.
///
/// THIS IS NOT THE SAME DEFECT AS THE SHIFT CLAMP. There, the ternary is
/// FAITHFUL -- EVM really does return 0 for a shift amount >= 256, so with a
/// symbolic amount both arms are genuinely reachable and only a LITERAL amount
/// was wasteful. Here there is no branch in the semantics at all: `lt(a, b)` is
/// a value. If it still costs paths, the repair is not a constant fold but
/// replacing the ternary with a bool -> uint256 typecast, which the same
/// function already uses for signed operands (`solidity_gen_typecast`, :2208).
///
/// The three functions differ in ONE thing each, and nothing else.
///
/// PRE-REGISTERED, written before anything was touched:
///   * `noCompare` is the baseline: arithmetic only, no comparison, no branch.
///     Whatever its path count is, that is what "no decision" costs here.
///   * `oneCompare` adds exactly one `lt` and NOTHING else. If its path count
///     is HIGHER than noCompare's, a pure value computation manufactured a
///     decision, and that is the finding.
///   * `twoCompares` adds a second, independent `lt`. If the cost is real it
///     should compound (a second decision, not a second copy of the first).
///   * If all three have the SAME path count, the comparison costs nothing and
///     `bool_to_u256` is not worth touching -- the finding is refuted and the
///     ternary at :2106 stays exactly as it is.
///
/// ⛔ The shift is deliberately absent from every function: the fold landed
/// this session and a `shr` here would put two mechanisms in one measurement.
contract ComparePoc
{
  uint256 public a;
  uint256 public b;
  uint256 public c;

  function noCompare(uint256 x) external
  {
    assembly ("memory-safe") {
      let v := add(x, 1)
      sstore(a.slot, v)
    }
  }

  function oneCompare(uint256 x) external
  {
    assembly ("memory-safe") {
      let v := lt(x, 32)
      sstore(b.slot, v)
    }
  }

  function twoCompares(uint256 x, uint256 y) external
  {
    assembly ("memory-safe") {
      let v := lt(x, 32)
      let w := lt(y, 64)
      sstore(c.slot, add(v, w))
    }
  }
}
