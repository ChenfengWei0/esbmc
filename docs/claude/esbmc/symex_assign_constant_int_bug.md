# "assignment to constant_int not handled" — root cause & fix options

## Symptom

```
ERROR: assignment to constant_int not handled
timeout: the monitored command dumped core
```

Triggered under the L2 experimental Solidity refactor that changes
`BytesStatic` storage from `unsigned char data[32]` to
`_BitInt(256) data` when verifying a 2D fixed-array state var that
contains `BytesStatic`:

```solidity
bytes32[3][2] internal buf;
function run() external {
    buf[0][0] = bytes32(uint256(0x1111));
    assert(buf[1][2] == bytes32(uint256(0xbeef)));
}
```

The symptom is NOT specific to the 2D shape — it surfaces as soon as
any code path writes to (or initialises through) a field of a struct
that contains a `_BitInt(N)` member whose width is already aligned to
its representation boundary (N a multiple of `ext_int_representation_bytes(T)`).

The existing test `regression/esbmc-solidity/multi_dim_fixed_bytes32_2d_fail`
reproduces. A pure-C minimal repro (`static B buf[2][3]; buf[0][0] = v;`
where `B = { _BitInt(256); size_t; }`) also reproduces.

Backtrace: `src/goto-symex/symex_assign.cpp:448` —
`log_error("assignment to {} not handled", get_expr_id(lhs))`.

## Dispatch table in symex_assign_rec

`src/goto-symex/symex_assign.cpp:390-450` dispatches on the LHS expr2 kind:

- `symbol2t`     → `symex_assign_symbol`
- `index2t`      → `symex_assign_array`   (recurses into source_value)
- `member2t`     → `symex_assign_member`  (recurses into source_value)
- `if2t`         → `symex_assign_if`
- `typecast2t` / `bitcast2t` → `symex_assign_typecast`
- `constant_string2t` / `null_object2t` → silently ignored
- `byte_extract2t` → `symex_assign_byte_extract`
- `concat2t`     → `symex_assign_concat`
- `constant_struct2t` → `symex_assign_structure`
- `constant_union2t`  → `symex_assign_union`
- `extract2t`    → `symex_assign_extract`
- `bitand2t`     → `symex_assign_bitfield`
- **anything else** → `abort()`

`constant_int2t` falls through to the abort.

## Actual IR at the crash site

Debug dump (captured by temporarily adding `lhs->pretty()` at the abort
branch) shows:

- **lhs**: `constant_int { value : 0, type : unsignedbv { width : 0 } }`
- **full_lhs**: `member { source_value : index(index(member(deref(this),
  "buf"), 1), 2), member_name : ext_int_pad$1, type : unsignedbv
  { width : 0 } }`
- **rhs**: `constant_int { value : 0, type : unsignedbv { width : 0 } }`

The LHS has been simplified to a `constant_int` because the full_lhs
is a member-access chain ending at a struct member whose TYPE has width
zero (`unsignedbv { width : 0 }`). Somewhere in the assign recursion
(`symex_assign_array` → `symex_assign_member` → some canonicaliser),
the chain reduced the zero-width write to a literal-constant write,
then tried to route it through `symex_assign_rec` again — and
`constant_int` has no assign handler.

## The zero-width member: `ext_int_pad$1`

The struct has FOUR members:

| idx | name              | width (bits) |
|-----|-------------------|--------------|
| 0   | data              | 256 (`_BitInt(256)`)|
| 1   | `ext_int_pad$1`   | **0**        |
| 2   | length            | 64           |
| 3   | `anon_pad$3`      | 192          |

`ext_int_pad$1` is inserted by `pad_ext_int_after` in
`src/clang-c-frontend/padding.cpp:116-131`. The call site
(`padding.cpp:194-206`) computes the padding size as

```cpp
const std::size_t repr_bytes = ext_int_representation_bytes(it->type());
const std::size_t repr_bits = repr_bytes * config.ansi_c.char_width;
const std::size_t unaligned_bits = w % repr_bits;
const std::size_t pad = unaligned_bits ? repr_bits - unaligned_bits : 0;
it = pad_ext_int_after(components, it, pad);
```

For `_BitInt(256)`: `repr_bytes = 32`, `repr_bits = 256`,
`unaligned_bits = 256 % 256 = 0`, so `pad = 0`. The call then inserts
a component with width 0 — spurious padding that the rest of the
infrastructure is not prepared for.

`pad_ext_int_after` unconditionally constructs an `unsignedbv_typet(0)`
member and inserts it; it does not guard on `pad_bits > 0`.

## How the zero-width member leaks into an assignment

