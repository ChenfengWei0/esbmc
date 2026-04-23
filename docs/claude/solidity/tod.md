# TOD (Transaction Order Dependence) Detection

Reference paper: *TransRacer: Function Dependence-Guided Transaction Race Detection
for Smart Contracts* (ESEC/FSE 2023).

## Status snapshot

| Capability | State |
|---|---|
| TOD-State (`--tod-race-check`) — any public state var differs after reordering | **Shipped** |
| Auto-discovery of candidate function pairs | **Shipped** (R/W footprint + call-graph closure) |
| Targeted assertions (only on shared footprint) | **Shipped** |
| One-shot auto-verify (internal solc invocation, no manual chain) | **Shipped** |
| TOD-Balance (`--tod-balance-check`) — `address(this).balance` differs after reordering | **Shipped** (requires `--bound` + EOA balance model) |
| Multi-sender harness (different `msg.sender` per call) | **Not implemented** |
| Setup phase (nondet calls reach non-initial states) | **Partial** — `__ESOL_nondet_state_forward` intrinsic available |
| Cross-contract TOD | Out of scope (same as TransRacer) |

## CLI surface

| Option | Value | Purpose |
|---|---|---|
| `--tod-race-check` | `auto` \| `f1,f2` | TransRacer-style storage-race TOD on `--contract`: pairs whose shared footprint includes a non-balance state variable. `auto` (or bare `--tod-race-check`) auto-discovers pairs, `f1,f2` targets one pair. |
| `--tod-balance-check` | `auto` \| `f1,f2` | Balance-based TOD: pairs whose shared footprint includes `address(this).balance`. Same auto/explicit pair semantics. Requires `--bound` + the EOA balance model (see `operational-models.md`). |
| `--dump-harness` | flag | Modifier: print the generated Solidity harness to stdout instead of verifying. |
| `--tod-jobs` | `N` | Number of parallel ESBMC subprocesses to run in `auto` mode. Defaults to `min(hardware_concurrency, pair_count)`. Use 1 for sequential. |

`--contract <name>` is required. Use `=` between `--tod-*-check` and its value; space-separated (`--tod-race-check auto`) is ambiguous with positional args and not supported.

## Work modes (matrix)

| # | Command | What it does |
|---|---|---|
| **A** | `--contract C --tod-race-check=A,B` | Generate harness, write `tod_A_B_harness.sol` next to source, redirect cmdline to it, set `--bound --no-standard-checks --no-unwinding-assertions --unwind 2`, run BMC in-process. One verdict. |
| **B** | `--contract C --tod-race-check=A,B --dump-harness` | Same generation, print to stdout, no verification. |
| **C** | `--contract C --tod-race-check=auto` | R/W footprint analysis → list candidate pairs → write a multi-harness `tod_auto_C_harness.sol` → subprocess loop ESBMC over each `TOD_<a>_<b>` → summary line `N pair(s) — X clean, Y TOD found, Z error` + list of failing pairs. Exit non-zero if any pair fails. |
| **D** | `--contract C --tod-race-check=auto --dump-harness` | Print candidate list + multi-pair harness, no verification. |

The same matrix applies to `--tod-balance-check`.

## Core idea: harness-based reduction to BMC

Given contract `V` with functions `A` and `B`:

> ∃ args_a, args_b such that
>   state(init → A(args_a) → B(args_b)) ≠ state(init → B(args_b) → A(args_a))

This is an existential query — perfect for BMC. We materialise both orderings in a generated harness and assert state equality at the end; a counterexample is the witness pair of transactions that reveals TOD.

## Critical ESBMC constraint: singleton aliasing

ESBMC creates ONE global static instance per contract type (`_ESBMC_Object_<Name>`). Two `new V()` calls share the same singleton — their state is aliased. So
`V c1 = new V(); V c2 = new V(); c1.setX(1); c2.setX(2); assert(c1.getX()==1)`
**incorrectly fails** because both write to `_ESBMC_Object_V.x`.

**Workaround — Two-Copy Rename**: duplicate the contract source with different names (`V_C1`, `V_C2`). Each gets its own singleton. Only the contract name changes; all internal logic is identical. Multi-pair harnesses share these two copies (emitted once) across all `TOD_<a>_<b>` test contracts.

