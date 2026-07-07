# ESBMC → Foundry Test Generation — Technical Reference (living document)

> **Purpose.** A single, continuously-updated record of the algorithms actually
> implemented in the ESBMC coverage→Foundry test generator. This is the
> engineering ground truth: what the code does, why, and where. When you change
> `src/goto-symex/foundry.cpp` (or the frontend stamps it depends on), update
> the matching section here in the same commit.
>
> **Companion docs.** `notes/foundry/roadmap.md` (what's next), `RESULTS.md` /
> `REPRO_MATRIX.md` under `notes/coverage-comparison/_foundry_roundtrip/`
> (empirical round-trip verdicts per benchmark). This file is the *how*; those
> are the *what-works*.
>
> Last updated: 2026-07-07.

---

## 0. What the generator does, in one paragraph

ESBMC runs branch-coverage over a Solidity contract. For each covered branch it
holds a satisfying SSA path + a solver model. The generator (`foundry_generator`)
**reconstructs a concrete, compilable Foundry `.t.sol` test** from that model:
it recovers the sequence of transactions (constructor + external calls), the
argument literals the solver chose, and the EVM environment (msg.value,
block.timestamp, msg.sender) each transaction ran under, then emits a
`forge`-runnable test whose execution re-drives the contract down the same
branch. The **paramount anti-goal**: *never emit a wrong or uncompilable test.*
Any construct it cannot render faithfully degrades to a `// UNSUPPORTED` comment
(or a revert-tolerant `try/catch`), never to a guessed literal.

---

## 1. Entry points and lifecycle

| Function | Role |
|----------|------|
| `collect(target, smt_conv, ns)` | Called once per covered claim (SAT). Reconstructs one `test_case` and appends it. Thread-safe (`data_mutex`). |
| `generate()` | End-of-run. Dedups all collected cases, groups by contract-under-test, writes one `<Primary>.cov.t.sol` per contract. |
| `generate_single(...)` | Single-counterexample mode (`--generate-foundry-testcase` without whole-unit coverage). Reconstructs one case and writes it immediately. |
| `clear()` | Reset accumulated state (cases, `non_instantiable`, `libraries`, `source_file`). |

The heavy lifting is `reconstruct()` (returns a `test_case` = ordered
`vector<sol_call>`) and `write_foundry_file()` (renders the `.t.sol`).

Data model:
- `sol_arg` = `{param, sol_type, value (expr2tc), literal (string)}`.
- `sol_call` = `{contract, method, args, supported, reverts, payable, msg_value,
  block_timestamp, warp, msg_sender, prank, deployer, ctor_value_unsendable}`.
- `test_case` = `vector<sol_call>` (constructors first, then transactions).

---

## 2. Symbol-name parsing (`parse_param_symbol`)

Solidity function parameters are named `sol:@C@<contract>@F@<method>@<param>`
with SSA suffixes appended (`?`, `!`, `&`, `#`). The parser:
1. Truncates at the first SSA-suffix char.
2. Requires prefix `sol:@C@`, splits on `@F@` to get `<contract>` and the rest.
3. Splits the rest on the first `@` into `<method>` / `<param>`.
4. Rejects anything where `<param>` carries a further `@` (that means a local or
   temporary, not a genuine parameter).

This is the keystone that lets the generator map a recovered SSA symbol back to
`(contract, method, param)`. A constructor argument is recognised as the special
case `method == contract`.

---

## 3. Type system: lowered type → Solidity source type string

The generator works with an internal **sol-type string** (e.g. `"UINT256"`,
`"ADDRESS"`, `"BYTES32"`, `"ARRAY:UINT256"`, `"UDVT:Name:UINT256"`,
`"BYTES_DYN"`, `"STRING"`, `"BOOL"`). Two extractors produce it:

### 3.1 `effective_sol_type(const typet &)`
Maps a lowered **type** to the sol-type string. Key cases, in order:
- **Array**: a `T[]`/`T[N]` lowers to `pointer<elem>` with the array kind on the
  *pointer* (`#sol_type` ∈ {`DYNARRAY`, `ARRAY`, `ARRAY_LITERAL`, `ARRAY_CALLOC`}).
  Detected **before** the pointer-deref (else `T[]` is mistaken for scalar `T`).
  Encoded `"ARRAY:<elem>"`.
- **`bytes`**: lowers to a `BytesDynamic` struct → `"BYTES_DYN"`.
- **fixed `bytesN`**: lowers to a `BytesStatic` struct; the width survives as
  `#sol_bytesn_size` on the type → `"BYTES<N>"` (1..32). The width is the
  authoritative discriminator (the `#sol_type`="BytesStatic" tag is not always
  present across solc AST shapes).
- **UDVT** (`type Name is <underlying>`): `#sol_udvt_name` on the type →
  `"UDVT:<Name>:<underlying-sol-type>"` (rendered as `Name.wrap(literal)`).
- Otherwise the type's `#sol_type` passes through (`UINT256`, `ADDRESS`, …).

### 3.2 `arg_sol_type(const exprt &arg)` — authoritative for `code_typet` args
The bytesN width and UDVT name are **stripped from the parameter's type** during
`type2tc` migration, but the frontend re-stamps them **directly on the
`code_typet` argument irep** (`#sol_bytesn_size`, `#sol_udvt_name`). So for a
declared parameter, `arg_sol_type` prefers the argument's own stamps and only
falls back to `effective_sol_type(arg.type())`. This is why bytesN and UDVT args
render at their exact declared width even after migration.

### 3.3 `sol_type_to_solidity(sol_type)` — sol-type → Solidity source name
Renders array-element / general type names (`"ARRAY:UINT256"` → `"uint256[]"`,
`"UDVT:X:..."` → `"X"`, `"BYTES_DYN"` → `"bytes"`, etc.). Returns `""` for a type
it cannot name (→ UNSUPPORTED).

### 3.4 CONTRACT-typed args (interface/contract handles) — mocked (§13)
An interface/contract-typed parameter lowers to `pointer<struct tag-I>` with
`#sol_type: CONTRACT` and `#sol_contract: <I>` on the **pointer argument type**.
`effective_sol_type` returns `"CONTRACT:<I>"` for it. When `I` is a **true
interface** (`#sol_interface`) and its full stub set renders, the generator
synthesizes a mock (§13); an abstract contract, a concrete contract, or an
unrenderable interface degrades to UNSUPPORTED.

Note: `string` lowers to `pointer<signedbv>` with `#sol_type: STRING` on the
pointer. `effective_sol_type` deliberately does **not** surface STRING (a string
call-argument stays UNSUPPORTED rather than default to `""` and risk a
wrong-branch replay). STRING is recognized only *locally* inside
`build_mock_spec`, so a string *return*/param is nameable in a mock stub.

---

## 4. Value formatting (`format_sol_value`, `default_sol_literal`)

`format_sol_value(sol_type, value)` turns a recovered solver value into a
Solidity literal. Discipline: **exact or nothing** — an unrenderable value
returns `""`, which flags the call UNSUPPORTED rather than risk a wrong literal.

- **UDVT**: recurse on the underlying, wrap `Name.wrap(inner)`.
- **BOOL**: `true`/`false` (accepts bool or int model value).
- **bytesN**: `format_fixed_bytes(n, value)` — the width `n` **must** come from
  the declared type, never inferred from the recovered `BytesStatic.length`
  (which is free nondet on paths that don't constrain the bytesN and could yield
  a wrong-width literal). Reads `data[0..n-1]` big-endian (matching solc's
  `bytes_static_from_uint` layout) and emits `bytesN(0x..)`. Handles both
  `constant_array` and `constant_array_of` (collapsed all-equal, e.g. all-zero).
- **UINT/INT**: decimal literal (always exact).
- **ADDRESS**: `address(uint160(N))` — never a bare 40-hex literal (solc rejects
  non-EIP-55-checksummed hex addresses).
- everything else → `""`.

`default_sol_literal(sol_type)` supplies a **type default** for a slot with no
recovered value (a sliced/irrelevant parameter — sound because it cannot change
which branch is reached):
- BOOL→`false`, UINT/INT→`0`, ADDRESS→`address(0)`, bytesN→zero literal,
  `ARRAY:T`→`new T[](4)` (N=4 mirrors the external-call harness
  `kHarnessDynLen` so length-dependent branches align), `BYTES_DYN`→`hex""`,
  STRING→`""`.

`cast_for_overload(sol_type, lit)` wraps integer literals in an explicit typed
cast (`uint8(3)`) so Solidity overload resolution selects the intended overload.

---

## 5. Method / parameter resolution

### 5.1 `get_method_params(ns, contract, method)` (cached)
Finds the function symbol `sol:@C@<C>@F@<method>#<node>` (rejecting
`@<name>`-suffixed params/locals) and returns its declared parameters in source
order as `(name, sol_type)`, skipping the synthetic `this` self-pointer. Prefers
`arg_sol_type`; falls back to the parameter symbol's `#sol_type`.

### 5.2 `params_of_method_id(ns, id)` — exact-id variant (overload resolution)
Same, but keyed on an exact method id (needed to disambiguate overloads where
the base name alone is ambiguous).

### 5.3 Dispatcher-callable set (`dispatcher_callable`, `collect_dispatch_calls`)
The whole-unit harness drives a contract through a synthetic dispatcher
`sol:@C@<C>@_ESBMC_Nondet_Extcall_<C>`. Recursively walking its body records
every direct call to a contract method as `base → {exact ids}`. This is the set
of externally-callable entries; modifier/aux helpers the dispatcher never calls
directly are naturally excluded. Overloads appear as multiple ids under one base.

### 5.4 Modifier-wrapper resolution (`resolve_dispatcher_method`)
A method with modifiers runs its body inside a synthetic wrapper
`<method>_<modifier>` that the dispatcher never calls directly. A branch covered
inside the wrapper names the wrapper, not the external entry. The wrapper symbol
carries `#sol_modifier_wrapper_for` naming the real method (authoritative). To
guard against the frontend's **unescaped** wrapper naming (`a`+`b_mod` and
`a_b`+`mod` both produce `a_b_mod`), resolution succeeds **only when exactly one
dispatcher-callable method is a `<m>_` prefix** of the wrapper name; otherwise it
under-covers (returns `""`) rather than emit a call to the wrong method.

