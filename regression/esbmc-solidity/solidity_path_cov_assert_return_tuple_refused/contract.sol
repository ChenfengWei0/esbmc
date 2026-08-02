// STAGE 3 -- a TUPLE return is REFUSED BY NAME, not silently absent.
//
// The return-value rungs read a per-unit ghost assigned at the RETURN
// instruction. A tuple return has NO RETURN instruction at all: measured on
// notes/coverage/poc/P27_TupleReturn.sol, "returns (uint256, uint256)" lowers
// to writes into a contract-scope tuple_instance object followed by a fall to
// END_FUNCTION. So no ghost is built and no rung can be emitted.
//
// THAT ABSENCE MUST BE NAMED. In this mode a candidate with no row reads as one
// that needed no assertion; for a return value that reads as "measured, and
// unconstrained", which is the opposite of what happened. Same rule the mapping
// fixture pins for state variables, arriving through the return value instead.
//
// ---- WHY THE OBVIOUS TEST FOR "DOES IT RETURN SOMETHING" DOES NOT WORK ----
//
// Both of the signals one would reach for FAIL on this very contract, and that
// is why the refusal is keyed on the tuple instance instead:
//
//   to_code_type(fsym->type).return_type().id()   reads "empty" -- byte for
//                                                 byte what a VOID unit reads
//   #sol_ast_return_sites                         reads 0, which is also why
//                                                 the AST-half exit census does
//                                                 not fire on this unit
//
// Either one would classify this unit as void and record NO refusal at all --
// the failure this directory exists to catch. The positive evidence used
// instead is the frontend's own lowering: the contract-scope
// tuple_instance$<node-id> object keyed by THIS unit's AST node id, which is
// the same key bmc.cpp's counterexample harvest uses to tie a tuple to its
// unit.
//
// `total` is here so the ladder is NOT empty: without a scalar state variable
// the zero-candidate gate would fire first and this run would exit before
// printing the refusal, which would make the fixture pin a different gate than
// the one it is about.
//
// ---- WHY THE SPEC NAMES enc=3 AND NOT enc=2 ----
//
// MEASURED, and it is a separate defect this fixture deliberately does NOT
// pin: on a tuple-returning unit the two arms exit differently.
//
//     enc=2  (a > 10)   exit_kind = UNDETERMINED
//     enc=3  (a <= 10)  exit_kind = normal
//
// Both arms fall to END_FUNCTION -- a tuple return emits no RETURN -- but the
// first jumps there straight from the if-body and so SKIPS the epilogue, which
// is the only positive evidence of a normal exit available at END_FUNCTION. N5
// then refuses the ladder on enc=2 before a single candidate or refusal is
// printed, so a spec naming it would pin the undetermined-exit gate instead of
// the return refusal. That whole class of tuple paths being unusable as an
// oracle is a real gap and belongs in its own fixture with its own fix; naming
// enc=3 keeps THIS directory about one thing.
//
// `payable`, so no ABI value gate is synthesised and msg.value needs no bound.
pragma solidity ^0.8.0;

contract TupleRet {
    uint256 total;

    function two(uint256 a) external payable returns (uint256, uint256) {
        if (a > 10) {
            total = total + a;
            return (1, 2);
        }
        return (0, 0);
    }
}