## Auto-bind for `new`-created instances

In unbound mode (default `--contract`), cross-contract calls returned nondet **without executing the function body**, which caused TOD harnesses to silently report SUCCESSFUL even on real bugs. The three `!is_bound` branches in `get_contract_member_call_expr()` (`solidity_convert_expr.cpp`) now check `is_new_created_var(base_expr_json)` and dispatch to the bound path when the base instance was created via `new`. The `_ESBMC_bind_cname` assignment in `get_new_object_expr` is unconditional.

Effect: a TOD harness no longer needs the user to pass `--bound` manually for the `new V()` setup pattern. (We still pass `--bound` in the harness auto-verify pipeline for consistency, especially for balance-TOD which additionally requires the EOA balance model.)

## Algorithm: R/W footprint with intra-contract callgraph closure

Module: `src/solidity-frontend/solidity_tod_analysis.{h,cpp}`.

**Step 1 — per-callable R/W footprint** (single AST walk):
- `Assignment.leftHandSide` → write target (recurse with `is_write_target=true`)
- `Assignment.rightHandSide` → read context
- Compound assignment (`+=` etc.) → re-visit LHS as a read too
- `UnaryOperation` with `++` / `--` / `delete` → write on argument
- `IndexAccess.baseExpression` / `MemberAccess.expression` → inherit parent write context (so `m[k] = v` writes `m`, reads `k`)
- Any `Identifier` whose `referencedDeclaration` is a state variable id → R or W
- `FunctionCall` whose callee is a same-contract callable → call edge
- `ModifierInvocation` → modifier body folded in via call edge
- For `--tod-balance-check`: `transfer`/`send`/`call{value:}`/`selfdestruct` mark a write on the virtual `__balance` token

**Step 2 — call-graph closure**:
```
footprint(f) = local(f) ∪ ⋃_{c ∈ callees(f)} footprint(c)
```
Iterated to a fixed point. External calls (`c.foo()`, `this.foo()`) are **conservatively skipped** — out of scope for the intra-contract analysis.

**Step 3 — pair candidacy**:
```
W(a) ∩ (R(b) ∪ W(b))  ∪  W(b) ∩ (R(a) ∪ W(a))  ≠ ∅
```
Pair filters:
- Both functions must be `public` or `external`
- Skip `view` / `pure` (no writes by definition)
- Skip `constructor` / `fallback` / `receive` (cannot be reordered)
- Skip self-pairs and symmetric duplicates (sort lexicographically, `a < b`)
- For balance-TOD: additionally filter F2 non-state-var-touching functions

For multi-contract source, F2 walks `linearizedBaseContracts` so inherited functions are discoverable.

## Harness emission (`solidity_tod_harness.{h,cpp}`)

Layout of an auto-generated `.sol`:

```solidity
// Header: pair list + verify command
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// ===== Copy 1 (V renamed to V_C1) =====
contract V_C1 { /* original verbatim */ }
// ===== Copy 2 =====
contract V_C2 { /* identical, V renamed to V_C2 */ }

// ===== TOD Harness contracts =====
// Targeted state variables (referenced by BOTH functions):
//   - x
contract TOD_A_B {
    function test(/* ctor + A + B params, prefixed to avoid collisions */) public {
        V_C1 c1 = new V_C1(...);
        V_C2 c2 = new V_C2(...);
        c1.A(...); c1.B(...);   // order 1
        c2.B(...); c2.A(...);   // order 2
        assert(c1.x() == c2.x());
    }
}
// ... one TOD_<a>_<b> per discovered pair
```

For non-public state variables, the harness emits a shadow getter `__tod_get_<name>` at the bottom of the two-copy source (so that assertions can compare private/internal slots across the two contracts).

**Setup-phase intrinsics** (optional use):

```solidity
__ESOL_nondet_state_forward(c1);        // drive c1 through nondet public-method dispatch
C c2 = __ESOL_deep_copy(c1);            // c2 starts at c1's current state, then diverges
```

