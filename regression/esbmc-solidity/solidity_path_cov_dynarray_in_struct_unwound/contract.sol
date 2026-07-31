// REDUCED FROM 1inch st1inch WHILE DEBUGGING IT, and it is 20 lines.
//
// The failure it isolates: with the DEFAULT bound the run produces NOTHING --
// zero VCCs, no unit entered, SIGABRT -- and with `--unwind 64` the very same
// contract reports 3 paths at 100%. Nothing else changes.
//
// WHAT MAKES IT HAPPEN, pinned by four sibling PoCs under notes/coverage/poc/
// that each move ONE thing and all pass at the default bound:
//
//     D06_PlainDynArray          the array, no struct       -> 3/3, 100%
//     D07_StructDynArrayNoPush   the struct, no push        -> 3/3, 100%
//     D08_StructFixedArray       fixed length instead       -> 3/3, 100%
//     D03 (this shape)           struct + dyn array + push  -> 0 VCCs, SIGABRT
//
// So the cause is exactly "a `push` to a DYNAMIC array that is a STRUCT
// MEMBER". That lowers to a `__memcpy_impl` whose loop needs more than four
// iterations. `--solidity-path-coverage` installs `--unwind 4` for itself when
// the user gives no bound, AND forces `--no-unwinding-assertions`, so the
// truncation becomes `ASSUME(!loopcond)` -- which is FALSE here, and therefore
// deletes every execution that reaches the harness. Symex then produces
//
//     Symex completed in: 0.003s (227 assignments)
//     Generated 0 VCC(s), 0 remaining after simplification
//
// and not one instrumented claim is ever asked.
//
// WHY THIS IS PINNED IN THE *WORKING* DIRECTION HERE. The sibling directory
// `solidity_path_cov_dynarray_in_struct_default_unwind_knownbug` pins the
// broken one. This one exists so the fix cannot be "raise the default and call
// it done" without a test that says what the raised bound must still deliver:
// the same three paths, all witnessed, and NO truncation warning.
//
// The 1inch relevance is not decoration. `@1inch/solidity-utils`'s AddressSet
// is `struct Data { AddressArray.Data _items; mapping(...) _lookup; }` and
// AddressArray.Data wraps a dynamic array -- this shape, one level deeper, in
// the plugin bookkeeping every St1inch constructor sets up.
pragma solidity ^0.8.0;

contract C {
    struct Data {
        uint256[] raw;
    }

    address public owner;
    address public feeReceiver;
    Data internal items;

    constructor() {
        owner = msg.sender;
        items.raw.push(7);
    }

    function setFeeReceiver(address r) external {
        require(msg.sender == owner, "not owner");
        feeReceiver = r;
    }
}
