# library_11 KNOWNBUG Investigation

## Summary

Test expects `VERIFICATION FAILED` but gets `VERIFICATION SUCCESSFUL`.
The root cause is a **dynamic array copy + reassignment bug** in the Solidity array model.
Values become unconstrained (nondet) after copying elements to a new array and reassigning
the pointer.

## Root Cause: Dynamic Array Copy Bug

The BigInt `add()` function has a carry expansion path:

```solidity
if (carry > 0) {
    uint[] memory newLimbs = new uint[](r.limbs.length + 1);
    for (i = 0; i < r.limbs.length; ++i)
        newLimbs[i] = r.limbs[i];    // copy elements
    newLimbs[i] = carry;              // add carry
    r.limbs = newLimbs;               // reassign pointer
}
```

After this reassignment, **`r.limbs[0]` becomes unconstrained**. Both
`assert(r.limbs[0] == 6)` and `assert(r.limbs[0] == 7)` pass as VERIFICATION SUCCESSFUL.

## Minimal Solidity Reproducer

```solidity
contract C {
    function f() public pure {
        uint[] memory arr = new uint[](1);
        arr[0] = 42;
        uint[] memory newArr = new uint[](2);
        for (uint i = 0; i < arr.length; ++i)
            newArr[i] = arr[i];
        newArr[1] = 99;
        arr = newArr;
        assert(arr[0] == 42);   // PASSES (but should)
        assert(arr[0] == 999);  // ALSO PASSES (should FAIL!)
    }
}
```

Both assertions pass, proving `arr[0]` is unconstrained after copy + reassignment.

## C Equivalent PoC

The equivalent C program works correctly:

```c
unsigned arr_storage[1] = {42};
unsigned *arr = arr_storage;
unsigned newArr_storage[2] = {0};
unsigned *newArr = newArr_storage;
newArr[0] = arr[0]; newArr[1] = 99;
arr = newArr;
assert(arr[0] == 42);   // PASSES
assert(arr[0] == 999);  // FAILS (correct)
```

**Result**: C backend correctly handles pointer reassignment + value preservation.

## Analysis

This is NOT an `unchecked` overflow bug. The unchecked arithmetic itself works correctly
(verified with inline unchecked blocks and library-routed unchecked blocks).

The bug is specifically in the **ESBMC Solidity dynamic array model** (`_ESBMC_arrcpy` /
`_ESBMC_array_length` C model functions) — when a dynamic array's elements are copied to a
new allocation and the original pointer is reassigned, the symex engine loses track of the
copied values.

## Where to Fix

The fix should be in one of:
- `src/c2goto/library/solidity/solidity_array.c` — the `_ESBMC_arrcpy` / array model functions
- Or in how `src/solidity-frontend/solidity_convert_expr.cpp` generates array copy operations

## Date

2026-04-12
