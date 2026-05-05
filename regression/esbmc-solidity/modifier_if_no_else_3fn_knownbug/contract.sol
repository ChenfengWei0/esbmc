// SPDX-License-Identifier: MIT
// KNOWNBUG: 3+ functions sharing an `if (cond) _;` modifier (no else branch)
// crash GOTO conversion with `ifthenelse takes two or three operands`.
//
// Root cause (suspected): solidity_convert_modifier.cpp's
// splice_placeholders walker erases the single `_;` operand at the
// ifthenelse's then-slot and inserts N operands from the wrapper body.
// When N != 1 (which differs across f1/f2/f3 due to per-function aux
// wrapper state), the parent ifthenelse ends up with `1 + N` operands —
// rejected by goto_convert.cpp:1647-1651.
//
// Single function works (N happens to be 1); three functions trip a
// divergence that produces a malformed shape. See
// notes/bug1_ifthenelse_modifier_inline_diagnosis.md for the dump.
pragma solidity >=0.8.0;

contract C {
    mapping(address => bool) ok;

    modifier g {
        if (ok[msg.sender]) _;
    }

    function f1() external g {}
    function f2() external g {}
    function f3() external g {}
}
