# Post-constructor state havoc: current state

Probe date: 2026-07-30. Branch `solidity`. Read-only investigation.

Files read in full: `src/solidity-frontend/solidity_convert_contract.cpp`,
`solidity_convert_constructor.cpp`, `solidity_convert_call.cpp`,
`solidity_convert_ref.cpp`, `solidity_convert_builtin.cpp`, `solidity_convert.cpp`,
`solidity_convert.h`, `solidity_tod_harness.cpp`, `README.md`;
`src/goto-programs/mark_decl_as_non_det.{h,cpp}`, `assign_params_as_non_det.{h,cpp}`,
`goto_convert_functions.cpp`; `src/c2goto/library/solidity/solidity_misc.c`;
`docs/claude/solidity/modes.md`, `approximation-ledger.md`.
`solidity_convert_expr.cpp` read lines 1–2200 (contains the whole `get_call_expr`
intrinsic dispatch). See **Uncertainties** for what was not read.

---

## 1. Does any form exist today?

**No.** There is no post-constructor havoc of contract state variables anywhere in
the tree, in any mode. The default harness path was traced end to end and contains
no havoc:

`convert()` (`solidity_convert.cpp:491-511`)
→ `multi_transaction_verification(c)` (`solidity_convert_contract.cpp:736-829`)
→ `emit_per_tx_reseed_call(tx_body)` (`:774`) + `get_unbound_funccall` (`:777`)
→ `emit_tx_driver(func_body, tx_body)` (`:791`).

The body of `_ESBMC_Main_<C>` is exactly: `[static-lifetime init calls]`,
`__ESBMC_HIDE:`, then N copies (or a `while(nondet_bool)` wrap) of
`{ _sol_per_tx_reseed(); _ESBMC_Nondet_Extcall_<C>(); }`. Nothing writes to the
contract instance between the constructor and the driver.

Candidate-by-candidate:

| Candidate | Where | What it actually does |
|---|---|---|
| `*havoc*` | — | No symbol, function, or option by that name exists in the Solidity frontend. The only `havoc` hits in the tree are the **Yul/inline-assembly** fallback (havocs `externalReferences` of an unsupported assembly block — ledger row 1, `docs/claude/solidity/approximation-ledger.md:28`) and the regression dirs `yul_*_havoc*`, `dynarr_3d_statevar_havoc_knownbug`, `state_var_havoc_via_intrinsic_fail`. None is entry-state havoc. |
| `*nondet_state*` | — | Only `__ESOL_nondet_state_forward` (see §2). No `nondet_state` symbol. |
| `__ESOL_nondet_state_forward` | see §2 | Implemented, but source-level only. |
| `assign_param_nondet` | `solidity_convert_call.cpp:535-757` | Fills **call arguments** for harness-generated calls (nondet scalars/strings/bytes/arrays; nondet contract pointer, or `_ESBMC_nondet_new_<C>()` under `--bound`). It never touches state variables. Also used at `solidity_convert_contract.cpp:419` to pad inherited-ctor arity. |
| `get_nondet_expr` | `solidity_convert_call.cpp:453-457` | 4-line primitive: `new_expr = exprt("sideeffect", t); new_expr.statement("nondet");`. The generic building block a havoc would use — but it has no state-variable caller. |
| `mark_decl_as_non_det` | `src/goto-programs/mark_decl_as_non_det.cpp:9-46` | Goto pass over **`DECL` instructions only** (`if (!it->is_decl()) continue;`) that assigns nondet to uninitialised **locals**. Solidity state variables are members of the static-lifetime global `_ESBMC_Object_<C>` and are never `DECL`'d, so this pass provably cannot reach them (it even asserts `!s->static_lifetime \|\| !s->type.is_code()` at `:22`). |
| `assign_params_as_non_det` | `src/goto-programs/assign_params_as_non_det.cpp:48-218` | The `--function` mechanism. Gated to a single function: `if (fn_sym->name.as_string() != target_function) return false;` (`:59`). It nondets each formal parameter; for a **pointer** parameter it additionally emits (`:96-213`) `bool flag = nondet(); if (!flag) { T tmp; tmp = nondet(); assume(tmp); lhs = &tmp; }`. Because the Solidity entry function is `f(C *this, ...)`, this is what makes `--function` see "all state variables nondet": `tmp` is a whole `C` struct assigned `gen_nondet(C)`. **This is the closest existing thing to entry-state havoc, and it is the only one.** Note it has exactly the mapping/dyn-array blind spot described in §3 — it nondets the struct value, not the out-of-struct globals. |
| c2goto Solidity models | `src/c2goto/library/solidity/` (12 files listed) | No state-forward or havoc model. The only per-iteration reseed is `_sol_per_tx_reseed()` (`solidity_misc.c:165-206`), which reseeds **ambient environment only** — `msg.*`, `tx.*`, `block.*` — with `block_number`/`block_timestamp` constrained monotone. It does not touch contract storage. |

