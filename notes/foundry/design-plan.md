# Plan v2: ESBMC ↔ Foundry test support (verify + emit)

> **v2 correction (branch).** The v1 Explore agents and the Codex adversarial review both
> ran against `perf/solidity-ast-index` (a ~6-week-stale checkout), so several v1/Codex
> conclusions are artifacts of a stale tree — most importantly Codex's "biggest risk: revert
> is unobservable / no `__ESBMC_reverted` exists." **On the mainline `solidity` branch a full
> revert-observation primitive already exists.** All file:line below verified on `solidity`.

Two end-goal features on the Solidity frontend:

- **F1 — Verify a Foundry test**: given a reference contract + a Foundry test contract, decide
  whether the test is *correct*, **conservatively** (report CORRECT unless *definitely* wrong;
  the user can always run `forge test`). Concrete replay first, then PUT (parameterized/fuzz).
- **F2 — Emit a Foundry test**: from an ESBMC counterexample, generate a compilable `*.t.sol`.
  A `--generate-*-testcase` family exists (XML/pytest/ctest); add a Foundry emitter. Fixed-value first.

---

## 0. Grounding (verified on `solidity` branch)

### 0.1 Revert observation ALREADY EXISTS (overturns Codex #1)

`docs/claude/solidity/revert-observation.md` + code:

- Global flag `_ESBMC_sol_reverted_flag` (`solidity_misc.c:226`), NOT contract state (survives `*this` rollback).
- `_ESBMC_sol_mark_revert()` is emitted at **every captured revert site — `revert` / `require`-false /
  `revert CustomError()`** — inside `build_revert_rollback_block`; the revert lowering is
  **"mark flag → rollback `*this` → `return <nondet>`", NOT `assume(false)`**, so the path *survives*
  and the caller can observe it (`solidity_convert_modifier.cpp:851-868`).
- `_ESBMC_sol_clear_revert()` at every public/external entry (the EVM call boundary).
- `__ESBMC_reverted()` user-facing read, hijacked from a user stub (`solidity_convert_ref.cpp:486-489`
  `is_intrinsic_alias`).
- Feature gate `uses_revert_observation` = source references `__ESBMC_reverted`
  (`solidity_convert.cpp:213-214`); also always-on under `--bound` for the low-level-call
  `ok = !reverted` model. Scoped observation around a callee call: save→clear→call→snapshot→restore
  (`emit_call_revert_clear/return`, `solidity_convert_call.cpp:2829-2890`).

⟹ **The exact primitive `vm.expectRevert`/`testFail_*` needs already ships.** Pattern the doc gives:
`x.f(); assert(__ESBMC_reverted());` ("this call must revert"). F0 is NOT a from-scratch blocker.
Residual gap: the mark covers revert/require/custom-error but **not arithmetic-overflow reverts**
(those are ESBMC overflow ASSERTs, not flag-marking reverts) — a refinement for `expectRevert`-on-overflow.

### 0.2 Test-case emitters (F2 base)

Shared value source `collect_nondet_values(target, smt_conv)` (`witnesses.cpp:1287-1347`): post-solve
extraction of model-true assignments whose RHS is a `nondet$` symbol. `ctest_generator`
(`ctest.{h,cpp}`) is the closest analog (compilable harness + build glue); pytest maps nondet→named
param and emits `func(params)`. **Neither reconstructs a Solidity transaction** (receiver, msg.sender/
value, ctor args, target address, branch choices) — confirmed by Codex #7 (`ctest.cpp:361,605,648`;
`pytest.cpp:100,559`). No path condition / env is recorded, only nondet values (`witnesses.cpp:1293,1309`).

### 0.3 Solidity harness (F1 base)

`--contract X` (single target) → `_ESBMC_Main_X` (`solidity_convert_contract.cpp:653`) =
static-init ctor + `while(nondet)/max-tx-bounded { _sol_per_tx_reseed(); _ESBMC_Nondet_Extcall_X(); }`.
Dispatcher `_ESBMC_Nondet_Extcall_X` (`solidity_convert_constructor.cpp:235`) nondet-picks one
public/external method per tx with nondet args (`assign_param_nondet`, `solidity_convert_call.cpp:531`;
calls without caller JSON get nondet params, `:332`). Tx count bounded by `--solidity-max-tx`
(exists: `options.cpp:235`; `--solidity-max-tx 0`/`--solidity-precise` = unbounded). Native `assert()`
→ ESBMC ASSERT (`solidity_convert_ref.cpp` require/assert lowering); `--bound` builds a multi-contract
switch + `_ESBMC_nondet_new_<C>` + low-level-call dispatch + EOA balance maps
(`solidity_convert_contract.cpp:823`, `solidity_convert_call.cpp:588,2865,4381`, `solidity_address.c:170`).