### 5.5 Contract-kind predicates (frontend stamps on the ctor symbol)
- `contract_is_non_instantiable` — `#sol_no_new` (abstract/interface/library):
  cannot be `new`'d.
- `contract_is_library` — `#sol_library`: called statically (`Lib.fn(args)`),
  never instantiated.
- `contract_bases` — `#sol_bases` list: linearized base contracts, used to drop
  base instances (only the most-derived contract is `new`'d).
- `symbol_is_payable` — `#sol_payable`: only payable methods/ctors may receive
  `{value:}`.

---

## 6. Reconstruction (`reconstruct`) — the core algorithm

Walks `target.SSA_steps` in order, keeping only **guard-true** steps
(`l_get(guard).is_true()`), and segments the path into: constructor phase →
one *segment* per transaction. Transaction boundaries are the dispatcher's first
nondet guard (`dispatcher_tx_contract`: `..._ESBMC_Nondet_Extcall_<C>#...
return_value$_nondet_bool$1`).

### 6.1 Per-step classification
For each guard-true step:
1. **Revert detection**: a guard-true assert whose location has
   `sol_revert_edge` (stamped by `goto_coverage` for a Phase-A detected
   `revert CustomError(...)`) marks the current segment `reverts = true`.
2. **Method attribution (authoritative)**: a guard-true coverage assert's source
   location names the covered function; `resolve_dispatcher_method` maps it to
   the real entry and **overrides** any earlier method guess for the segment.
   This fixes mis-attribution in multi-function dispatchers (e.g. a `dock` claim
   latching onto `ship`).
3. **Environment recovery (③A0)** — see §7.
4. **Transaction boundary**: an assignment to a `..._nondet_bool$1` dispatcher
   guard pushes a new segment for contract `<C>`, attaching the buffered pending
   env values.
5. **Parameter recovery**: an assignment whose RHS is a nondet symbol and whose
   LHS parses as `(c, m, p)` records `value = smt_conv.get(nondet)` and the
   sol-type. Constructor args (`m == c`, pre-dispatcher) go to `ctor_args`;
   in-segment args go to `segs.back().args`.
6. **Method fallback**: if a segment still has no method, the first
   dispatcher-callable body that executes fixes it.

### 6.2 `build_call(contract, method, recovered)` — arity-correct call synthesis
- `receive`/`fallback` → UNSUPPORTED (Solidity forbids calling them by name).
- Resolves the exact signature: single dispatcher id → direct; overloaded → the
  single candidate declaring every recovered parameter name (ambiguous / no
  match → UNSUPPORTED, never a guess); else base-name lookup.
- For each **declared** parameter in source order: fill from recovered value
  (re-formatted against the **declared** type — critical for bytesN width), else
  the recovered recovery-site literal, else the type default. An empty literal
  flags the call UNSUPPORTED. Overloaded calls get `cast_for_overload`.
- This is **always arity-correct**: it iterates declared params, so it never
  emits a wrong-arity call.

### 6.3 Alternate reconstruction paths
- **`--function` isolated mode** (§ libraries): no dispatcher, so no segments.
  Reconstruct a single static call `Lib.fn(args)` to the focused function.
  **Restricted to libraries** (`contract_is_library`): a library's internals
  inline into the caller so a static call is sound; a contract's private/internal
  fn is not externally callable. `recover_focus_param` reads the model value of a
  focused param directly off any guard-true SSA step referencing it (in
  `--function` mode params are free nondet inputs with no `param = nondet`
  assignment to key off).
- **Coverage-claim fallback**: when no normal path produced a call (per-claim
  slicing removed the dispatcher's `$1` guard, or a loop-only branch never set a
  segment method), derive the covered method directly from the guard-true
  coverage assert's source location and reconstruct one call to it.

### 6.4 Base-instance dropping + defaulted ctor synthesis
- Remove every call whose contract is a base of another used contract (the base
  ctor runs transitively via `new Derived(...)`).
- Record `non_instantiable` (abstract/interface/library) and `libraries` sets for
  the emitter.
- Synthesize a defaulted ctor (`new C(...)`) for any touched contract with a
  parameterized ctor that wasn't reconstructed.
- `stable_partition` so constructors precede transactions.

---

## 7. Environment reconstruction (③A0): msg.value / block.timestamp / msg.sender

The dispatcher's `_sol_per_tx_reseed` prologue assigns the C globals `msg_value`
/ `block_timestamp` / `msg_sender` just before each tx's Extcall guard. The
recovery buffers the most-recent reseed values (`pending_*`) and attaches them to
the next segment when its boundary fires.

**`is_env_global(name, base)`** decisively excludes user Solidity symbols: the C
globals do **not** live in the `sol:` namespace, so a `sol:` prefix is a hard
exclusion (a user variable named `block_timestamp` won't false-match).

Per environment axis, a value is pinned **only when the body actually reads it**
(`reads_global` walk over the segment's steps), to avoid noisy cheatcodes:
- **msg.value** — pinned only for a **payable** method with a non-zero solver
  value → `vm.deal(address(this), v)` + `{value: v}`. Non-payable methods never
  receive value.
- **block.timestamp** — `reads_timestamp` → `vm.warp(t)` before the call. The
  reseed's own monotonic timestamp read and `initialize()` are excluded via the
  `.sol`-file discriminator (`step_in_sol`), so they're not mis-attributed to the
  body.
- **msg.sender** — `reads_msg_sender` → `vm.prank(sender)` (last cheatcode before
  the call). A nested/high/low-level call wrapper that overwrites `msg_sender`
  after the reseed marks the segment `sender_dirty`, refusing the pin (safe
  under-coverage) — the top-level sender isn't reproducible via `vm.prank`.

### 7.1 Constructor-time environment (`ctor_*`)
A ctor branching on `block.timestamp` / `msg.value` / `msg.sender` is satisfied
by the **deploy-time** ambient (set in `initialize()` before the dispatcher).
Foundry's `new C()` deploys under the default env, so a timelock/value/owner
ctor can revert in `setUp` and fail the whole suite. Recovery captures the
deploy-time values from `initialize()` and whether the ctor reads them:
- `ctor_reads_timestamp` → `vm.warp(t)` in setUp before `new C()`.
- deployer (`owner = msg.sender`): pinned only on a clean path
  (`!ctor_sender_dirty`) → `vm.startPrank(deployer)` around the deploy.
- payable ctor with non-zero value → `vm.deal` + `new C{value: v}(...)`.
- **non-payable ctor needing non-zero value** → UNSUPPORTED
  (`ctor_value_unsendable`): the EVM forbids sending value; forge can't reproduce
  it, so the deploy degrades rather than emit a guaranteed-reverting test.

The `--contract` option (not `step_location_method`) attributes the ctor env, so
a base ctor / inlined modifier wrapper / aux init helper doesn't misname it.

**Env carrier when no ctor args were recovered.** When a ctor reads env but
`ctor_args` is empty (e.g. `--focus-function` nondets them, or the args are
interface handles not captured as recovered scalars), a carrier call is
synthesized to hold the `vm.warp`/`vm.startPrank`. It routes through
`build_call(C, C, {})` — NOT a bare no-arg `sol_call` — so a **parameterized**
ctor never renders as an uncompilable `new C()`. `build_call` fills each slot
with a mock (interface) or a type default; the carrier then keeps the deploy
only if **every** argument is a faithful mock (or the ctor is parameterless →
`new C()`). If any slot fell back to a type default (`0`/`address(0)` — a guess a
ctor `require` could revert, breaking `setUp`), the deploy degrades to
UNSUPPORTED (`ctor_unrecovered`), reported as `Foundry: N deploy(s) UNSUPPORTED
(constructor args not recovered on this path)`. A UNSUPPORTED call's mock
arguments are not deployed (they would be orphaned). Regression:
`foundry_covgen_ctor_env_interface_only_fail` (interface-only ctor → `new
Gauge(<mock>)` under `vm.warp`, forge 2/2). Surfaced by St1inch's focus-mode
`constructor(IERC20, uint256, address)` which previously emitted an uncompilable
`new St1inch()`.

---

## 8. Emission (`write_foundry_file`)

### 8.1 Construction plan + grouping
`plan_of(tc)` builds one `inst` per distinct non-library contract (`c0`, `c1`,
…), marking `buildable` (not abstract/interface/library and ctor args rendered),
carrying `ctor_args`, `ctor_warp`, `deployer`, `ctor_value`, `value_unsendable`.
`sig_of(plan)` folds the construction signature (incl. env pins) into a string;
cases are **grouped by construction signature** so each group shares one `setUp`.
A ctor needing a different warp / deployer / value than another gets its own
group (own test contract `<Primary>CovTest_<gidx>`).

### 8.2 setUp
For each buildable instance: emit `vm.warp` (once, if any ctor reads time),
`vm.startPrank(deployer)` … `new C{value:}(args)` … `vm.stopPrank()`.
Non-buildable instances emit a specific `// UNSUPPORTED:` reason (abstract /
non-payable-value / unrenderable ctor arg).

### 8.3 Per-test-case body (`test_cov_<n>`)
For each non-ctor call:
- receiver = `Lib` (static) for a library, else the instance var.
- Env cheatcodes prepended (`vm.warp` / `vm.deal` / `vm.prank`) only in
  call-emitting branches.
- **UNSUPPORTED** (unrenderable arg or unbuilt instance) → comment, no call, no
  orphan `vm.deal`.
- **Detected revert** (`reverts`) → `vm.expectRevert(); c.m(args);` (loud on
  ESBMC↔forge divergence).
- **Library** → direct `Lib.fn(args)` (can't try/catch an inlined call; sound
  because library `require`/`revert` lowers to `__ESBMC_assume`, pruning the
  reverting branch — the recovered args always drive a non-reverting path).
- **Otherwise** (outcome unconfirmed) → `// [revert-tolerant]` +
  `try c.m(args) {} catch {}` (the only place a revert is silently tolerated —
  documented in the file header).

### 8.4 Imports
`import {C} from "./<src>.sol"` for every touched contract, plus file-level UDVT
types used as args. Always imports from the `.sol` source (forge compiles `.sol`),
never the `.solast`.

---

## 9. Deduplication (`fingerprint`)

Structurally-identical reconstructions collapse to one. The fingerprint folds:
contract.method, each arg literal, and — crucially — the env pins
(`{value:}`, `[warp:]`, `<prank:>`, `<dep:>`) so two counterexamples that differ
*only* in msg.value / timestamp / sender / deployer do **not** dedup to one case
(they cover distinct branch arms, e.g. the onlyOwner PASS vs FAIL arm).

---

## 10. Frontend stamps this generator depends on (contract with the frontend)

| Stamp | Where set | Read for |
|-------|-----------|----------|
| `sol:@C@<C>@F@<m>@<p>` naming | frontend symbol naming | `parse_param_symbol` |
| `#sol_type` (UINT256/ADDRESS/BOOL/CONTRACT/DYNARRAY/…) | `solidity_convert_type.cpp` | type extraction |
| `#sol_bytesn_size` (on type **and** on code_typet arg) | `get_elementary_type_name` / arg stamp | exact bytesN width |
| `#sol_udvt_name` | frontend | `Name.wrap(...)` |
| `#sol_contract` (with `#sol_type: CONTRACT`) | frontend | interface/contract handle (mock target) |
| `#sol_payable` | `get_function_definition` | `{value:}` legality |
| `#sol_no_new` | ctor symbol | non-instantiable |
| `#sol_library` | ctor symbol | static-call kind |
| `#sol_interface` | ctor symbol | mockable interface (§13) |
| `#sol_bytesn_size` (on a struct **component** irep) | `get_struct_class_fields` | bytesN struct-field width (§14) |
| `#sol_bases` | ctor symbol | base-instance dropping |
| `#sol_modifier_wrapper_for` | modifier wrapper symbol | wrapper→real method |
| `sol_revert_edge` (location bool) | `goto_coverage` (Phase A) | `vm.expectRevert` |

**Rule:** any change to these stamps must update both the frontend and the
matching reader here, and add/adjust a `regression/esbmc-solidity/foundry_*`
test.

---

## 11. Regression coverage (as of 2026-07-07)

`regression/esbmc-solidity/foundry_covgen_*` (+ `foundry_*`, goto-coverage
inert-check suites) — 102/102 green pre-mock-synthesis. Notable anchors:
`foundry_covgen_bytesN_fail`, `foundry_covgen_revert_fail`,
`foundry_covgen_require_revert_fail`, `foundry_covgen_library_udvt_fail`.

Each foundry-gen regression asserts the emitted `.t.sol` text (or a substring)
rather than running forge, so they're fast and hermetic. The end-to-end
forge round-trip lives in `notes/coverage-comparison/_foundry_roundtrip/`
(driver `roundtrip.sh`), not in ctest.

---

## 13. Interface-arg mock synthesis (`build_mock_spec`, shipped 2026-07-07)

A constructor/method taking an interface/abstract-contract handle cannot be
deployed with a bare address — the contract calls methods on the handle (e.g.
FarmingPool's ctor calls `stakingToken_.name()`), which reverts on a
code-less address and (for a ctor) fails the whole `setUp`. The generator
synthesizes a mock contract so the deploy succeeds.

**Detection.** A CONTRACT-typed argument (`effective_sol_type` = `"CONTRACT:<I>"`)
whose `<I>` is a **true interface** (`contract_is_interface`, reading the
frontend's `#sol_interface` stamp — set only for `contractKind == "interface"`).
An interface is guaranteed to have no constructor arguments and no abstract
receive/fallback, so `ESBMCMock_<I> is <I>` is always fully implementable. An
**abstract contract** (which may take ctor args, e.g. finding: `abstract A {
constructor(uint256) }`, or declare an abstract `receive`/`fallback`) is NOT
mocked — `is A` would fail to compile — nor is a **concrete contract** (mocking
would erase real side effects a later branch may depend on). Both degrade to
UNSUPPORTED.

**Enumeration (`build_mock_spec`).** `<I>`'s externally-visible methods are read
from the symbol table: code symbols `sol:@C@<I>@F@<name>#<node>` whose first
argument is the `this` self-pointer (this excludes events — `void(a,b,c)`, no
`this` — and non-code aux locals like `old_sender`). The only synthesized
code-with-`this` members an interface carries are the `$`-prefixed call helpers
(`$call`/`$send`/`$staticcall`/`$delegatecall`/`$transfer`) and its own ctor
(`name == iface`), so those are the **only** two exclusions — a legally
`_`-prefixed interface method is NOT excluded (verified: IERC20Metadata's
code-with-`this` set is exactly the 9 methods + 5 `$`-helpers + ctor). Inherited
methods are already **inlined** into the derived interface's `@F@` set, so no
base walk is needed. Signatures are deduped by `(name, param-type-list)`. A
name collision (the source already declares a contract `ESBMCMock_<I>`) degrades
to UNSUPPORTED rather than redeclare.

**Materialization caveat (load-bearing).** An interface surfaces in the symbol
table only when it is an **ancestor of an analyzed/instantiated contract**
(FarmingPool `is ERC20 is IERC20Metadata` → `IERC20Metadata` materializes). An
interface referenced *only* as a parameter type is dropped from the symbol
table. So enumeration is reliable for the FarmingPool-shaped case (the arg type
is in the contract's own inheritance chain) but returns **zero** functions for a
param-only interface. Zero functions → `renderable = false` → UNSUPPORTED
(conservative: we cannot prove `contract Mock is I {}` compiles). A future robust
source is the AST/ABI registry; deferred until a param-only interface needs it.

**Rendering (all-or-nothing).** For each method, a `pure`/`payable` `override`
stub returning the type default (`0` / `false` / `address(0)` / `""` / bytesN
zero). Mutability mirrors the interface: `payable` → `payable` (a `pure`
override of a payable fn is rejected by solc), everything else → `pure` (a valid
tightening of view/nonpayable). If **any** param or return type is unrenderable
(a struct/tuple return, an un-nameable type), or zero functions are found, the
whole interface is `renderable = false` and every deploy needing it is
UNSUPPORTED — a partial mock would not satisfy `is <I>` and would fail to
compile.

**Distinctness (fresh-per-slot).** Each interface argument gets its OWN mock
instance (keyed by parameter name): `mk_<I>_<param>`. ESBMC's recovered
construction path already satisfied any `a != b` guard, and a fresh instance per
slot (distinct deployed addresses) reproduces it — this is what FarmingPool's
`stakingToken_ != rewardsToken_` needs. **Limitation:** a constructor that
instead REQUIRES two interface parameters to be the SAME instance (`a == b`) is
not reproduced. The concrete `$address` the solver equated is **not recoverable**
from a pointer model value — `smt_conv.get()` on a contract-pointer nondet (RHS)
or its renamed LHS returns the *same* unconstrained symbol for both handles
(scalars concretize, pointers do not). That rare shape would revert `setUp`; it
is a documented residual, not a silent wrong-coverage claim.

**Emission.** One `contract ESBMCMock_<I> is <I> { <stubs> }` per interface used
(deduped file-wide), emitted after imports with an `[approx]` comment. `<I>` is
added to the imports. Each `ESBMCMock_<I>` instance is a state var declared +
deployed first in the group's `setUp` (before the contracts that receive it).

**Precision (residual, inherent).** Mock methods return fixed defaults, matching
ESBMC's *havoc* of external calls for every branch **except** one that depends
on a specific return value (`if (token.decimals() > 18)`): ESBMC explored both
arms via nondet, forge sees only the default, so that arm under-covers. The body
`try/catch` can silently hide the divergence. `[approx]` flags it. Never a wrong
or uncompilable test — only under-coverage.

**Visibility.** `generate()` logs `Foundry: N interface mock(s) synthesized`
and `Foundry: N contract-typed arg type(s) UNSUPPORTED (not mocked ...)`.

**Validation.** Round-trips end-to-end: **FarmingPool** compiles under forge
(`via_ir`), `setUp` deploys (ctor `.name()`/`.symbol()` hit the mock, two
distinct instances satisfy `!=`+nonzero), 4/4 tests PASS. Regression:
`foundry_covgen_interface_mock_fail` (2 interfaces synthesized),
`foundry_covgen_interface_mock_unsupported_fail` (struct-return interface → all-
or-nothing UNSUPPORTED).

## 14. Struct-literal argument rendering (`format_struct_literal`, shipped 2026-07-07)

A method taking a struct (EscrowDst `publicWithdraw(bytes32, IBaseEscrow.Immutables
calldata)`) previously degraded the WHOLE call to UNSUPPORTED. The generator now
renders the struct as a positional Solidity literal `<Qualified>(f0, f1, …)`.

**Value recovery.** `smt_conv.get` on a struct param returns a clean
`constant_struct2t` whose `datatype_members` hold the field values (in declared
order incl. padding).

**Field source types come from the DECLARED tag, not the value.** The recovered
value's migrated type is stripped (UDVT→bare `unsignedbv`, local struct name
only). `format_struct_literal` finds the declared tag symbol `tag-struct
<Qualified>` by matching the value's local name (exact, or as the `.<local>`
suffix of a contract-scoped name), reads its `struct_typet` components (which
retain `#sol_type`/`#sol_udvt_name`), and **zips them index-for-index against the
recovered members** (guarding equal count + non-null members). The local-name
match **requires uniqueness**: if two structs share a local name across scopes
(`I1.S` / `I2.S`) the parameter's declared type is an inline struct carrying only
the local name too, so the correct tag is undeterminable — it degrades to
UNSUPPORTED rather than pick the wrong one (which would emit a wrong-typed,
uncompilable literal). Duplicate-local-name structs are a documented residual.

**Padding.** ESBMC injects `anon_pad$*` components (Order: 7 comps for 4 fields;
Immutables: 10 for 9). These are skipped — the recovered members and declared
components align at the same indices (padding present in both).

**Per-field rendering** reuses the existing formatters: UDVT→`Name.wrap(N)`,
address→`address(uint160(N))`, uint/int→decimal, `bytes`→`hex""`, and a nested
user struct **recurses**. A field's UDVT type / the struct scope are collected
into `out_imports` so the test imports them (`import {Money}`,
`import {IBaseEscrow}`).

**bytesN struct field — the frontend stamp.** A `bytes32` field lowers to a
generic `BytesStatic` struct and loses `#sol_bytesn_size` (a type-follow drops
the stamp that the symbol-type wrapper carried). Fix: `get_struct_class_fields`
(`solidity_convert_decl.cpp`) re-stamps the width from the AST typeString onto
the **component irep** (survives the follow, unlike a stamp on the component
type). `format_struct_literal` recovers the width from the component and renders
`bytesN(0x..)`. The stamp is guarded to `bytesN` fields only (inert for every
other field type, so it can't perturb non-bytesN struct type identity).

**All-or-nothing.** Any field that cannot render — a fixed/dynamic **array**
field (a dynamic `new T[](4)` default is illegal for a fixed field), an
unrenderable nested type, an unconstrained bytesN member — degrades the WHOLE
struct to "" → the call is UNSUPPORTED. Never a partial/wrong literal.

**Detection guard.** A user struct arg is detected by the recovered value being
a `constant_struct` whose type tag is NOT exactly `BytesStatic`/`BytesDynamic`
(`is_bytes_wrapper_struct`, an EXACT name check, not a substring — a legit user
struct like `BytesBundle` must not be excluded) — because `bytesN` (BytesStatic)
and `bytes` (BytesDynamic) values are ALSO `constant_struct`s and have their own
renderers (regression: `foundry_covgen_bytesN_fail` caught the mis-routing). The
same exact check gates the nested-struct recursion.

**Visibility.** `generate()` logs `Foundry: N struct-literal arg(s) rendered`.

**Validation.** `ST.place(Order{bytes32,UDVT,address,uint})` and
`IM.exec(Immutables-shape{bytes32×2, Address/Timelocks UDVTs, uint, bytes})` both
forge-compile + 2/2 tests PASS — the latter is EscrowDst's exact field-type set,
so EscrowDst's method arg now renders (its full round-trip still needs a buildable
ctor + timelock env — Phase 2). Regressions: `foundry_covgen_struct_arg_fail`
(2 struct literals rendered), `foundry_covgen_struct_arg_unsupported_fail`
(fixed-array field → UNSUPPORTED).

## 12. Known limits / open features (pointer to roadmap)

1. **Interface-arg mock synthesis** — **SHIPPED 2026-07-07** (§13). FarmingPool
   round-trips. Residuals: `a==b`-required interface ctors; param-only
   (non-materialized) interfaces; return-value-dependent branches ([approx]).
2. **Struct-literal args** — EscrowDst `Immutables` (nested UDVTs + `bytes`).
3. **Full environment reconstruction** beyond ③A0 — access-token balances,
   multi-actor prank sequences.
4. **Whole-unit solver scale** — Aqua's 7/8 whole-unit run times out; focus-fn
   reproduces a subset. Orthogonal to generation.
5. **Nondet-return-dependent branches behind havoc'd external calls** — a fixed
   mock cannot reproduce a branch that depends on a havoc'd return value
   (fundamental precision loss; degrade + `[approx]`, never wrong).