A struct write `this->buf[i][j] = some_struct_value;` at the GOTO
level is a single `ASSIGN` instruction whose LHS is the nested
`member(index(index(member(deref(this), "buf"), i), j))` chain and
whose RHS is the source struct expression. `symex_assign_rec` recurses
via `symex_assign_member` → `symex_assign_array` → `symex_assign_array`
→ `symex_assign_member` and eventually ends up assigning
field-by-field. When it reaches the zero-width `ext_int_pad$1` field,
the RHS extraction — a bit-slice of the source struct's 0-bit region —
simplifies to `constant_int { value : 0, width : 0 }`, and likewise
the LHS's `with` / `member_offset` lowering collapses to a
`constant_int` value node because there is no storage to write into.
`symex_assign_rec` is then called with `lhs = constant_int`, which
falls through to the abort.

This is the shape regardless of where the assignment originates —
user write, constructor deep-init, struct copy on return, anything.
Any code path that field-walks `BytesStatic` under the uint256 layout
hits it.

## Why L1 doesn't hit this

In L1, `BytesStatic` stores `unsigned char data[32]` — no `_BitInt`
member, no `pad_ext_int_after` call, no zero-width padding component.
`ext_int_pad$N` cannot appear inside L1's `BytesStatic`. The struct
has exactly `data` and `length` plus plain `anon_pad$N` for
whole-struct-end alignment, and symex handles the chain cleanly.

## Why the padding fix alone is not enough

The earlier commit `98765dfdf6` (`fix: make add_padding idempotent
for _BitInt / _ExtInt struct members`) guards against re-entering
`add_padding` on an already-padded struct. That closes the "duplicate
`ext_int_pad$N` name" collision at `irep2_type.cpp:282`, but does
NOT stop the ORIGINAL insertion of a zero-width pad on the FIRST
pass — and the first-pass zero-width pad is what triggers the symex
constant_int abort.

The two bugs are orthogonal:
- commit 98765dfdf6 = "don't insert the same padding twice"
- this bug          = "don't insert a zero-width padding at all"

## Fix options

### Option A (preferred) — fix at the padding source

Guard the `pad_ext_int_after` call so it is only invoked when the
padding size is positive:

```cpp
// src/clang-c-frontend/padding.cpp:194-206
if (is_extint && !is_bitfield && !it->get_is_padding())
{
  ...
  const std::size_t unaligned_bits = w % repr_bits;
  if (unaligned_bits != 0) {
    const std::size_t pad = repr_bits - unaligned_bits;
    it = pad_ext_int_after(components, it, pad);
  }
  // else: already aligned — no padding needed, don't insert a 0-width component
}
```

This is the root-cause fix. The struct shape is the one a C compiler
would emit anyway (no pad when alignment is already satisfied), so no
downstream code should expect a zero-width placeholder.

**Risks to check:**
- `padding.cpp:300-317` later recomputes offsets and special-cases
  `it_type.get_bool("#extint")` with a sibling-extint-pad lookup
  (`pad_field != components.end() && pad_field->get_is_padding() &&
  pad_field->type().get_bool("#extint")`). If the expected pad is
  absent, the offset arithmetic at line 303/312 must handle its
  absence correctly. Needs a read-through.
- Any code that pattern-matches on `ext_int_pad$N` field names and
  assumes one always exists after every `#extint` member would
  break silently. Grep result (earlier): no such callers outside
  padding.cpp.
- The regression test `struct_extint_03` (added by 98765dfdf6)
  verifies that _ExtInt(256) in a struct round-trips correctly —
  must keep passing after the option-A change.

### Option B (alternative, narrower) — handle zero-width constant LHS in symex

Add a branch in `symex_assign_rec`:

```cpp
else if (is_constant_int2t(lhs) && lhs->type->get_width() == 0)
{
  // zero-width write — no SSA effect, silently drop
}
```

Semantically harmless (there is nothing to write), and localised to
symex. But leaves the spurious zero-width field in the struct model,
which wastes SSA tracking slots and may confuse other passes (e.g.
slicing, memory model, struct equality) downstream. Symptomatic, not
a root fix.

### Option C — both

Do option A for the root, and option B as a defensive guard for any
future source of zero-width LHS. Slightly belt-and-suspenders but
cheap.

## Recommendation

Option A. The zero-width pad is a frontend bug, not a symex gap.
`pad_ext_int_after` should mirror `pad_bit_field` (which only inserts
when the accumulated bit count is non-zero modulo `char_width`, and
only pads the *gap* size). A zero-sized struct component is not
idiomatic C/C++ and ESBMC's other passes (member lookup, offset
arithmetic, byte-extract lowering) are not built to tolerate it —
symex is just the first to trip.

## Follow-on work (out of scope for this bug)

Even with option A landed, the L2 BytesStatic refactor still has the
`bytes_7` regression where k-induction's inductive step can't close
the SSA for uint256-backed bytes operations. That is a separate
solver-complexity issue orthogonal to the zero-width pad — see
`docs/claude/solidity/language-support.md` if/when it gets written up.
