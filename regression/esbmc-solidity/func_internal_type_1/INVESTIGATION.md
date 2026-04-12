# func_internal_type_1 KNOWNBUG Investigation

## Summary

Frontend crash when converting FunctionTypeName parameters (e.g., `function (uint) pure returns (uint) f`).
This is a **Solidity frontend bug**, not a backend/symex issue.

## Error

```
terminate called after throwing an instance of 'nlohmann::json_abi_v3_11_3::detail::type_error'
  what():  [json.exception.type_error.305] cannot use operator[] with a string argument with number
```

The crash occurs during AST-to-IR conversion when the frontend encounters a `FunctionTypeName`
node used as a function parameter type. The `get_func_decl_ref()` / `make_pointee_type()` path
does not fully handle the FunctionTypeName AST structure.

## C Equivalent PoC

The equivalent C program using function pointers works correctly:

```c
void map(unsigned *self, unsigned len, unsigned (*f)(unsigned), unsigned *result) {
    for (unsigned i = 0; i < len; i++)
        result[i] = f(self[i]);
}
unsigned square(unsigned x) { return x * x; }
// ... pyramid(4) == 14 => VERIFICATION SUCCESSFUL
```

**Result**: `VERIFICATION SUCCESSFUL` — ESBMC's C backend correctly handles function pointers
as parameters and indirect calls through them.

## Root Cause

The bug is entirely in the **Solidity frontend** (`solidity_convert_*.cpp`).
The ESBMC middle-end and backend fully support function pointers and indirect calls.

Fixing this requires:
1. Handling `FunctionTypeName` AST nodes in `get_type_name_t()` / `make_pointee_type()`
2. Generating function-pointer typed parameters in the IR
3. Resolving indirect calls: at each call site `f(x)`, either:
   - Generate a direct call if `f` can be statically resolved, or
   - Generate an if-else dispatch over all possible target functions

## Complexity

High. Requires function pointer representation + call resolution in the frontend.

## Date

2026-04-12