### 0.4 Confirmed gaps (greenfield / real, on `solidity`)

- **Zero Foundry/cheatcode handling** (grep clean).
- **Env is fully nondet** — `initialize()` sets `msg_sender/tx_origin/block_timestamp/...` all nondet
  (`solidity_misc.c:120-150`). Foundry has *deterministic defaults* (default sender, `block.timestamp==1`,
  `block.number==1`, …). ⟹ Codex #3 confirmed: unpinned env → false mismatch unless we model defaults.
- **No fixture ordering** — `setUp()`/`test_*` are ordinary nondet-dispatched methods (no once-before-each).
- **forge-std asserts don't fail** — `assertEq/assertTrue` set forge-std's *soft* failed-flag + continue,
  never native `assert`; a false `assertEq` produces no ESBMC failure. (Codex #6.)
- **No SSA provenance/taint** — SSA steps carry no origin metadata (`symex_target_equation.h`), and
  `[approx]` is only logging, not an SSA fact. ⟹ Codex #2: a precise path-level faithfulness gate is
  not buildable today.

---

## F0 — Foundry-mode revert plumbing (builds on existing primitive; scope-corrected per Codex R2)

Not a from-scratch blocker (the primitive exists), but the existing flag is a **single global
"did the MOST RECENT public/external call revert" bit, cleared at every public entry**
(`solidity_convert_modifier.cpp:446-458`) and set on revert lowering (`:851-868`); internal/private
reverts mark+return without full bubbling (`:774-781`); low-level `.call/.staticcall/.delegatecall`
save/clear/snapshot/restore it (`solidity_convert_call.cpp:2829-2898,3024-3059`). So naïvely reading the
bit at test end is **wrong** — a later call clears it. Scope-correct design:

1. Turn the feature gate on in Foundry mode so revert = observable early-return, not `assume(false)`.
2. **`vm.expectRevert([sel|data])` = a one-shot state machine scoped to the NEXT call**: `clear` →
   run exactly the next call → `assert(_ESBMC_sol_reverted_flag)` immediately (before any subsequent
   entry can clear it). Selector/return-data payload ignored in v1 → **hard-taint** any test that
   matches on the specific error (Codex R2/benchmark: revert-data matching is the top missing boundary).
3. **`testFail_*` = a harness-level abort outcome, NOT an end-of-body flag read**: the harness wraps the
   whole test body and asserts the body reverted (aborted), decoupled from any intermediate call's flag.
   Keep this outcome **orthogonal** to the forge-std soft-assert flag (§F1.b) — a soft assert must NOT
   satisfy a `testFail_*` that Foundry expects to *revert*.
4. **Nested/low-level call monitor**: because the next public entry clears the flag, expectRevert around a
   call that itself makes further external calls needs a dedicated scoped monitor (save/observe/restore at
   the expectRevert boundary, mirroring `emit_call_revert_clear/return`), not the raw global bit.
5. Extend `mark` to arithmetic-overflow/`assert`-reverts **only if** a corpus test needs `expectRevert`
   on overflow; otherwise hard-taint. Document as a support-matrix row.

---

## F1 — Verify Foundry tests (conservative)

### F1.0 Verdict semantics + faithfulness gate (Codex #2 → coarse v1)

Oracle assumption: reference contract is correct; we check the *test*. A reachable native-`assert`
violation ⇒ the test's expectation disagrees with the reference ⇒ **WRONG**. (Surface the caveat:
ESBMC only detects a *disagreement*; attribution to test-vs-contract relies on the reference-correct
assumption.)

| ESBMC outcome | Reported |
|---|---|
| SUCCESSFUL | **CORRECT** |
| FAILED, and the whole test uses only Foundry-*supported* constructs | **WRONG** (definite) |
| FAILED, but the test touches ANY unsupported cheatcode/construct/unpinned-env read | **CORRECT** (hard-tainted) |
| UNKNOWN / timeout | **CORRECT** |