These are plain free-function stubs with concrete bodies so solc can still compile the harness. ESBMC recognises the `__ESOL_` prefix and replaces the bodies with `_ESBMC_state_forward_<C>` / `_ESBMC_clone_<C>`. The intrinsics are only invoked at the user level by the harness author; the auto-generated harness does NOT insert them by default.

**Targeted assertions** consume the **closure-closed** R/W footprint, not just body-local references. So a public function that only writes `x` via an internal helper still produces the right `assert(c1.x() == c2.x())`.

**Mapping handling** (`mapping(K => V)` and `mapping(K1 => mapping(K2 => V))`): the harness collects every parameter of type `K` from `A` and `B`, emits one assertion per Cartesian-product key tuple. Three or more nested levels are skipped with an explanatory comment.

## What "TOD-State" means

**TOD-State only** (i.e. `--tod-race-check`). The harness asserts equality on every public state variable in the closure intersection. Any field of type `uint` / `int` / `address` / `bool` / `bytes32` / `mapping(...)` qualifies — including a state variable literally named `balance` (declared as `uint public balance;`).

## What "TOD-Balance" means

`--tod-balance-check` is the separate ETH-balance TOD mode. Real ETH balance lives in the contract's hidden `$balance` field, not in user-declared state vars. The R/W analyser marks `transfer` / `send` / `call{value:}` / `selfdestruct` as writes on a virtual `__balance` token; the harness emits `assert(address(c1).balance == address(c2).balance)` whenever `__balance` is in the shared footprint.

Requires `--bound` because balance reads for unknown addresses short-circuit to `nondet_uint` under `--unbound` (see `operational-models.md` § EOA Balance).

## Known limitations

- **Same-sender only**: all harness calls share `msg.sender = address(this)` (the harness contract). Misses TOD requiring distinct senders (e.g. `approve` + `transferFrom`).
- **Constructor-state only**: no automatic setup phase between constructor and the reordered call pair. User can insert `__ESOL_nondet_state_forward(c1)` manually before the ordering for exploratory state.
- **External calls dropped from R/W closure**: `c.foo()` and `this.foo()` contribute nothing to the footprint, so a pair that interacts only through an external surface may not be flagged as a candidate.
- **3+ level nested mapping**: assertions are skipped with a comment.

## Regression coverage

| Test | Mode | Property |
|---|---|---|
| `tod_counter_fail` | single pair, auto-verify | Both functions write `x` → FAILED |
| `tod_disjoint_pass` | single pair | Disjoint state → SUCCESSFUL |
| `tod_auto_multi` | auto | Two independent pairs both flagged |
| `tod_auto_closure` | auto | Footprint closure across internal helpers |
| `tod_auto_clean` | auto | Disjoint state → "0 candidates, exit cleanly" |
| `tod_balance_pass` / `_fail` | balance | ETH-balance TOD detection, racing / non-racing |
| `tod_transracer_wintoken_approve_race` | race | TransRacer-style approve/increaseApproval race |
| `tod_two_cname_mapping_fail` | harness | Two structurally identical contracts share singleton; mapping reads must route via the same singleton the dispatcher writes to |
| `tod_bind_polymorphism_mapping_pass` / `_fail` | — | `A1 alias = A1(address(c2))` — per-pointer bind shadow + shadow-propagation + polymorphic getter read |
| `nested_mapping_in_contract_pass` / `_fail` | — | Combined-key nested-mapping write+read inside a single contract |
| `nested_mapping_pubgetter_compile` | — | Nested public-getter compiles (crash-fix regression) |
| `cross_single_mapping_pass` / `_fail` | — | Single-level public-getter read lines up with in-contract write (raw key, no fold) |
| `cross_nested_mapping_pass` / `_fail` | — | Nested public-getter read lines up with in-contract write (fold + combine) |
| `ext_call_new_autobind_pass` / `_fail` | — | Auto-bind fix prerequisite |
| `esol_state_forward_invariant_pass` / `_reaches_nontrivial_fail` / `_internal_not_exposed_pass` | — | `__ESOL_nondet_state_forward` visibility filter + invariance + reachability |
| `esol_clone_*` | — | `__ESOL_deep_copy` per-type isolation tests (see operational-models.md) |
