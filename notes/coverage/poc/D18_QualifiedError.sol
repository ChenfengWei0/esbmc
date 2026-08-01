// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// DOES A QUALIFIED CUSTOM-ERROR REVERT STILL PRUNE ITS PATH?
//
// WHY THIS FILE EXISTS. `solidity_convert_expr.cpp:688-695` turns a MemberAccess
// call into `code_skipt()` when the referenced declaration is an
// `EventDefinition` OR an `ErrorDefinition`:
//
//     if (!func_ref.empty() && func_ref.contains("nodeType") &&
//         (func_ref["nodeType"] == "EventDefinition" ||
//          func_ref["nodeType"] == "ErrorDefinition"))
//     { new_expr = code_skipt(); return false; }
//
// The UNQUALIFIED spelling `revert Err(x)` takes a different route entirely
// (`solidity_convert_expr.cpp:2982-3008`) and calls the error function whose
// body is `__ESBMC_assume(false)` -- which is what makes the path go away. The
// QUALIFIED spelling `revert L.Err(x)` / `revert C.Err(x)` hits the skip.
//
// If the skip really does replace the assume, then a path that reverts on chain
// survives enumeration AS A NORMAL EXIT -- and this pipeline renders exactly
// that as a Foundry test asserting the call succeeds. That is a RED test on the
// unmodified contract, which is the only way this pipeline produces a wrong
// deliverable at all.
//
// THE EXPECTATION IS DIFFERENTIAL, ON PURPOSE. The severity of the drop is
// conditional on `uses_revert_observation`, whose value under path coverage was
// NOT established when this file was written -- and a prediction that depends on
// an unknown is not a prediction. So the expectation below compares the two
// spellings against EACH OTHER and needs no knowledge of the flag:
//
//     `guardQualified` and `guardPlain` are the same function twice, differing
//     ONLY in whether the error name is qualified. They MUST report the same
//     path count and the same exit kinds. If they do not, the drop is live.
//
// This also means the file answers something whichever way the flag goes: if
// the two agree, the skip is being compensated for somewhere downstream and
// that place is worth finding; if they disagree, the difference IS the defect
// and the report names it without further argument.
//
// EXPECTED, stated before running:
//   guardPlain      2 paths -- one normal exit (x != 7), one revert (x == 7)
//   guardQualified  2 paths -- IDENTICAL kinds to guardPlain
//   guardContract   2 paths -- IDENTICAL again; a CONTRACT qualifier reaches the
//                   same line by a different route (TypeMemberCall) and is here
//                   so that "library" is not confused with "qualified"
//
// WHAT A DEFECT LOOKS LIKE, and it will not look like a crash: `guardQualified`
// reporting 2 paths BOTH with exit_kind normal, or reporting 1 path, while
// `guardPlain` reports one normal and one revert. Same count, wrong kinds is the
// worse of the two, because a path count that matches is what a reader checks.
//
// THE CONTROL AGAINST OVER-READING: `noError` has the same branch shape and no
// custom error at all. If IT also disagrees with `guardPlain`, the difference is
// not about errors and this file is measuring something else.

library L18 {
    error LibSaysNo(uint256 got);
}

contract D18_Other {
    error OtherSaysNo(uint256 got);
}

contract D18_QualifiedError {
    error LocalSaysNo(uint256 got);

    uint256 public tag;

    // UNQUALIFIED -- the spelling that is known to lower to `assume(false)`.
    function guardPlain(uint256 x) external {
        if (x == 7) {
            revert LocalSaysNo(x);
        }
        tag = 1;
    }

    // LIBRARY-QUALIFIED -- reaches expr.cpp:688 via LibraryMemberCall.
    function guardQualified(uint256 x) external {
        if (x == 7) {
            revert L18.LibSaysNo(x);
        }
        tag = 2;
    }

    // CONTRACT-QUALIFIED -- reaches the same line via TypeMemberCall. Separate
    // from the library case because "qualified" and "library" are two different
    // properties and only one of them is the one under test.
    function guardContract(uint256 x) external {
        if (x == 7) {
            revert D18_Other.OtherSaysNo(x);
        }
        tag = 3;
    }

    // CONTROL: same branch, no custom error, so the revert cannot be the cause
    // of any difference this function shows.
    function noError(uint256 x) external {
        if (x == 7) {
            revert("plain-string");
        }
        tag = 4;
    }
}
