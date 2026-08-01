// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// TWO LIBRARY STRUCTS WITH THE SAME SHORT NAME BECOME ONE SORT, AND IF EITHER
// CONTAINS THE OTHER THAT SORT CONTAINS ITSELF.
//
// Under `--z3` the colliding half of this file aborts before solving anything:
//
//     Solving claim 'setFeeReceiver:path:N at' with solver Z3 v4.13.3
//     Encoding remaining VCC(s) using bit-vector/floating-point arithmetic
//     ERROR: Z3 error datatype is not well-founded encountered
//
// It is an ENCODING-time refusal, not a timeout and not a memory death: z3 is
// handed an algebraic datatype whose constructor mentions itself with no base
// case, and there is no such type in this source.
//
// THE PAIRED CONTROL IS IN THIS FILE, and the two halves differ in ONE thing --
// the struct names. `L1.Data` / `L2.Data` abort; `M1.AlphaData` /
// `M2.BetaData`, identical in every other character, complete normally. That
// pair is the whole finding. Everything else was ruled out by measurement:
//
//   the focused function's body   IRRELEVANT -- the automatic reduction of
//                                 st1inch deleted setFeeReceiver's zero check,
//                                 assignment and event, and it still fired
//   the inner mapping             IRRELEVANT -- `uint256 _raw` fires too
//   the outer mapping             IRRELEVANT -- a plain state variable fires
//   inheritance                   IRRELEVANT -- one contract, no bases, fires
//   nesting inside ONE library    DOES NOT FIRE -- two libraries are needed,
//                                 which is what makes the short names collide
//   a lone library struct         DOES NOT FIRE -- one type cannot be made to
//                                 contain itself by sharing a name with nothing
//
// The first four were each proposed as the cause and each refuted by a
// hand-written single-factor file, after four earlier candidates (struct in
// struct through a mapping, a string state variable, a base-contract chain, an
// immutable of interface type) were refuted the same way. Recorded so they are
// not proposed again.
//
// WHY st1inch HITS IT: `AddressArray.Data` and `AddressSet.Data`, where
// `AddressSet.Data` has an `AddressArray.Data items` field. Nothing about that
// contract is unusual -- naming a library's payload struct `Data` is the
// ordinary Solidity idiom, so this is not a corner of the language.
//
// PROVENANCE. Reduced from notes/coverage/inputs/st1inch__St1inch.flat.sol
// (4874 lines) by notes/coverage/scripts/reduce_to_poc.py against the predicate
// `z3-not-well-founded`: automatically to 1182 lines, then by hand, splitting
// one factor at a time. The backend is part of the reproduction -- bitwuzla
// never returns on the same query and cvc5 raises std::bad_alloc, so a run
// without `--z3` is a run of something else.
//
// THE KEY, NOW READ OUT OF THE SOURCE rather than inferred from behaviour.
// src/solvers/z3/z3_conv.cpp:1030-1031:
//
//     z3::symbol tuple_name = z3_ctx.str_symbol(
//       std::string("struct_type_" + strct.name.as_string()).c_str());
//
// The z3 tuple sort is named after `strct.name` and nothing else. Two
// `struct_type2t`s with the same name therefore ask z3 for two datatypes under
// ONE name, and when one holds the other z3 sees `struct_type_Data` with a
// `struct_type_Data` field -- self-referential, no base case, refused.
//
// WHY ONLY z3. bitwuzla and cvc5 flatten tuples rather than declaring
// datatypes, so a name collision costs them nothing here; they fail on this
// benchmark for their own unrelated reasons (never returning, and
// std::bad_alloc). `--tuple-node-flattener` avoids it on z3 for the same
// reason, which is why it works as a stopgap and why it is not a fix.
//
// WHERE THE FIX BELONGS, and this part is a judgement, not a measurement. Not
// in z3_conv: a sort name has to be STABLE across the repeated mk_struct_sort
// calls for one type, so "make it unique" cannot mean a counter, and deriving
// it from the members would only paper over two distinct types still sharing a
// name everywhere else. The name is supposed to identify the type, so the
// frontend giving two distinct Solidity types one name is the defect. The
// reference side already qualifies it -- solidity_convert_type.cpp:596-629
// takes the second whitespace token of `struct L1.Data storage ref`, i.e.
// `L1.Data` -- so the mismatch is on the DEFINITION side, which is where to
// look next.
//
// `--cov-report-json` IS PART OF THE REPRODUCTION, and finding that out was an
// accident worth keeping. Neither state variable below is ever read, so slicing
// removes both and the colliding sort is never built -- without the flag this
// file completes normally. The flag exempts contract-scope stores from slicing
// so each path's counterexample values survive into the report, and that is
// what keeps `_collides` alive long enough to be encoded. So the defect needs
// the type to REACH the solver, which the ordinary pipeline does and a
// stripped-down command does not.
//
// The accident: the regression was first written without the flag, all three of
// its expectations matched, and the KNOWNBUG turned red. That run is the proof
// the expectations are ACHIEVABLE -- a KNOWNBUG whose regexes can never match
// is green forever, including after the bug is fixed, and this project has
// already shipped one of those.
//
// Run:
//   solc --ast-compact-json D13_Z3TupleNotWellFounded.sol > D13.solast
//   esbmc D13.solast --sol D13_Z3TupleNotWellFounded.sol \
//         --solidity-path-coverage --cov-report-json \
//         --contract D13_Z3TupleNotWellFounded --solidity-max-tx 1 --z3

library L1 {
    struct Data {
        uint256 _raw;
    }
}

library L2 {
    // Same SHORT name as L1.Data, different type. This field is what closes the
    // cycle once the two are given one sort.
    struct Data {
        L1.Data items;
    }
}

// THE CONTROL: identical shape, distinct short names. If a fix makes the
// colliding pair encode, this one must keep encoding -- it is what rules out
// "the encoder stopped encoding nested library structs at all".
library M1 {
    struct AlphaData {
        uint256 _raw;
    }
}

library M2 {
    struct BetaData {
        M1.AlphaData items;
    }
}

contract D13_Z3TupleNotWellFounded {
    L2.Data private _collides;
    M2.BetaData private _distinct;

    function setFeeReceiver(address r) public {}
}