**v1 gate = fail-closed on any reachable approximation** (Codex R2 #3 sharpens this — syntactic
cheatcode scan alone is UNSOUND). Ordinary constructs inject nondet with no `vm.*` present:
`ecrecover` returns nondet `[approx]` (`solidity_convert_expr.cpp:2262-2286`), unsupported inline
assembly havocs external refs (`solidity_convert_stmt.cpp:1235-1272`). SSA steps carry no
taint/provenance field (`symex_target_equation.h:100-168`), so the gate must be built on the
**`[APPROX]`/approximation-ledger surface, propagated through the call graph, fail-closed**: if ANY
reachable operation on the test's paths is approximated (cheatcode OR ordinary), suppress WRONG for that
`--foundry-test`. **Reachability-sensitive** (benchmark #2): a pre-scan that taints-and-prunes before
executing would test "unsupported→SUCCESS", not "never-false-WRONG" — the taint must fire only when the
approximated op is actually reached, and supported assertions before/after it must still be checked.
Path-level precision (a real SSA dependency slice from the violated ASSERT) is a later upgrade requiring
provenance propagation in symex — explicitly deferred, not promised in v1.

### F1.a — Deterministic Foundry harness + default env (Codex #3, #4)

- Detect a Foundry test contract by function-name convention (`test`/`testFail`/`invariant` prefixes) —
  robust to import-resolution variance; don't depend on parsing `forge-std`.
- **Reuse existing call-conversion**, do NOT hand-assemble calls by name (Codex #4). Build the harness
  through the same constructor/static-instance path (`add_static_contract_instance`,
  `solidity_convert_contract.cpp:66,138`) and `get_non_library_function_call` with explicit caller JSON /
  AST node ids, so `this`, inherited fields, multiple-inheritance ctor order (`linearizedBaseList`,
  `solidity_convert_constructor.cpp` ~2089/2125), and overloads stay correct.
- Harness shape per test fn, **linear & deterministic** (no `while(nondet)`, no per-tx reseed):
  ```
  _foundry_init_defaults();   // NEW: pin Foundry default env (sender, timestamp=1, number=1, ...)
  constructor();              // static init as today
  setUp();                    // once, if present
  test_Foo();                 // the single selected test
  ```
- **`_foundry_init_defaults()` is mandatory** (Codex #3): a new C-model initializer that overwrites the
  nondet `initialize()` env with Foundry's concrete defaults, before any cheatcode adjustment. Without
  it, unpinned env reads taint every test and we never report WRONG.
  - **Must bypass/specialize `_sol_per_tx_reseed`** (Codex R2 #4): the reseed re-nondets env per tx
    (`solidity_misc.c:165-205`, dispatched at `solidity_convert_contract.cpp:679-692`) and would wipe the
    pinned defaults. Foundry mode uses the linear harness (no reseed) — but if any bounded loop is used
    (invariant handlers, F1.d), it must reseed to the *pinned* Foundry values, not nondet.
  - **Foundry default env constants — VERIFIED** (forge-std `src/Base.sol` `CommonBase` + Foundry
    `[evm]`/testing config, checked 2026-07; pin a forge version in the oracle as these are
    version-sensitive):

    | field | value | source |
    |---|---|---|
    | `msg.sender` inside a `test_*` fn | `0x1804c8AB1F12E6bbf3894d4083f33e07309d1f38` (`DEFAULT_SENDER` = `address(uint160(uint256(keccak256("foundry default caller"))))`) | Base.sol |
    | `address(this)` of the test contract | `0x5615dEB798BB3E4dFa0139dFa1b3D433Cc23b72f` (`DEFAULT_TEST_CONTRACT`) — this is `msg.sender` **seen by a callee** when the test calls out, absent a prank | Base.sol |
    | `tx.origin` | `0x1804…1f38` (== `DEFAULT_SENDER`) | evm config |
    | `block.number` | `1` | evm config |
    | `block.timestamp` | `1` | evm config |
    | `block.chainid` | `31337` | evm config |
    | `tx.gasprice` | `0` | evm config |
    | `block.basefee` | `0` | evm config |
    | `block.coinbase` | `0x0000…0000` | evm config |
    | `block.difficulty`/`prevrandao` | `0` | evm config |
    | `msg.value` | `0` | — |
    | default balance (test contract & sender) | `0xffffffffffffffffffffffff` (2^96−1) | `initial_balance` |
    | `VM_ADDRESS` (cheatcode handle) | `0x7109709ECfa91a80626fF3989D68f67F5b1DD12D` = `keccak256("hevm cheat code")` low-160 | Base.sol |

    **Critical nuance:** `msg.sender` *inside* a test fn is `DEFAULT_SENDER` (0x1804…), but the sender a
    *called* contract observes is `address(this)` = `DEFAULT_TEST_CONTRACT` (0x5615…) unless `vm.prank`.
    The benchmark's default-env anchor (§T20 #08) must pin the right one per read site. Sources recorded
    in the benchmark `ORACLE.md`.
- Selection: iterate all `test*` fns, or `--foundry-test <name>`. New flag `--foundry` (or auto-detect).

### F1.b — forge-std assertion lowering (Codex #6, distinct from revert)

- Model a **separate** `_ESBMC_foundry_failed` soft-flag (NOT the revert flag — different concept: EVM
  revert vs forge-std assertion soft-fail). Lower `assertEq/assertTrue/assertFalse/assertGt/.../
  assertApproxEqAbs/assertApproxEqRel` to `if (!cond) _ESBMC_foundry_failed = true;` (continue, matching
  forge-std), and emit a single final harness assertion `assert(!_ESBMC_foundry_failed)` for normal tests.
  `assertTrue(vm.failed())` then reads the soft-flag correctly instead of the v1-wrong `assert(false)`.
- **Keep the two outcomes orthogonal** (Codex R2 #5): `testFail_*` asserts the *revert/abort* outcome
  (§F0.3), NOT `_ESBMC_foundry_failed || __ESBMC_reverted()` — a soft-assert must not satisfy a test
  Foundry expects to revert, and the revert bit can be cleared by later entries. A normal test's verdict
  = `!_ESBMC_foundry_failed` AND did-not-abort; a `testFail_*`'s = did-abort. Never OR them.

### F1.c — Minimal cheatcode model (Codex #5, #8)

Recognize `vm.<name>(...)` as intrinsics, not external calls. Support matrix (anything not listed →
**hard-taint** the test, per F1.0):

| Cheatcode | Lowering | Note |
|---|---|---|
| `vm.assume(c)` / `bound(x,lo,hi)` | `__ESBMC_assume(...)` / return clamped x | must for PUT |
| `vm.prank/startPrank/stopPrank` | set `msg_sender` for next / range | reuse env vars |
| `vm.deal(a,v)` | **set-balance helper (NEW), not `_ESBMC_eoa_credit`** | Codex #5: credit adds to nondet init, ≠ set |
| `vm.warp(t)`/`vm.roll(n)` | set `block_timestamp`/`block_number` | |
| `vm.expectRevert()` | F0 clear+assert-flag | selector-specific form → hard-taint v1 |
| `mockCall`/`etch`/`store`/`load`/`snapshot`+`revertTo` | **unmodeled → hard-taint** | Codex #8 |

`--bound` coexistence (Codex #5): decide explicitly whether Foundry-mode nested `c.f()` calls use the
`--bound` low-level dispatch (reentrancy/EOA model) or a deterministic direct call. Default: deterministic
direct calls for the test's own calls; keep `--bound` semantics only if the test itself models an
adversarial counterparty. Document.

### F1.d — PUT / parameterized (`testFuzz_*`, `invariant_*`)

Test-fn args → nondet (existing `assign_param_nondet`), now correctly pruned by `vm.assume`/`bound`.
Where ESBMC beats forge (∀ inputs vs sampling). `invariant_*` = ctor→setUp→bounded deterministic-context
handler sequence→`assert(invariant)`; reuse a `--solidity-max-tx`-bounded loop but with cheatcode-frozen
(not reseeded) context.

---

## F2 — Emit Foundry tests (after F1 defines the target shape)

Codex #7 confirmed F2 is **not** self-contained: current emitters know nondet values only, not
transaction structure/env/ctor state/revert expectations — exactly what F1.a defines.

- **F2.a (fixed value)**: new `foundry_generator` (`src/goto-symex/foundry.{h,cpp}`) on the
  `ctest_generator` shape (`clear/collect/generate` + `generate_single`), reusing `collect_nondet_values`.
  Main new work = Solidity `type2tc`→literal formatting (`uint256/int256/address/bool/bytesN/bytes/string`).
  Emit against **F1's harness/env metadata**: a `*.t.sol` with pinned env + `c = new C(ctorargs)` +
  `c.f(concrete args)` + the failing assertion. **F2 requires an explicit transaction record from F1**
  (Codex R2 #6): receiver, selector, args, msg.sender, msg.value, pinned env, and revert-outcome. The
  witness collector only captures nondet *values* (`witnesses.cpp:1286-1347`), not this shape — so F2
  MUST consume an F1-produced metadata struct. **When that record is incomplete (violation reached only
  through the nondet dispatcher / ambient EVM state), emit an explicit `// UNSUPPORTED: <reason>` stub**,
  never a wrong or uncompilable test. Wire option (`options.cpp:376`), `bmct` member (`bmc.h:50`),
  3 dispatch sites (`bmc.cpp` ~:172, ~:2163, ~:1149/:1551).
- **Acceptance = round-trip**: emitted `*.t.sol` compiles and `forge test` reproduces the same failure.
  This is also F1's validator ⇒ synergy, but only after F1's shape exists.
- **F2.b (parameterized)**: later; emit `testFuzz_*`/case tables from path conditions; ties to F1.d.

---

## Prioritization (revised — F1-first, per Codex + revert primitive existing)

**v1 said F2-first; overturned.** F2's target shape is defined by F1, and F0's blocker shrank because
the revert primitive already exists — so F1-first is now clearly right.

1. **F0** — Foundry revert plumbing on the existing `__ESBMC_reverted` flag (small).
2. **F1.a** — deterministic harness + `_foundry_init_defaults()`, reusing call-conversion (the keystone).
3. **F1.b** — forge-std soft-assert lowering (`_ESBMC_foundry_failed`).
4. **F1.c** — minimal cheatcode set + support matrix + whole-test hard-taint gate (F1.0).
5. **F2.a** — fixed-value emitter on F1's metadata; UNSUPPORTED-stub fallback.
6. **F1.d (PUT) → F2.b (parameterized emit)**.

**Cross-cutting foundations (build once):** Foundry default-env C model (`_foundry_init_defaults`,
set-balance helper); `Vm`/cheatcode intrinsic recognition table; support matrix + hard-taint gate;
Solidity `type2tc`↔literal formatting (F2). Build on the **`solidity` branch**.

---

## Residual risks (post-correction, incl. Codex R2)

0. **Revert-semantics mapping (biggest, per R2)** — Foundry treats revert as *whole-transaction abort*;
   ESBMC's primitive is a *last-call global bit* cleared at each public entry. `expectRevert`/`testFail`
   MUST be built as scoped one-shot / harness-abort constructs (§F0.2-4), never a naïve end-of-body read,
   or common revert-idiom tests are silently misclassified. Revert-**data** matching is unsupported v1 →
   hard-taint. This is the single item most likely to sink correctness; prototype it first (F0).
1. **Coarse gate over-suppression** — whole-test hard-taint means a single unsupported cheatcode in an
   otherwise-clean test yields CORRECT (never WRONG). Acceptable for conservativeness, but limits F1's
   *usefulness* until the cheatcode matrix is broad. Mitigation: prioritize the top ~8 cheatcodes real
   1inch-style tests use; measure hard-taint rate on a real corpus early.
2. **overflow/`assert`-revert not flag-marked** — `expectRevert` on an arithmetic overflow won't observe
   a revert (it's an ESBMC ASSERT). Either extend `mark` to overflow sites or hard-taint. Decide via corpus.
3. **`--bound` × deterministic-test coexistence** — the reentrancy/EOA/low-level-call machinery is
   always-on under `--bound`; a Foundry test that itself issues low-level calls will re-enter the nondet
   dispatcher. Must define precisely which calls stay deterministic. (Codex #5.)
4. **Real forge-std parsing** — v1 recognizes `assert*`/`vm.*` by callee name without parsing forge-std;
   overloaded/aliased assertions or helper wrappers (`assertEq` via a project helper) may slip through →
   silent no-failure. Mitigation: hard-taint on unrecognized `Test`-inherited calls too.
5. **Corpus-driven validation** — every semantic choice above (env defaults exact values, which reverts
   mark, gate coarseness) should be pinned against a small real Foundry-test corpus before committing
   the model. Build the corpus first.