---

## 2. `__ESOL_nondet_state_forward`

**Implemented — but it is a source-level intrinsic, reachable only from a
user-written (or TOD-generator-written) harness contract. It is NOT reachable from
the default `_ESBMC_Main_<C>` harness, and it is not entry-state havoc.**

### Where

- Declaration + doc comment: `src/solidity-frontend/solidity_convert.h:944-951`.
- Helper builder: `src/solidity-frontend/solidity_convert_constructor.cpp:695-861`
  (`build_esol_state_forward_helper`).
- **Sole call site / lowering**: `src/solidity-frontend/solidity_convert_expr.cpp:1981-2030`,
  inside `get_call_expr`.

### What it does — the lowering code

The trigger is a syntactic match on an `Identifier` callee name in the *user's
Solidity source* (`solidity_convert_expr.cpp:1990-2030`):

```cpp
  if (
    callee_expr_json.is_object() &&
    callee_expr_json.value("nodeType", "") == "Identifier" &&
    callee_expr_json.value("name", "") == "__ESOL_nondet_state_forward")
  {
    ...
    std::string cname = src_arg.type().get("#sol_contract").as_string();
    if (cname.empty() && src_arg.type().is_pointer())
      cname = src_arg.type().subtype().get("#sol_contract").as_string();
    ...
    symbolt fwd_sym;
    if (build_esol_state_forward_helper(cname, fwd_sym))
      return true;

    side_effect_expr_function_callt fwd_call;
    fwd_call.function() = symbol_expr(fwd_sym);
    fwd_call.type() = to_code_type(fwd_sym.type).return_type();
    fwd_call.location() = fwd_sym.location;
    fwd_call.arguments().push_back(src_arg);
    new_expr = fwd_call;
    return false;
  }
```

The helper it builds is `void _ESBMC_state_forward_<C>(C *c)`
(`solidity_convert_constructor.cpp:695-861`), id
`sol:@C@<C>@F@_ESBMC_state_forward_<C>#`, memoised on first use (`:704-708`).
Its body (per the header comment at `:686-693`):

```
void _ESBMC_state_forward_C(C *c) {
    __ESBMC_HIDE:
    while (nondet_bool()) {
        _sol_per_tx_reseed();
        if (nondet_bool()) (*c).f1(nondet args...);
        if (nondet_bool()) (*c).f2(...);
        ...
    }
}
```

Concretely: `__ESBMC_HIDE` label (`:717-720`); one `C *c` formal (`:722-739`);
`contract_var = dereference_exprt(c, tag-C)` used as the implicit `this` (`:752-753`
and `:822`); the dispatch loop skips non-`public`/`external` methods and the ctor
(`:756-764`, honours `--no-visibility` at `:748-749`); per-tx reseed at the top of
each iteration (`:746`); and the loop itself:

```cpp
  code_whilet code_while;
  code_while.cond() = nondet_bool_expr;
  code_while.body() = while_body;
  func_body.move_to_operands(code_while);          // :833-836
```

**Note:** it uses a raw unbounded `while(nondet_bool)` — it does **not** go through
`emit_tx_driver()`, so `--solidity-max-tx` does not apply to it (unlike
`build_bound_drive_helper`, which does call `emit_tx_driver` at
`solidity_convert_constructor.cpp:648`).

### Reachability

Only from Solidity source text containing a call named
`__ESOL_nondet_state_forward`. Two producers:

1. **User-written harness contracts.** All three regression tests use this shape,
   and all three require `--bound`:
   - `regression/esbmc-solidity/esol_state_forward_invariant_pass/` — monotonic
     invariant survives state-forward; `--contract H --bound --k-induction`.
   - `regression/esbmc-solidity/esol_state_forward_reaches_nontrivial_fail/` —
     proves state-forward can actually drive `x` off 0.
   - `regression/esbmc-solidity/esol_state_forward_internal_not_exposed_pass/` —
     internal functions are NOT invoked.

   Every one of them is of the form
   ```solidity
   function __ESOL_nondet_state_forward(C c) {}   // stub, body ignored
   contract C { ... }
   contract H { function check() public { C c = new C(); __ESOL_nondet_state_forward(c); ... } }
   ```
   i.e. the *verified contract* is the harness `H`, and `c` is a **freshly
   `new`-allocated** instance — not the `_ESBMC_Object_<C>` singleton that the
   default harness drives.

2. **The TOD race-mode harness generator**,
   `src/solidity-frontend/solidity_tod_harness.cpp:1291`, which emits
   `__ESOL_nondet_state_forward(c1);` into the generated `.sol`, plus the stub at
   `:1454-1469`. That generated file is then re-run through ESBMC as an ordinary
   source file, so it is the same user-harness path.

**It is NOT 90 % of the entry-state fix.** Three reasons:

- It is invoked *inside a function body of some other contract*; nothing in
  `solidity_convert_contract.cpp` (which I read in full) emits such a call into
  `_ESBMC_Main_<C>`. Wiring it into the default harness would require synthesising
  the call in C++, not reusing the source-level trigger.
- Semantically it is **not** havoc: it reaches only states reachable by *some
  sequence of that contract's own public/external calls from the current state*.
  That is strictly weaker than a free symbolic coordinate — anything only an
  `internal`/`private` path or an external actor can establish stays unreachable
  (this is exactly what `esol_state_forward_internal_not_exposed_pass` pins).
- Cost: it is an unbounded `while(nondet_bool)` dispatch loop *nested inside* the
  outer transaction driver — i.e. it multiplies the dispatch state space rather
  than replacing it.

---

## 3. What a real implementation would touch

### Contract instance symbol

- Name / id: `_ESBMC_Object_<C>` / `sol:@_ESBMC_Object_<C>#` —
  `solidity_convert_contract.cpp:44-51` (`get_static_contract_instance_name`).
- Type: `symbol_typet(prefix + c_name)` where `prefix = "tag-"`
  (`solidity_convert_contract.cpp:59, :86`; `solidity_convert.h:1571`). It is a
  **struct value**, not a pointer; members are read as `member_exprt(inst, name, t)`.
- Flags: `lvalue = true`, `static_lifetime = true`, `file_local = true`
  (`solidity_convert_contract.cpp:96-100`).
- Registered for every contract at `solidity_convert.cpp:452-453`. Its `.value` is
  the ctor call, which `clang_c_maint::static_lifetime_init` turns into
  `<C>(&_ESBMC_Object_<C>)` at the top of `__ESBMC_main` — see the comment at
  `solidity_convert_contract.cpp:151-154`. Base contracts of a single `--contract`
  target are registered but **not** deployed (`run_ctor == false`, `:155-156`).
- Ready-made accessor: `get_static_contract_instance_ref(c_name, new_expr)`
  (`solidity_convert_contract.cpp:53-64`).

### Existing nondet-assign helpers

There is **no** helper that assigns nondet to a struct's fields. The pieces that
exist:

- `get_nondet_expr(const typet&, exprt&)` — `solidity_convert_call.cpp:453-457`.
  Produces `sideeffect(nondet)` of any type. This is the atom.
- `assign_param_nondet` — `solidity_convert_call.cpp:535-757`. The per-type
  dispatch table you would want to mirror (CONTRACT → nondet pointer or
  `_ESBMC_nondet_new_<C>()`; STRING → `nondet_string()`; BYTES_DYN →
  `llc_nondet_bytes()`; 1-D array → `calloc`; 2-D array → `_ESBMC_alloc_nested_2d`;
  scalar → `get_nondet_expr`). But it walks a *parameter list*, not struct
  components.
- Two existing recursive **struct walkers** that are the right structural template:
  `emit_clone_deep_copy_fixup` (`solidity_convert_constructor.cpp:1280-1497`) and
  `emit_ctor_deep_init_fixup` (`:1589-1773`). Both already handle the four hard
  shapes: `mapping_t` fields, pointer-backed fixed arrays, multi-dim arrays, and
  inline user structs.
