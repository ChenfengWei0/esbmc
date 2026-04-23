# Code Architecture Notes

## Expression Conversion (`get_expr`)

The main expression converter `get_expr()` dispatches to focused handler functions:
- `get_decl_ref_expr()` — variable/function/contract reference resolution
- `get_literal_expr()` — integer, bool, string, hex, bytes literals
- `get_tuple_expr()` — tuple expressions (init lists, swap, multi-return)
- `get_call_expr()` — function calls (builtin, struct ctor, normal, event/error)
- `get_contract_member_call_expr()` — cross-contract member access (x.func(), x.data())
- `get_index_access_expr()` — array/mapping index access
- `get_new_object_expr()` — `new` expressions (contract instantiation, dynamic arrays)

## Declaration Lookup (`find_decl_ref`)

After inheritance merging, AST node IDs are **not unique** across contracts (inherited nodes are copied into derived contracts). The lookup uses two functions:

| Function | Purpose |
|----------|---------|
| `find_node_by_id(subtree, id)` | Pure DFS — find node by ID in any subtree |
| `find_decl_ref(id)` | Scoped lookup: searches `current_baseContractName` + libraries + globals, falls back to `overrideMap` |

## Solidity ↔ C Type Mapping (`SolType` enum)

The `SolidityGrammar::SolType` enum (defined in `solidity_grammar.h`) annotates `typet` objects to preserve Solidity type semantics through the C/irep2 pipeline. Stored in irep via the `#sol_type` attribute, but accessed only through type-safe helpers:

```cpp
set_sol_type(typet &t, SolidityGrammar::SolType st);   // solidity_convert.h
SolidityGrammar::SolType get_sol_type(const typet &t);  // solidity_convert.h
```

Classification functions (in `SolidityGrammar` namespace):
- `is_uint_type(SolType)` — UINT8–UINT256
- `is_int_type(SolType)` — INT8–INT256 (excluding UINT)
- `is_integer_type(SolType)` — all integers
- `is_bytesN_type(SolType)` — BYTES1–BYTES32
- `is_bytes_type(SolType)` — BYTES1–BYTES32 + BYTES_DYN + BYTES_STATIC
- `is_address_type(SolType)` — ADDRESS + ADDRESS_PAYABLE
- `elementary_to_sol_type(ElementaryTypeNameT)` — maps grammar enum to SolType

**Value types:**

| Solidity | `SolType` enum | irep2/C type |
|----------|---------------|--------------|
| `uint8`–`uint256` (×32) | `UINT8`–`UINT256` | `unsignedbv_typet(N)` |
| `int8`–`int256` (×32) | `INT8`–`INT256` | `signedbv_typet(N)` |
| `bool` | `BOOL` | `bool_type()` |
| `address` | `ADDRESS` | `unsignedbv_typet(160)` |
| `address payable` | `ADDRESS_PAYABLE` | `unsignedbv_typet(160)` |
| `bytes1`–`bytes32` (×32) | `BYTES_STATIC` *(inherited from `byte_static_t`)* | `symbol_typet(lib_prefix + "BytesStatic")` with `#sol_bytesn_size` |
| `bytes` (dynamic) | `BYTES_DYN` | `symbol_typet(lib_prefix + "BytesDynamic")` |
| `string` | `STRING` | `pointer_typet(signed_char_type())` |
| `enum` | `ENUM` | `enum_type()` (= `unsignedbv_typet(8)`) |

**Composite/reference types:**

| Solidity | `SolType` enum | irep2/C type |
|----------|---------------|--------------|
| `T[N]` (static array) | `ARRAY` / `ARRAY_LITERAL` | `array_typet(sub, size)` with `#sol_array_size` |
| `T[]` (dynamic array) | `DYNARRAY` | `pointer_typet(sub_type)` |
| `mapping(K=>V)` | `MAPPING` | `array_typet()` (infinity size) or `symbol_typet("mapping_t")` |
| `struct S` | `STRUCT` | `symbol_typet(prefix + "struct " + name)` |
| contract instance | `CONTRACT` | `pointer_typet(symbol_typet(id))` with `#sol_contract` |
| library | `LIBRARY` | `code_typet(...)` (marker only) |

**Literals/temporaries:**

| Concept | `SolType` enum | irep2/C type |
|---------|---------------|--------------|
| integer constant | `INT_CONST` | `signedbv_typet(256)` |
| string literal | `STRING_LITERAL` | `string_constantt(...).type()` |
| array literal | `ARRAY_LITERAL` | `array_typet(sub, size)` |
| new allocation | `ARRAY_CALLOC` | (allocation marker) |
| BytesStatic (runtime) | `BYTES_STATIC` | `symbol_typet(lib_prefix + "BytesStatic")` |
| BytesDynamic (runtime) | `BYTES_DYN` | `symbol_typet(lib_prefix + "BytesDynamic")` |

