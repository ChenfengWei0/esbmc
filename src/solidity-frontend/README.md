# ESBMC Solidity Frontend

The ESBMC Solidity frontend verifies smart contracts written in
Solidity. It parses the Solidity AST (produced by `solc
--ast-compact-json`), lowers it to ESBMC's intermediate representation,
synthesises a verification harness, and hands the goto program to
ESBMC's bounded / k-induction engine and its SMT backends.

This README is the user-facing overview. Deeper references live in the
repo:
- [`CLAUDE_Solidity.md`](../../CLAUDE_Solidity.md) — index of
  per-topic developer docs under `docs/claude/solidity/`.
- [`../../CLAUDE_COVERAGE.md`](../../CLAUDE_COVERAGE.md) — coverage
  pipeline reference.
- [`../../scripts/minimise/ALGORITHM.md`](../../scripts/minimise/ALGORITHM.md) —
  minimiser implementation spec.

## Table of contents

- [Quick start](#quick-start)
- [Solc invocation](#solc-invocation)
- [Supported Solidity versions](#supported-solidity-versions)
- [Choosing what to verify](#choosing-what-to-verify) — `--contract`, `--function`, `--focus-function`
- [External-call resolution](#external-call-resolution) — `--bound` vs default unbound
- [Property selection](#property-selection) — overflow, reentry, multi-property, standard checks
- [Solver selection](#solver-selection) — automatic preference, `--16` word size, Z3/CVC5/Bitwuzla/Boolector
- [TOD detection](#tod-transaction-order-dependence-detection) — `--tod-race-check`, `--tod-balance-check`, `--dump-harness`
- [Counter-example minimisation](#counter-example-minimisation) — `esbmc-minimise`, `--dump-violation-info`
- [Structural coverage](#structural-coverage) — branch / condition / assertion coverage
- [Externally exposed intrinsics](#externally-exposed-intrinsics) — `__ESOL_deep_copy`, `__ESOL_nondet_state_forward`, `__ESBMC_assert`, `__ESBMC_assume`, `__ESBMC_nondet_*`
- [Approximations you should know about](#approximations-you-should-know-about)
- [Building](#building) — CMake options, Bitwuzla prerequisites, static release build
- [Developer notes](#developer-notes)

---

## Quick start

Pass a `.sol` file directly to ESBMC — it will automatically find and
invoke `solc`:

```sh
esbmc example.sol --contract MyContract
```

ESBMC searches for `solc` in this order: `--solc-bin <path>` > `$SOLC`
env var > `solc` in `$PATH`. You can pin a particular solc:

```sh
esbmc --solc-bin /path/to/solc example.sol --contract MyContract
```

### Manual AST generation (legacy)

You can also generate the AST yourself and pass both files. Use `--sol`
to tell ESBMC the original source (needed for pretty-printing and
source-location mapping):

```sh
solc --ast-compact-json example.sol > example.solast
esbmc --sol example.sol example.solast --contract MyContract
```

## Solc invocation

Auto-invoke (`.sol` input):

1. ESBMC resolves `solc` using the search order above.
2. ESBMC runs `solc --ast-compact-json <input>` and captures stdout to
   a temp AST file.
3. ESBMC prints the discovered path and version, e.g. `Compiling
   Solidity AST using: /usr/local/bin/solc (v0.8.30)`.
4. The temp AST is consumed like a manually-generated `.solast`.

On `solc` error, ESBMC prints the compiler's diagnostics and exits
without attempting verification.

## Supported Solidity versions

Version support is checked at the AST level via the top-level
`PragmaDirective`:

| pragma | Behaviour |
|---|---|
| Upper-bound strictly `< 0.5.0` (e.g. `pragma solidity ^0.4.24;`) | **Rejected.** Frontend prints `unsupported version` and exits. |
| Lower-bound `<0.5.0` but upper-bound admits `>=0.5.0` (e.g. `pragma solidity >=0.4.0 <0.9.0;`) | **Warning, accepted.** solc has already chosen a modern compiler for the file — ESBMC trusts solc's output. |
| `0.5.0 – 0.7.0` | **Warning.** May cause unexpected behaviour; AST/semantic shapes differ in minor ways from 0.8. |
| `>= 0.7.0` | **Fully supported.** Developed and tested against 0.8.x (current test baseline: solc 0.8.30). |

Implementation: `check_min_version` in `solidity_convert.cpp`. If a
contract in your codebase uses pre-0.5 syntax, upgrade the source and
recompile with a modern solc rather than falling back to `solc-select`
on an old compiler — see the user memory feedback
`feedback_upgrade_to_08.md`.

## Choosing what to verify

ESBMC Solidity has **two orthogonal axes** for scoping a verification
run:

1. What contract/function is the entry point?
2. How are external calls resolved?

This section covers axis 1. See [External-call resolution](#external-call-resolution) for axis 2.

### Default — verify every declared contract

```sh
esbmc contract.sol
```

ESBMC constructs a multi-contract wrapper `_ESBMC_Main` that drives
each contract's own `_ESBMC_Main_<C>` harness (constructor + nondet
public-method dispatch loop). Fastest smoke test; all soundness caveats
of `--contract` compound across contracts.

### `--contract <ContractName>` (recommended)

```sh
esbmc contract.sol --contract MyContract
```

Narrow verification to a single contract. Constructor runs first;
state variables take their declared/initialiser values; every
public/external method is dispatched nondet-guarded inside a
`while (nondet_bool())` loop.

To target multiple contracts in one run, either repeat the flag or use
a space-separated list:

```sh
esbmc contract.sol --contract A --contract B
esbmc contract.sol --contract "A B"
```

### `--focus-function <name>` — narrow to one function, keep the harness

```sh
esbmc contract.sol --contract MyContract --focus-function myFunc
```

Same harness as `--contract MyContract`, but the nondet dispatch loop
inside `_ESBMC_Nondet_Extcall_MyContract` filters out every method
except `myFunc`. The constructor still runs; state variables still
receive their initialisers; `myFunc` can still be re-entered during its
own execution (via the filtered dispatch loop). This is a pure
verification-cost optimisation: the safety proof strength is the same
as `--contract MyContract` *for the paths through `myFunc`*.

Requires `--contract` when the source declares more than one
(non-library, non-interface) contract; auto-inferred when there is
exactly one.

**Use `--focus-function` instead of `--function` in regression tests.**
It preserves the full post-constructor harness, so adversarial tests
still exercise the dispatch logic and the frontend/solver weaknesses
they are designed to expose.

### `--function <name>` — single-function, nondet state

```sh
esbmc contract.sol --contract MyContract --function myFunc
```

Makes `myFunc` the GOTO entry point and calls it once with nondet
parameters. **The constructor does NOT run.** All state variables
start as unconstrained nondet of their declared type.

- **`VERIFICATION SUCCESSFUL`** under `--function` is a *stronger*
  safety proof than under `--contract` / `--focus-function`, because
  it proves the function safe under *every* conceivable initial state
  (strictly a superset of constructor-reachable states).
- **`VERIFICATION FAILED`** under `--function` may be a false positive
  — the counterexample could rely on a state unreachable from any
  `constructor() → (any tx sequence)`. Re-verify with `--focus-function`
  before trusting the trace.

Best for: quick safety checks on `pure` / `view` functions; over-
approximate proofs for one function. **Do not use `--function` in
regression tests** — it fabricates nondet state, which hides real
frontend/solver weaknesses. See
[`docs/claude/solidity/modes.md`](../../docs/claude/solidity/modes.md)
for the full soundness discussion.

Combining `--function` and `--focus-function` is rejected at
conversion time — the two flags express different intents.

### `--no-visibility`

Forces verification of *every* function, including internal/private
functions that would otherwise be unreachable from the nondet dispatch
harness. Rarely useful outside developer debugging.

### Summary — which flag to pick

| Goal | Flag combination |
|------|------------------|
| Whole contract, realistic state, all functions | `--contract C` |
| One function, realistic state | `--contract C --focus-function f` |
| One function, over-approximate state | `--contract C --function f` (not in tests) |
| Multi-contract system with known callees | `--contract A --contract B --bound` |
| Quick smoke test of all contracts | (no `--contract`, no `--function`) |

## External-call resolution

### Default: unbound

```sh
esbmc contract.sol --contract C --unbound    # or just --contract C
```

Each contract is verified in isolation. Low-level calls (`.call()`,
`.delegatecall()`, `.staticcall()`) return a nondet `(bool, bytes)`
tuple; their side effect is a nondet re-entry into the **current**
contract's dispatch loop; the target address is ignored. This is an
**over-approximation** for reentrancy detection (SWC-107), which is
what enables classic reentrancy-bug discovery even without a concrete
attacker contract.

### `--bound` — trusted closed-world

```sh
esbmc contract.sol --contract A --contract B --bound
```

Contracts declared in `contractNamesList` are linked together under a
**trusted closed-world assumption**, analogous to SMTChecker's
`--model-checker-ext-calls=trusted` mode. External calls to addresses
matching a known `_ESBMC_Object_X.$address` dispatch to that
contract's nondet dispatch; other addresses produce hard `return false`
(an **under-approximation** relative to real EVM, but sound under the
trusted assumption).

| Aspect | Unbound (default) | `--bound` |
|---|---|---|
| Reentrancy bugs (SWC-107) | ✓ (nondet self-reentry) | ✓ (plus concrete cross-contract dispatch) |
| Cross-contract state invariants | ✗ (contracts isolated) | ✓ |
| `delegatecall` storage context | nondet | ✓ via shadow fast path (literal `abi.encode*` only — see below) |
| `staticcall` read-only enforcement | nondet | ✓ via snapshot + rollback |
| Attacker-controlled arbitrary address | ✓ (nondet reentry models it) | ✗ (unknown-address path pruned) |
| Typical wall-time | Faster | Slower; more SMT constraints per contract |

**Low-level call semantic notes (bound mode):**
- `.call(abi.encodeWithSignature("f(T)", args))` — dispatches to the
  target's `f(T)` and returns `(true, BytesDynamic)`.
- `.delegatecall(...)` — fast path inlines the target body into the
  caller's storage context when the payload is
  `abi.encodeWithSignature` / `abi.encodeWithSelector` /
  `abi.encodeCall` **literal** and every state variable the target
  touches exists on the caller with the same name+typeString.
  Falls back to the generic `$delegatecall#0` dispatcher on any miss.
- `.staticcall(...)` — the target struct is snapshotted before
  dispatch and restored after, so any writes the target performs are
  silently invisible to the caller (correct under real EVM, which
  reverts on writes inside staticcall). Does not currently *surface*
  the "target tried to write" event — sound but loses that specific
  detection.

See [`docs/claude/solidity/modes.md`](../../docs/claude/solidity/modes.md)
for the full dispatch matrix and limitations.

## Property selection

### Common checks (enable as needed)

```sh
esbmc contract.sol --contract C --overflow-check --div-by-zero-check
```

| Flag | What it enables |
|------|-----------------|
| `--overflow-check` | Signed + unsigned overflow/underflow on all integer widths. |
| `--unsigned-overflow-check` | Unsigned-only variant. |
| `--div-by-zero-check` | Division by zero. |
| `--no-bounds-check` | Suppress array-bounds checks. |
| `--no-pointer-check` | Suppress pointer-validity checks. |
| `--no-standard-checks` | Turn off the default claim set (useful when you only want a specific check). |

The Solidity frontend automatically injects the extra checks needed
for faithful EVM semantics:
- **Sub-256-bit overflow** — `uint8`/`uint16`/`uint32` overflow is
  caught despite C integer promotion; implemented as a narrowing-cast
  check in `goto_check.cpp` when the source is `.sol`.
- **`unchecked { ... }` blocks** (Solidity 0.8+) — overflow assertions
  inside `unchecked` blocks are tagged with `#sol_unchecked` and
  skipped.

### `--reentry-check`

```sh
esbmc contract.sol --contract C --reentry-check
```

Instruments every `.call()` with assertions that capture potential
reentrant entry traces. A reported reentrancy is an **indicator**
(the trace shows a path where reentrancy is possible), not a
confirmed exploit. Inspect the counterexample to decide whether it
translates to an actual loss-of-funds scenario.

### `--multi-property`

Report **all** reachable property violations in a single run rather
than stopping at the first one. Useful for comprehensive scans.

```sh
esbmc contract.sol --contract C --overflow-check --multi-property
```

### `--negating-property <function>`

Rewrites every `assert(cond)` in the named function to `assert(!cond)`.
Handy for reachability queries.

## Solver selection

When the user passes no explicit solver flag, the Solidity frontend
auto-picks the first available backend in this order:

1. Bitwuzla (fastest on 256-bit bitvectors and mapping chains)
2. CVC5
3. Boolector
4. Z3

If you need a specific backend:

```sh
esbmc contract.sol --contract C --z3
esbmc contract.sol --contract C --cvc5
esbmc contract.sol --contract C --bitwuzla
esbmc contract.sol --contract C --boolector
```

**Empirical rules of thumb:**
- Z3 struggles with 256-bit QF_BV and often aborts with
  `datatype is not well-founded` on recursive Solidity structs. Avoid
  as a default for production scans.
- Bitwuzla is the fastest default but warns (and can abort) on
  equality over constant arrays. If a mapping-heavy contract aborts
  with `ERROR: SMT solver failed`, retry with `--cvc5`.
- CVC5 is the most consistent on the 1inch-scale benchmarks but
  slower.

### `--16` — reduce word size

```sh
esbmc contract.sol --contract C --16
```

Sets the machine word to 16 bits. Substantially faster on
mapping-heavy contracts (where keys would otherwise force 256-bit
expensive reasoning), at the cost of precision loss for addresses and
large uint arithmetic. **Not sound in general** — use only for
scaling triage, never for final verdicts on over/underflow properties.

Note: the minimum supported machine word is 16, so overflow/underflow
checks for `uint8` and `int8` are not available under `--16`.

## TOD (Transaction Order Dependence) detection

Detect pairs of functions that, when executed in different orders
from the same initial state, produce different final states — the
classic TOD vulnerability pattern.

```sh
# Auto-discover state-race pairs on a contract
esbmc contract.sol --contract C --tod-race-check

# Target a specific pair
esbmc contract.sol --contract C --tod-race-check=f1,f2

# Same two modes for balance-TOD (requires --bound)
esbmc contract.sol --contract C --tod-balance-check --bound
esbmc contract.sol --contract C --tod-balance-check=f1,f2 --bound
```

| Option | Modes | Purpose |
|--------|-------|---------|
| `--tod-race-check[=auto\|f1,f2]` | TransRacer-style storage-race TOD. Any public state variable in the pair's shared footprint is asserted equal across the two orderings. |
| `--tod-balance-check[=auto\|f1,f2]` | ETH-balance TOD. `address(this).balance` compared across the two orderings. Requires `--bound` + the EOA balance model. |
| `--dump-harness` | Print the generated Solidity harness to stdout and exit without verifying. Useful for inspecting / debugging the harness. |
| `--tod-jobs N` | Parallel ESBMC subprocesses in `auto` mode. Defaults to `min(hardware_concurrency, pair_count)`. Use `1` for sequential. |

The generator lives in
`src/solidity-frontend/solidity_tod_{analysis,harness}.{cpp,h}`.

Full pipeline (auto mode):
1. R/W footprint analysis per public function + call-graph closure.
2. Enumerate candidate pairs where footprints intersect.
3. Emit a multi-harness `.sol` with two **renamed** copies of the
   target contract (ESBMC's singleton aliasing requires separate
   names per copy); one `TOD_<a>_<b>` harness contract per pair.
4. Run ESBMC on each `TOD_<a>_<b>` contract in parallel.
5. Summarise: `N pair(s) — X clean, Y TOD found, Z error`, exit non-zero
   if any pair fails.

See [`docs/claude/solidity/tod.md`](../../docs/claude/solidity/tod.md)
for the algorithm details and the list of known limitations.

## Counter-example minimisation

For real-world contracts where the reported counterexample trace is
unreadable against hundreds of lines of context, use the three-phase
minimiser to shrink the source while preserving the exact violation
oracle:

```sh
# 1. Run ESBMC and dump the violation oracle
esbmc contract.sol --contract C --overflow-check --cvc5 \
      --incremental-bmc \
      --dump-violation-info /tmp/violation.json

# 2. Feed the oracle to the minimiser
python scripts/minimise/minimise.py \
    --input contract.sol \
    --oracle /tmp/violation.json \
    --out reduced/ \
    --esbmc-flags "--contract C --overflow-check --cvc5 --incremental-bmc"
```

Three phases:
- **Phase 0 — dead-code sweep**: delete contracts / functions / state
  variables / modifiers that can't contribute to the reported violation.
- **Phase 1 — compile-driven closure**: re-add the minimum set of
  declarations that `solc` needs to compile the reduced source.
- **Phase 2 — greedy reduction**: delete statements / locals that do
  not change the violation oracle; keep-last on any change that
  breaks the oracle.

Output: reduced source + `manifest.json` describing what was removed
and why.

ESBMC-side flag: `--dump-violation-info <path>` — writes a structured
JSON oracle (contract, function, bug_type, relative_loc, trace
functions, locked symbols) consumed by the minimiser.

Full spec: [`scripts/minimise/ALGORITHM.md`](../../scripts/minimise/ALGORITHM.md).
User overview: [`docs/claude/solidity/minimise.md`](../../docs/claude/solidity/minimise.md).

## Structural coverage

ESBMC supports all four coverage criteria on Solidity:

```sh
# Branch coverage (use --focus-function to narrow the instrumentation surface)
esbmc contract.sol --contract C --focus-function myFunc \
  --branch-coverage-claims --unwind 10 --no-unwinding-assertions

# Condition coverage (all user functions)
esbmc contract.sol --contract C \
  --condition-coverage-claims --unwind 10 --no-unwinding-assertions

# Assertion coverage
esbmc contract.sol --contract C --focus-function myFunc \
  --assertion-coverage-claims --unwind 10 --no-unwinding-assertions

# Branch + function entry
esbmc contract.sol --contract C \
  --branch-function-coverage-claims --unwind 10 --no-unwinding-assertions

# JSON report for CI or HTML rendering
esbmc contract.sol --contract C --branch-coverage --cov-report-json
python3 scripts/cov-report.py cov-report.json -o cov-report-html
```

Solidity-specific handling:
- The `_ESBMC_Main_*` multi-transaction loop is **automatically
  neutralised** in coverage mode (back-edges converted to SKIPs), so
  each user function executes exactly once.
- `__ESBMC_HIDE`-labelled auxiliary functions (~20 per contract:
  constructors, dispatchers, mapping helpers, ...) are excluded from
  the instrumentation surface.
- Coverage output is pretty-printed back to Solidity syntax: `(signed
  int)y < 50` displays as `y < 50`, `msg_sender` as `msg.sender`,
  `this->owner` as `owner`, etc.
- `require(cond)` is not counted as a branch (modelled as
  `__ESBMC_assume`).
- Modifier-renamed functions (e.g. `deposit_onlyPositive`) are
  prefix-matched, so `--focus-function deposit` targets the modified
  form.

See [`../../CLAUDE_COVERAGE.md`](../../CLAUDE_COVERAGE.md) for the full
coverage architecture.

## Externally exposed intrinsics

The Solidity frontend recognises several user-visible identifiers
that the verifier intercepts with special semantics. These are
intended to be used inside **harness contracts** or **test scaffolding**
that the author writes alongside the contract under verification.

| Identifier | Signature (conceptual) | What ESBMC does with it |
|---|---|---|
| `__ESOL_deep_copy(C src)` | `C → C` | Replaces with `_ESBMC_clone_<C>(src)` — allocates a fresh contract instance, copies every field (including mapping `.addr` retargeting, fixed-array slab reallocation, multi-dim arrays), mints a new `$address`. See [`docs/claude/solidity/operational-models.md`](../../docs/claude/solidity/operational-models.md) for per-field semantics. |
| `__ESOL_nondet_state_forward(C c)` | `C → void` | Drives `*c` through a nondet dispatch loop over `C`'s public/external methods. After the call, `*c` represents some reachable (not just initial) state. Internal / private functions are NOT invoked. |
| `__ESBMC_assume(expr)` | `bool → void` | Adds `expr` as a path constraint. Unreachable paths are pruned — behaves like Solidity's `require`, but without the revert semantics. Also a VSA hint for `p != 0` pointer guards. |
| `__ESBMC_assert(expr, msg)` | `bool, string → void` | User-level assertion. |
| `__VERIFIER_assume` / `__VERIFIER_assert` | same as `__ESBMC_*` | Accepted for compatibility with SV-COMP style stubs; identically handled. |
| `__ESBMC_nondet_*()` | `() → T` (T = declared return type) | Returns a fresh nondet value of the call's declared return type. Any name beginning with `__ESBMC_nondet_` is recognised — the suffix is for documentation only. Use when an instrumenter needs to inject a fresh nondet at a specific program point without changing function signatures (e.g. self-composition oracles for miner-timestamp / hyperproperty checks where neither a parameter nor a state variable is a viable injection site — parameters break internal callers, state variables start at the post-constructor default in `--contract` mode rather than being havoc'd). |

Users are expected to declare solc-compile-compatible stubs in the
source; ESBMC replaces the stub bodies at verification time. Example
stubs the TOD harness generator uses:

```solidity
function __ESOL_nondet_state_forward(C c) { assembly {} }
function __ESOL_deep_copy(C src) pure returns (C) { return src; }
function __ESBMC_nondet_uint() internal pure returns (uint256) {}
function __ESBMC_nondet_bool() internal pure returns (bool) {}
function __ESBMC_nondet_address() internal pure returns (address) {}
```

## Approximations you should know about

ESBMC Solidity makes deliberate soundness / completeness trade-offs.
Every in-source approximation carries an `[APPROX: OVER]` or
`[APPROX: UNDER]` marker; grep `\[APPROX:` to find them all.

Highlights (non-exhaustive, see
[`docs/claude/solidity/approximation-ledger.md`](../../docs/claude/solidity/approximation-ledger.md)
for all 21 entries):

| Feature | Direction | Consequence |
|---|---|---|
| `keccak256` / `sha256` / `ripemd160` / `ecrecover` | OVER+UNDER | Deterministic bijective (`~x`, `~(x+1)`, …). Same input → same output ✓, distinct inputs → distinct outputs ✓. Concrete hash values NOT computed; preimage/collision/signature-forgery properties NOT refutable. `ecrecover` ignores `(v,r,s)`. |
| `abi.encode*` | OVER+UNDER | Identity on the first argument; remaining arguments evaluated for side effects only. Packed layout properties unprovable; selector-dispatch checks with the same first arg look equal. |
| `abi.decode` | OVER | Nondet uint256. Round-trip `decode(encode(x)) == x` NOT provable. |
| `msg.*` / `tx.*` / `block.*` | OVER | All fields nondet per access. `block.number` not monotonic across reads. |
| Unbound external calls (`addr.call`, etc.) | OVER | Returns nondet; side-effect = nondet re-entry into current contract. Target address ignored. Enables reentrancy detection; cross-contract effects invisible. |
| Bound-mode `delegatecall` | UNDER (partial fix) | Fast path shadow-inlines target body into caller context, but only for literal `abi.encode*` payloads with matching state-var layouts. Proxy / UUPS / Diamond storage-slot patterns fall back to generic dispatcher. |
| `revert` / `require` / failed `transfer()` | UNDER state, OVER control | Emits `__ESBMC_assume(false)`: path pruned but SSA writes before the revert are NOT rolled back. `try/catch` bodies pruned with the try arm. |
| `selfdestruct` | UNDER | Modelled as `exit(0)` — post-selfdestruct behaviour (address reuse, CREATE2 re-deploy) not explored. |
| Inline assembly / Yul | OVER | All externally-referenced variables havoc'd to nondet. Assembly-enforced invariants unprovable. |
| `IndexRangeAccess` slices (`b[s:e]`) | OVER | Fresh nondet value; no parent-array constraint, no `s <= e <= length`. |
| `type(I).interfaceId` / `creationCode` / `runtimeCode` | OVER | Nondet bytes4 / bytes. |
| `address(new C())` uniqueness | UNDER | 16-slot if-chain caps pairwise distinctness at the 16th allocation; the 17th `new C()` on a path is unconstrained vs prior addresses. Loose default; opt into precise modelling via `--solidity-precise` (see below). |

### Address uniqueness modelling — loose default vs `--solidity-precise`

`_ESBMC_get_unique_address` (the helper called from every contract
constructor to hand out a fresh address) ships in two variants:

- **Default — 16-slot unrolled if-chain.** Loop-free, so `--unwind`
  does not truncate the uniqueness constraints. Sound for the first
  16 contract instantiations on any path; the 17th is unconstrained
  (silent under-approximation).
- **`--solidity-precise` — `for`-loop linear scan over `sol_max_cnt`.**
  No slot cap. The loop is bounded by the runtime contract-allocation
  count, so the user must pass `--unwind N` with N ≥ the number of
  contract instantiations on any path. Without `--no-unwinding-assertions`,
  a too-low `--unwind` produces a visible "unwinding assertion loop
  &lt;id&gt;" failure that surfaces the bound explicitly. With
  `--no-unwinding-assertions`, the loop tail is silently truncated
  — same blind spot as `_ESBMC_get_addr_array_idx` and other linear
  scans in the address library.

**Why the loose form is the default.** The `--unwind` coupling of
the precise variant is awkward in practice: many tests legitimately
use `--unwind 1` or `--unwind 2` to control dispatcher exploration
depth, and bumping `--unwind` to satisfy address uniqueness changes
the entire test's path-exploration semantics. The 16-slot if-chain
decouples uniqueness from `--unwind` at the cost of a hard cap that
no regression test in the suite has ever exceeded.

A quantifier-based form (`__ESBMC_forall`) was investigated and
rejected — cvc5 returns `unknown (INCOMPLETE)` on every standard
quantifier strategy and only `--sygus-inst` succeeds (which conflicts
with incremental mode and is intractably slow on k-induction);
bitwuzla's BV-quantifier engine is also slow on related patterns.

**When to opt in.** Pass `--solidity-precise` if your contract
allocates more than 16 instances on any path AND your property
depends on address-distinctness. Pair it with `--unwind N` where N
covers your maximum on-path allocation count. Drop
`--no-unwinding-assertions` while debugging so insufficient unwind
shows up as a real warning instead of silent truncation.

**Future under-approximations bind to the same flag.** As we replace
more loose modellings with precise (sound) ones, they will be
controlled by `--solidity-precise` so users get one knob rather than
many.

**Consequences for review:**
- `VERIFICATION SUCCESSFUL` is a real safety proof *within* the
  approximation ledger. If the property depends on an approximation
  marked "False negatives", the proof is NOT sound — re-verify with a
  tighter model or manual reasoning.
- `VERIFICATION FAILED` may be a spurious counterexample if it
  depends on an approximation marked "False positives" (e.g.
  row 7 — nondet `msg.value` at harness entry; row 5 — non-monotonic
  `block.number`; row 16 — cross-iteration state visibility across
  reverts). Check the counterexample trace against the ledger before
  filing a bug.

## Building

### Minimal Solidity build

```sh
cd build
cmake .. -DENABLE_SOLIDITY_FRONTEND=ON -DENABLE_REGRESSION=ON
cmake --build . -j$(nproc)
```

Both `ENABLE_SOLIDITY_FRONTEND` and `ENABLE_REGRESSION` must be ON.
The default `./scripts/build.sh` sets `ENABLE_REGRESSION=OFF`, so
regression tests won't appear in `ctest -N` unless explicitly
enabled.

### Bitwuzla (recommended default, auto-selected)

Required system packages on Ubuntu/WSL:

```sh
sudo apt install -y libgmp-dev libmpfr-dev
pip install --user --break-system-packages meson ninja
cmake .. -DENABLE_BITWUZLA=ON -DDOWNLOAD_DEPENDENCIES=ON
```

`libgmp-dev` and `libmpfr-dev` are required by Bitwuzla's upstream
build; `meson`/`ninja` drive the compile.

### CVC5 (needed for some 256-bit tests)

```sh
cmake .. -DENABLE_CVC5=ON -DDOWNLOAD_DEPENDENCIES=ON
```

### Static release build (shippable binary)

```sh
./scripts/build.sh -S ON -b Release build install
```

`-S ON` is the Linux default in `build.sh`; produces a statically
linked binary under `./release/bin/esbmc` that can be shipped to
machines without matching shared-library versions. Requires the build
dependencies above (`libgmp-dev`, `libmpfr-dev`, `meson`, `ninja`, plus
the ones `build.sh` installs via apt).

### Testing

```sh
cd build
# All Solidity tests
ctest -j$(nproc) -L esbmc-solidity --output-on-failure

# A specific test
ctest -R "regression/esbmc-solidity/address_1"

# Skip slow tests during development
ctest --timeout 60 -L esbmc-solidity
```

See [`docs/claude/solidity/testing.md`](../../docs/claude/solidity/testing.md)
for the test baseline, the list of slow THOROUGH tests, and the
adversarial / stress-test suites.

## Developer notes

- **Solidity → C IR**: Each contract is lowered to a C struct.
  Library contracts register as code-only symbols. Inheritance is
  handled via AST merging (C3 linearisation follows solc).
- **Blockchain state** is encoded as per-contract `$address`,
  `$balance`, `$bind_cname` struct fields. In the real EVM, balance
  is bound to an address; ESBMC binds balance to each contract
  instance.
- **Operational models** (`src/c2goto/library/solidity/`) are
  pre-compiled into a separate `sol64.goto` via the c2goto pipeline
  and embedded in the ESBMC binary. When adding a new `.c` model file,
  list its exported function names in `solidity_c_models` in
  `cprover_library.cpp`.
- **Pretty-printing** of intermediate C expressions back to Solidity
  syntax is applied at display time only (counterexamples, coverage
  output). Claim matching and internal reasoning use the original C
  strings.
- **Hook for debugging counterexamples**: dump the goto program with
  `--goto-functions-only` to see what the frontend actually emits;
  compare against an equivalent C program verified through ESBMC's C
  frontend to isolate frontend vs C-model vs backend bugs. See
  [`docs/claude/solidity/architecture.md`](../../docs/claude/solidity/architecture.md).
- **Contribute a regression test** whenever you fix a bug: every
  Solidity PR should add at least one PASS and one FAIL test case
  under `regression/esbmc-solidity/`.