- `collect_contract_global_stores(store_prefix, out)` — declared
  `solidity_convert.h:1299-1301`, used by `build_revert_rollback_block`. It
  enumerates exactly the out-of-struct file-local infinite-array globals under
  `sol:@C@<C>@`. **This is the ready-made enumerator for the mappings/dyn-arrays
  that `*this = nondet` misses.**

### Mappings / dynamic arrays: reached by `*this = nondet`?

**No — and worse, a naive `*this = nondet` is actively harmful.**

Two independent confirmations that the data lives outside the struct:

- `README.md:465-471`: *"**Out-of-struct global stores** — mappings and
  state-variable dynamic arrays are lowered to file-local infinite-array globals
  *outside* the `*this` struct (keyed by `$address`), so `*this = save` alone does
  not reach them."*
- `solidity_convert_constructor.cpp:1015-1028` (the TOD clone helper's step 5b):
  *"State-var dyn-arrays live OUTSIDE the contract struct (they are global SMT
  arrays, see is_dynarray_state branch in get_var_decl), so the `*c = *base`
  struct copy above doesn't touch them — the walker at step 6 also explicitly
  skips them."* The clone helper therefore hand-copies `<arr>_dynarray_len[addr]`
  and each element via `_ESBMC_dynarr_idx(addr, i)`.

What *is* inside the struct, and must be **preserved**, not havoc'd:

- The `mapping_t` handle `{base, mid, addr}` (`solidity_convert.h:667-671`). Its
  `addr` is the instance's `$address` and `base` points into
  `_ESBMC_inf_<C>_<var>[]`. Nondet-ing this field destroys the mapping's identity
  — every subsequent read/write lands in an unrelated keyspace. This is the single
  biggest reason a whole-struct nondet is wrong.
- `$address`, `$balance`, `$codehash`, `$code`, `$mutex_<C>`, `_ESBMC_bind_cname`
  — the synthetic members added by `add_auxiliary_members`
  (`solidity_convert_builtin.cpp:55-256`). Havocing `$address` breaks every
  address-dispatch ladder (`$call#0`, `$transfer#0`, the mapping keyspace, the
  `sol_addr_array` uniqueness registry); havocing `_ESBMC_bind_cname` breaks
  polymorphic dispatch. There is already a predicate for exactly this set:
  `is_sol_builin_symbol(cname, name)` at `solidity_convert_contract.cpp:467-479`.
- `$dynamic_pool` (`BytesPool`) — `solidity_convert_builtin.cpp:269-309`.
- Pointer-backed fixed arrays (`#sol_array_size` on a pointer type): a scalar
  nondet would NULL the backing buffer; they need element-wise nondet, same as
  `emit_ctor_deep_init_fixup` does calloc element-wise.

So the shape of a correct implementation is *not* `*this = nondet`; it is a
recursive component walker (skip-list + per-shape dispatch) **plus** a second pass
over `collect_contract_global_stores` for the mapping/dyn-array globals.

### Where in the harness it would be inserted

`src/solidity-frontend/solidity_convert_contract.cpp`, in
`multi_transaction_verification`, **immediately after the `__ESBMC_HIDE` label is
pushed at line 753 and before `emit_tx_driver(func_body, tx_body)` at line 791**:

```cpp
745:  static_lifetime_init(context, func_body);
...
750:  code_labelt label;
751:  label.set_label("__ESBMC_HIDE");
752:  label.code() = code_skipt();
753:  func_body.operands().push_back(label);
      // <-- insert emit_post_ctor_state_havoc(c_name, func_body) HERE
...
791:  emit_tx_driver(func_body, tx_body);
```

This point is guaranteed post-constructor: the ctor runs via
`clang_c_maint::static_lifetime_init` in `__ESBMC_main` before `config.main`
(= `_ESBMC_Main_<C>`, set at `:826`) is called.

One insertion covers every entry mode, because `--contract`, whole-file, and
`--bound`/`--unbound` multi-contract all funnel through this function:
`solidity_convert.cpp:497` (single `--contract`), and
`prepare_harness_entry_functions` (`solidity_convert_contract.cpp:837-861`) called
from both `multi_contract_verification_bound` (`:930`) and
`..._unbound` (`:1003`). `--focus-function` also uses the same harness (it only
filters the dispatch loop inside `get_unbound_function`,
`solidity_convert_constructor.cpp:335-361`).

