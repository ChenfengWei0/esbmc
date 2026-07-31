// A UNIT BODY HAS A DOUBLE IDENTITY, and --focus-function may suppress only one
// of the two.
//
// The same function body appears
//   (a) as an externally-callable UNIT with its own ABI non-payable value gate,
//       entered by the dispatcher with free arguments; and
//   (b) PHYSICALLY INLINED into another unit's path when it is called
//       internally (sol_path_inlinet::expand_here), entered with computed
//       arguments and WITHOUT the gate.
//
// `--focus-function f` suppresses (a) for every non-focused unit -- that is the
// instrumentation narrowing, and it happens in the ENUMERATION loop. It must
// NOT suppress (b). If it did, the focused unit would silently lose the callee's
// decisions from its own path identity and every `enc` would mean something
// else: a wrong answer that looks like a smaller, faster run.
//
// This test pins the direction that is easy to break. `caller` is the FOCUSED
// unit and it internally calls `pub`, which is itself public -- so `pub` is a
// unit the focus excludes, and its body is exactly the double-identity case.
// `helper` is private and is there as the control: it has only identity (b), so
// it is unaffected either way and any change confined to it would not be
// evidence about (a)/(b) at all.
//
// ---- THE MUST-FLIP PAIR (measured, both directions) ----
//
// The broken variant is one edit: make `expandable_callee` return nullptr for a
// callee that is a unit the focus does not select, i.e. suppress (b) as well.
//
//                             correct        (b) suppressed
//   expanded internal calls   2              1
//   instrumented              5 paths        3 paths
//   expansion multiplier      5.00x          3.00x
//   enc values                15,14,13,12,2  7,6,2
//   Path Coverage             80%            100%
//
// Two things make this worth a regression rather than a comment:
//
//  * THE BROKEN SIDE LOOKS BETTER. Fewer paths, a higher coverage percentage, a
//    faster run. Nothing about the output says a decision went missing.
//  * IT IS COMPLETELY SILENT. Refusing the callee inside `expandable_callee`
//    also hides it from the residual-unit-call scan, which is the detector that
//    exists to NAME an unexpanded call to a gated unit. So not even the NAMED
//    OBSTACLE warning fires -- the mechanism built to catch this exact hazard is
//    bypassed by the same edit that causes it.
//
// The arithmetic that makes 5 the right number: the ABI value gate contributes
// one reject path, and on the accept side `pub`'s `if` and `helper`'s `if` are
// independent, so 1 + 2*2 = 5. Dropping `pub` leaves 1 + 2 = 3. That is why the
// `5.00x` expansion line is pinned as well as the count: `caller`'s own body has
// exactly ONE path before expansion, so the multiplier IS the callees'
// contribution and reads directly as "the callees' decisions are in here".
pragma solidity ^0.8.0;

contract C {
    uint256 public x;

    function pub(uint256 a) public {
        if (a > 1) {
            x = 1;
        } else {
            x = 2;
        }
    }

    function helper(uint256 a) private {
        if (a > 3) {
            x = 3;
        }
    }

    function caller(uint256 a) public {
        pub(a);
        helper(a);
    }
}