**Internal tuple types:**

| Concept | `SolType` enum | irep2/C type |
|---------|---------------|--------------|
| multi-return | `TUPLE_RETURNS` | `struct_typet()` |
| tuple instance | `TUPLE_INSTANCE` | (derived from function return type) |

**Note:** `bytes1`–`bytes32` inherit `BYTES_STATIC` from the `byte_static_t` member (not individually typed as `BYTES1`–`BYTES32`) and are differentiated only by the `#sol_bytesn_size` irep attribute.

## RAII State Guards

The converter uses `ScopeGuard<T>` and `StackGuard<T>` templates for safe save/restore of mutable state:
- `current_baseContractName` — scoped contract context for `find_decl_ref`
- `current_BinOp_type` — stack-based type context for binary operator conversion

## Auxiliary Name Generation

`get_unique_name(name_prefix, id_prefix, ...)` is the shared helper for generating collision-free auxiliary variable/function/array names. Called by `get_aux_var()` and `get_aux_array_name()`.

## Debugging with C PoC Equivalents

When a Solidity KNOWNBUG might involve the ESBMC middle-end (symex, SSA) or backend (solvers), rather than the Solidity frontend, **write an equivalent C program** and verify it through ESBMC's C frontend. This isolates whether the bug is in:

- **Solidity frontend** (AST→GOTO conversion): C PoC works, Solidity doesn't → frontend bug
- **Solidity C model** (`src/c2goto/library/solidity/`): C PoC works because it uses simpler primitives → model bug
- **ESBMC symex/solver engine**: C PoC also fails → engine bug (rare, file upstream)

### Technique

1. **Read the Solidity contract** and understand the expected verification result
2. **Dump the GOTO** (`--goto-functions-only --goto-functions-too`) to see what the frontend generates
3. **Write an equivalent C program** that replicates the GOTO's logic:
   - Map `uint256` → `unsigned _ExtInt(256)`, `address` → `unsigned _ExtInt(160)`
   - Map Solidity dynamic arrays → C arrays (stack or malloc)
   - Map contract structs → C structs with `$address`, `$balance` fields
   - Map `transfer()` → C function with if-else dispatch over known contract instances
   - Map the harness loop → `while(nondet_bool()) { ... }` with nondet function dispatch
   - Use `__ESBMC_assume(0)` for `revert` / insufficient balance pruning
   - Use `nondet_uint256()` / `nondet_addr()` for unconstrained extern functions
4. **Run the C program through ESBMC** with the same `--unwind` and check the result
5. **Progressively add complexity** if the simple version works:
   - Start with constants → then nondet values
   - Start with direct global access → then pointer indirection (`this->field`)
   - Start without the harness loop → then add `while(nondet_bool())`
   - Add nondet addresses (instead of constants) to match `_ESBMC_get_unique_address`

### Example: Isolating a Balance Transfer Bug

```c
// Models: new D{value: amount}(arg) with D.constructor calling transfer back
typedef unsigned _ExtInt(256) uint256_t;
typedef unsigned _ExtInt(160) addr_t;
uint256_t nondet_uint256(void);

struct Contract { addr_t address; uint256_t balance; };
struct Contract C_instance, D_instance;

void transfer(struct Contract *src, addr_t dest_addr, uint256_t val) {
    if (dest_addr == C_instance.address) {
        if (src->balance < val) __ESBMC_assume(0);
        src->balance -= val;
        C_instance.balance += val;    // direct global access
    }
}

void createAndEndowD(struct Contract *this_ptr, uint256_t amount) {
    uint256_t before = this_ptr->balance;         // pointer deref
    if (this_ptr->balance < amount) return;
    this_ptr->balance -= amount;                   // pointer deref
    struct Contract tmp_D = { .balance = amount };
    transfer(&tmp_D, this_ptr->address, (uint256_t)1);
    uint256_t after = this_ptr->balance;           // pointer deref
    assert(after == before - amount);  // should FAIL: after = before - amount + 1
}
```

If the C version correctly finds the violation but Solidity doesn't, the bug is in the Solidity-specific layer (C model functions, address model, array model), not in the frontend conversion or the ESBMC engine.

### Known Bug Categories by Layer

| Layer | Symptom | Example |
|-------|---------|---------|
| **Frontend crash** | `json type_error`, `abort`, segfault during "Converting" | Unhandled AST node kinds in type/expr converters |
| **C model bug** | C PoC works, Solidity produces wrong result | Dynamic array copy loses values, address model too complex for solver |
| **Engine bug** | Both C PoC and Solidity fail the same way | (Rare) |