### Rough size of the change

Medium — the walkers already exist to copy from, but the correctness surface is
wide:

- ~40 lines: `emit_post_ctor_state_havoc(const std::string&, codet&)` driver —
  resolve the singleton, iterate `to_struct_type(*context.find_symbol("tag-"+C)).components()`,
  skip `comp.is_type()` / `comp.type().id()=="struct"` / `is_sol_builin_symbol(...)`
  / `$dynamic_pool`, dispatch per component.
- ~120-200 lines: the recursive per-shape emitter (scalar, string, BytesDynamic,
  pointer-backed fixed array incl. multi-dim, inline user struct, `mapping_t`
  handle → *preserve*). Structurally a third sibling of
  `emit_clone_deep_copy_fixup` / `emit_ctor_deep_init_fixup`; realistically it
  should be factored to share their dispatch rather than triple it.
- ~30 lines: the out-of-struct pass over `collect_contract_global_stores`. **This
  is the risky part** — those globals are `array_typet(T, infinity)`, and the only
  existing writes to them are array-to-array *copies* (`store = _sol_save_store`
  in `build_revert_rollback_block`). Whether `sideeffect(nondet)` of an infinite
  array type survives symex/`replace_nondet` is unverified; if it does not, this
  needs a C-model helper (a `_sol_havoc_map_<...>` in
  `src/c2goto/library/solidity/`) instead, plus a `solidity_c_models` entry in
  `cprover_library.cpp` (see `README.md:1042-1046`).
- Declaration in `solidity_convert.h` (1 entry) + an option (`--solidity-havoc-entry-state`
  or fold into `--solidity-precise`) in `esbmc_parseoptions.cpp`.
- Regression: **it flips `state_var_default_init_no_setter_pass` to FAILED by
  design** (see §4), so that test must be re-baselined or the feature must be
  opt-in. Plus new PASS/FAIL pins per the repo convention (`README.md:1056-1058`).

---

## 4. Recorded reason it was not done

Not a "can't" — an explicit, **regression-locked design decision**, with a
prescribed workaround.

**The lock.** `regression/esbmc-solidity/state_var_default_init_no_setter_pass/contract.sol`,
lines 5-20 (test.desc expects `^VERIFICATION SUCCESSFUL$`):

> ```
> // Pins the Solidity-frontend design choice (see commit 135c223362 / MODELING-2):
> // state variables KEEP their constructor-assigned initial values (0 / default for
> // unset slots) through the dispatcher loop. They are NOT havoc'd at function entry.
> //
> // For this contract there is no setter, so `a` and `b` remain 0 across every
> // dispatched call to `f()`. `require(a > b)` is `require(0 > 0)` → unsat, the
> // trace dies, and the downstream assert is unreachable. SUCCESSFUL is the sound
> // verdict per Solidity post-deployment semantics.
> //
> // If a future change introduces implicit entry-havoc of state variables, this
> // test will flip to FAILED and force the contributor to confirm intent. Users
> // who need adversarial-state queries (self-composition, SWC-116
> // timestamp-dependence, miner-manipulability) should declare the
> // `__ESBMC_nondet_*` intrinsic inside the contract and assign into the state
> // variable explicitly — see the dual partner
> // `state_var_havoc_via_intrinsic_fail` for the FAILED counterpart.
> ```

**The rationale as stated.** Two claims are made: (a) keeping post-constructor
values is *"the sound verdict per Solidity post-deployment semantics"* — i.e.
havoc would be an over-approximation producing false positives on any contract
whose invariant depends on construction; (b) users who genuinely need adversarial
entry state should get it *explicitly at source level*.

**The prescribed workaround.** `src/solidity-frontend/README.md:865`, the
`__ESBMC_nondet_*()` intrinsic row, states the gap in as many words:

> *"Use when an instrumenter needs to inject a fresh nondet at a specific program
> point without changing function signatures (e.g. self-composition oracles for
> miner-timestamp / hyperproperty checks where neither a parameter nor a state
> variable is a viable injection site — parameters break internal callers, **state
> variables start at the post-constructor default in `--contract` mode rather than
> being havoc'd**)."*

The dual regression `state_var_havoc_via_intrinsic_fail/contract.sol:9-17`
documents the pattern:

> *"The `__ESBMC_nondet_uint` intrinsic (commit 135c223362) is declared inside the
> contract with an empty body; the Solidity frontend lowers each call to
> `side_effect("nondet", T)` of the AST return type. Assigning the intrinsic result
> into the state variable at function entry havocs the variable for the remainder of
> the trace."*

Lowering: `solidity_convert_expr.cpp:2032-2075` (any callee name prefixed
`__ESBMC_nondet_`).

**Related but distinct rationale** — `docs/claude/solidity/modes.md:24-62` gives
the general argument against nondet entry state, for `--function` mode:
`VERIFICATION SUCCESSFUL` becomes stronger, but `VERIFICATION FAILED` *"may be a
false positive — the counterexample could rely on a combination of state-variable
values that is unreachable from `constructor() → (any tx sequence)`"*, and
`--function` is consequently **banned from regression `test.desc` files** (hard
rule at `modes.md:217-221`). Any post-constructor havoc inherits exactly that
false-positive posture for the whole `--contract` mode.

**No entry** for entry-state havoc exists in
`docs/claude/solidity/approximation-ledger.md` (24 rows, read in full) — it is not
tracked as an approximation. Rows 7 (unbound dispatch) and 24 (`$balance` nondet
for non-payable ctors) are the nearest neighbours; row 24 records a structurally
identical *reverted* attempt (S1.1: tightening `$balance` to `gen_zero` was tried
and reverted because *"the harness has no 'between-call external transfers in'
simulation"*), and `solidity_convert_builtin.cpp:139-152` names the missing
prerequisite: *"The proper fix is a nondet-bump at method-call entry (or per-iter
dispatcher reseed) so `$balance` can grow externally between calls."* That is the
same shape of mechanism as post-constructor state havoc, and it is recorded as
open.

---

## Uncertainties

- **`solidity_convert_expr.cpp` lines 2201-6532 were not read** (nor
  `solidity_convert_decl.cpp`, `_stmt.cpp`, `_modifier.cpp`, `_mapping.cpp`,
  `_type.cpp`, `_util.cpp`, `_tuple.cpp`, `_inheritance.cpp`, `_literals.cpp`,
  `_monomorphize.cpp`, `_grammar.cpp`, `_language.cpp`, `_tod_analysis.cpp`). My
  §1 "no" is therefore established **by tracing the harness-construction path end
  to end** (which is complete and lives entirely in the files I did read), not by
  an exhaustive symbol sweep. If some other file emits a state assignment into the
  singleton I would not have seen it — but it could not be part of the harness,
  since `multi_transaction_verification` is the only producer of `_ESBMC_Main_<C>`
  and I read it in full.
- **Where `assign_params_as_non_det` and `mark_decl_as_non_det` are instantiated**
  I could not determine — the wiring is presumably in
  `src/esbmc/esbmc_parseoptions.cpp` (5062 lines, not read). Their *semantics* are
  established from their implementations, which I read in full, and those
  implementations are self-gating (`target_function` name check; `is_decl()`
  check), so the conclusion in §1 does not depend on the wiring.
- **Whether `sideeffect(nondet)` of an `array_typet(T, infinity)` works** through
  goto-symex / `replace_nondet` is unverified — I did not run anything (per
  instruction) and found no existing site that does it. This is the main technical
  unknown for the out-of-struct mapping/dyn-array half of the fix. Settling it
  needs either reading `src/goto-symex/builtin_functions.cpp` +
  `src/util/migrate.cpp`'s nondet handling, or a throwaway experiment.
- **The claim in `solidity_tod_harness.cpp:1444-1447`** that "the frontend
  recognises the `__ESOL_` prefix in `get_noncontract_defition` and drops the body"
  is unverified — `get_noncontract_defition` lives in `solidity_convert_decl.cpp`,
  which I did not read. What I *did* verify is the call-site replacement in
  `get_call_expr`, which is what actually makes the intrinsic work.
- **Exact `--bound` requirement.** All three `esol_state_forward_*` regressions
  pass `--bound`. I did not determine whether the intrinsic *requires* it or
  whether that is incidental to those tests' cross-contract shape (`H` calling
  into `C`).
