// SPDX-License-Identifier: MIT
// KNOWNBUG: a Solidity modifier body of shape `{ _; assert(...); }` —
// any `assert(...)` placed AFTER the `_;` placeholder, even
// `assert(true)` — crashes ESBMC during the conversion phase with
// `to_symbol_expr` arity assertion at src/util/std_expr.h:88.
//
// Empirical filter (verified during Bug 1 work): the trigger is
// specifically `assert(...)`. None of these post-`_;` shapes crash:
//   require(...)      — explicit intercept at solidity_convert_expr.cpp:2180
//   revert()          — explicit intercept at solidity_convert_expr.cpp:2161
//   plain assignment  — never enters the builtin-call path
//   _; alone          — no post-statement to lower
//
// Suspected crash callsite: clang_c_adjust_expr.cpp:892-894, where the
// guard `if (f_op.is_symbol())` lacks the `!has_operands()` part of
// `to_symbol_expr`'s precondition. Some downstream rewrite of the
// unintercepted `assert` builtin produces a symbol-shaped exprt with
// operands, tripping the assertion when clang_c_adjust revisits it.
//
// Pre-fix output: `to_symbol_expr` assertion + abort during conversion.
// Post-fix output: VERIFICATION SUCCESSFUL.
//
// KNOWNBUG mode + regex `^VERIFICATION SUCCESSFUL$` silently passes
// today (regex does NOT match the abort output) and will flag for
// promotion to CORE once the fix lands. Pre-existing failing tests
// modifier_3 and modifier_4 share this root cause and are expected to
// clear automatically alongside this pin once the fix is in.
pragma solidity >=0.8.0;

contract C {
    modifier g {
        _;
        assert(true);
    }

    function f() external g {}
}
