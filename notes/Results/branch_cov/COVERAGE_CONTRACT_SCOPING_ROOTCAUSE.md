# Root cause: `--contract C` does NOT scope branch-coverage instrumentation

Status: **diagnosis only** (no fix applied; fix surface + denominator
decision surfaced for the user per strict-stage-authorization).
Every `file:line` below was read in this session. Falsifier evidence
captured this session (not recalled).

---

## 0. The reported symptom

`cov_pilot_cross_chain_swap_EscrowDst/test.desc` run with
`--contract EscrowDst --branch-coverage-claims` reports
`Branches : 90`, `Reached : 37`, `Branch Coverage: 41.11%`.

`contract EscrowDst` (contract.sol:1573 → EOF, ~75 lines) cannot
contain 90 branches. The file is a *flattened* unit: 8 libraries
(AddressLib 22, Errors 77, ProxyHashLib 410, RevertReasonForwarder
435, TimelocksLib 496, Create2 558, ImmutablesLib 760, SafeERC20 942),
2 abstract bases (BaseEscrow 1444, Escrow 1550), 1 contract
(EscrowDst 1573).

### Falsifier check (captured this session)

`esbmc contract.solast --sol contract.sol --contract EscrowDst
--branch-coverage-claims --show-claims --no-assertions` → 90 claims.
Bucketed by source line range:

| Owner | line range | claims |
|---|---|---|
| libraries | < 1444 | **48** |
| abstract bases BaseEscrow+Escrow | 1444–1572 | 38 |
| `contract EscrowDst` itself | ≥ 1573 | **4** |
| **total** | | **90** |

48/90 are pure library internals (`SafeERC20.forceApprove`,
`safeTransfer`, `safePermit`, `safeIncreaseAllowance`,
`Create2.deploy`, `ImmutablesLib._validateImmutables`, …). Only 4 are
in `contract EscrowDst`. The user's expectation ("`--contract C` ⇒
count only C's branches") is objectively violated.

---

## 1. The full pipeline, every link source-read this session

### Link 1 — frontend converts EVERY contract/library body, unfiltered

`src/solidity-frontend/solidity_convert.cpp:368-386` — second-round
loop calls `get_contract_definition(_name)` for **every**
`ContractDefinition` node, with **no `tgt_cnt_set` guard**:

```cpp
for (... nodes ...) {
  if (node_type == "ContractDefinition") {
    std::string _name = (*itr)["name"];
    if (get_contract_definition(_name))   // called for ALL, no filter
      return true;
  }
}
```

`tgt_cnt_set` (= `{EscrowDst}`) is built at
`solidity_convert.cpp:920-927` and consumed **only** at
`solidity_convert.cpp:430-449` to pick the *harness entry*
(`multi_transaction_verification("EscrowDst")` at
`solidity_convert_contract.cpp:653`). It scopes **which contract's
public functions the dispatcher calls** — never which function bodies
are emitted as goto functions. Libraries are registered/converted
unconditionally (first round; `populate_auxiliary_vars`
`solidity_convert.cpp:886-895` only routes libraries into
`nonContractNamesList`, it does not suppress their body emission).

⇒ After goto conversion, `Create2.deploy`, `SafeERC20.safeTransfer`,
`ImmutablesLib.*`, both abstract bases, **all** have
`body_available == true`.

### Link 2 — the coverage instrumentation loop has no `--contract` gate

`src/goto-programs/goto_coverage.cpp:246-289`
(`goto_coveraget::branch_coverage`):

```cpp
Forall_goto_functions (f_it, goto_functions)
  if (f_it->second.body_available && f_it->first != "__ESBMC_main") {
    if (filter(f_it->first, goto_program)) continue;
    Forall_goto_program_instructions (it, goto_program) {
      ... if (location_pool.count(cur_filename) == 0) continue; ...
      else if (it->is_goto() && !is_true(it->guard)) {
        insert_assert(goto_program, it, it->guard);
        insert_assert(goto_program, it, gen_not_expr(it->guard));
      }
    }
  }
```

It instruments **every** body-available function that `filter()`
doesn't reject.

### Link 3 — `filter()` has no contract notion

`src/goto-programs/goto_coverage.cpp:1201-1217`:

```cpp
bool goto_coveraget::filter(func_name, goto_program) const {
  if (target_function != "" && !is_target_func(func_name, target_function))
    return true;                       // --function only
  if (goto_program.hide &&
      (lid == SOLIDITY || lid == PYTHON))
    return true;                       // __ESBMC_HIDE only
  return false;
}
```

- `target_function` is set **only** from `--function`
  (`esbmc_parseoptions.cpp:3562-3563`:
  `if (cmdline.isset("function")) tmp.set_target(...)`). With
  `--contract` it stays `""`. First clause inert.
- `goto_program.hide` is set (`goto_convert_functions.cpp:39-51,
  182-183`) **iff** the body carries an `__ESBMC_HIDE` label. The
  Solidity frontend stamps `__ESBMC_HIDE` only on *synthetic
  auxiliaries* (`_ESBMC_Main`/`sol_main`
  `solidity_convert_contract.cpp:666-670`, ctor helpers, builtins,
  mapping helpers — all the `set_label("__ESBMC_HIDE")` sites). It is
  **never** stamped on user library/contract functions like
  `Create2.deploy`. Second clause inert for libraries.

⇒ `filter()` returns false for every library function ⇒ instrumented.

### Link 4 — the only "skip a library" guard is defeated by flattening

`src/goto-programs/goto_coverage.cpp:255-260`:

```cpp
std::string cur_filename = get_filename_from_path(it->location.file());
if (location_pool.count(cur_filename) == 0)   // "probably a library"
  continue;
```

`location_pool` = `{ basename(cmdline.args[0]) } ∪ include_files`
(`goto_coverage.cpp:239-243`). This is the *only* mechanism intended
to exclude library code — but it keys on **source filename**. The
pilot inputs are **flattened single-file** `.sol`: every library,
base, and the contract all carry `location.file() == "contract.sol"`,
which equals `basename(args[0])`. So the guard passes for all of them.
Flattening is precisely what neutralises the one library exclusion.

### Link 5 — `Branches : N` counts the same unfiltered set

`get_total_instrument()` `goto_coverage.cpp:623-645` iterates the same
`forall_goto_functions` + same `filter()` and counts every
`"instrumented assertion"` user-provided assert ⇒ `total_branch = 90`.
Reported at `src/esbmc/bmc.cpp:892,903`
(`total = goto_coveraget::total_branch; log_result("Branches : {}",
total)`).

### Link 6 — `Reached`/`%` semantics (so the deflation is explained)

- `bmc.cpp:895`: `tracked_instance = reached_claims.size()`.
- A claim enters `reached_claims` only when its instrumented assert is
  found **violable** — i.e. the solver returns `P_SATISFIABLE`
  (`bmc.cpp:2000-2012` failed/unknown bookkeeping;
  `bmc.cpp:2172-2185` `reached_claims.emplace(claim_sig)` after a
  witness is enumerated). Semantically: "this branch edge is
  *reachable* on some path from the harness entry."
- `bmc.cpp:914`: `Branch Coverage = reached_claims.size()*100.0 /
  total_branch`.

So the denominator (`total_branch`) is the whole flattened unit, while
the numerator (`reached_claims`) can only ever cover branches the
**EscrowDst-only dispatcher** (`_ESBMC_Main_EscrowDst`) can actually
drive. Library branches reached *through* EscrowDst's calls
(`SafeERC20.safeTransfer` via `_uniTransfer`) count; library branches
EscrowDst never calls (`Create2.deploy`, dead `safePermit` paths) are
**permanently unreachable** ⇒ inflate the denominator, never the
numerator ⇒ structurally deflated %. The `Reached : 0` family
(`farming`/`st1inch`) is the same mechanism at extreme scale: a huge
instrumented set the single-contract dispatcher cannot drive within
the k-induction budget (cf. k-induction budget-burn).

### Link 7 — k-induction × coverage

`esbmc_parseoptions.cpp:3545-3565` forces `base-case`,
`multi-property`, `keep-verified-claims=false`, `no-pointer-check`.
Coverage asserts survive `--no-assertions`
(`symex_main.cpp:530-541`: `"instrumented assertion"` is exempted).
No contract-scoping anywhere on this path either. k-induction only
governs *how deep* symex drives the harness; it never changes the
instrumented denominator.

---

## 2. Conclusion (FINDING — mechanism read this session)

`--contract C` is an **entry-point selector only**. Branch-coverage
instrumentation and the `Branches:` denominator are computed over the
**entire flattened compilation unit**, gated solely by (i) `--function`
(unused here) and (ii) `__ESBMC_HIDE` (never on user library code),
with the lone filename-based library guard nullified by flattening.
Hence a ~4-branch contract reports 90, and the coverage % is
structurally meaningless for single-contract scope. This is a real
bug, not a documented over-approximation — nothing in the read path
intends unit-wide instrumentation under `--contract`.

---

## 2b. Sharper defect: branch attribution has TWO disagreeing keys
###     (modifier-splice case — source-read this session)

The bug is deeper than "instrument the whole unit". A coverage claim
has two independent identity keys, and for Solidity they **disagree**:

- **Function-owner key**: the synthetic modifier-wrapped function
  `<func>_<modifier>` is symbol-keyed under the contract that *uses*
  the modifier — `get_modifier_function_name`
  (`solidity_convert_modifier.cpp:861-862`:
  `id = "sol:@C@" + cname + "@F@" + func + "_" + mod + "#0"`, `cname`
  = using contract). Correct: it belongs to B.
- **Instruction-location key**: the modifier body (incl. its branch
  `if`) is converted from the *modifier definition* AST via
  `get_block(mod_def["body"], …)`
  (`solidity_convert_modifier.cpp:1190-1192`); `mod_def` is the
  `ModifierDefinition` resolved by `find_decl_ref`/base-walk
  (`:964-1010`). Each spliced statement keeps the modifier's own
  source location → the instrumented assert's `it->location.file()/
  line` points into **contract A's** source region. Only the aux
  *symbol* location is the using-function node (`:1033-1034`); the
  inner branch is not re-stamped.

### Consequences (correcting §1's naive line-range bucketing)

1. **Location-based scoping is unsound for Solidity, full stop.** The
   existing per-instruction `location_pool` filter
   (`goto_coverage.cpp:255-260`) would, even with *non-flattened*
   multi-file input, exclude a modifier defined in a separate file but
   used by the target contract — **under-counting the target's real
   branches**. Flattening then additionally collapses everything in.
   Two independent unsoundnesses in the one location heuristic.
2. **§0's "38 abstract-base claims" are mostly the target's own
   behavior.** `withdraw_onlyCaller`, `cancel_onlyAfter`,
   `publicWithdraw_onlyBefore`, … are `BaseEscrow`-defined modifiers
   (`onlyCaller`/`onlyBefore`/`onlyAfter`, lines 1444+) **spliced into
   EscrowDst's** `withdraw`/`cancel`/`publicWithdraw`. They execute as
   EscrowDst transactions and are reachable from
   `_ESBMC_Main_EscrowDst`, but their `location` is in the base's line
   range. So the §0 "48 lib / 38 base / 4 own" split *itself
   demonstrates the mis-attribution*: line-range bucketing falsely
   labels genuine EscrowDst branches as out-of-scope.
3. **The "unused modifier inflates the denominator" half does NOT
   occur.** `ModifierDefinition` is a no-op in `get_function_decl`
   (`solidity_convert_decl.cpp` `ModifierDef:` → `break;`); a modifier
   is *never* emitted as a standalone goto function. It materialises
   only at use sites, under the using contract. An unused modifier in
   A contributes zero instrumented branches. The 90's inflation is
   purely unreachable library **functions** (§1) — that finding
   stands; the modifier path is mis-attribution, not inflation.

### This collapses the denominator "decision"

Semantics **(a) own-contract-by-source-range is REJECTED outright** —
not "under-counts libraries" but *actively mis-attributes inherited /
spliced-modifier branches that ARE the target's behavior*. (c) is the
bug. **(b) call/splice-graph reachability from the harness entry,
keyed on the goto FUNCTION (owner is correct), NEVER on
`it->location`, is the only sound option.** There is no real product
choice on semantics; the only residual product question is whether
inherited-but-*unreachable* base branches count — and reachability
answers that uniformly (unreachable ⇒ excluded).

### This sharpens Fix-B

Fix-B must compute the reachable-**function** set from
`_ESBMC_Main_<C>` / `sol_main` and gate `filter()` +
`get_total_instrument()` on **function membership in that set only**.
It must NOT consult `it->location`. The existing `location_pool`
filename heuristic (`goto_coverage.cpp:239-243,255-260`) is unsound
(consequence 1) and should be **removed/replaced** by the reachability
gate, not kept alongside it.

## 3. Fix surface + the decision the user must make first

**Updated by §2b: (a) is now proven UNSOUND, not a valid option.**
The only real choice left is "implement Fix-B (semantics b)" vs "leave
as KNOWNBUG". The (a)/(b)/(c) menu below is kept for the record but
(a) is struck.

The denominator semantics is a **product decision** (surfacing before
coding, per no-silent-substitution):

- **(a) own-contract-only**: count branches syntactically inside
  `contract C` (+ its linearized abstract bases). EscrowDst → ~4 (+38
  base = 42). *Under-counts* library branches EscrowDst genuinely
  executes (`SafeERC20.safeTransfer` is real EscrowDst behavior).
- **(b) reachable-from-C's-harness**: count branches in any function
  transitively reachable from `_ESBMC_Main_C` / `sol_main`. EscrowDst
  → its 4 + 38 base + only the library branches it actually calls.
  This is the verification-meaningful denominator ("fraction of
  branches C's transactions can exercise").
- **(c) whole unit** = current buggy behavior.

Recommended target: **(b)**. (a) is easy but semantically wrong for
library-heavy contracts; (c) is the bug.

### Fix options (NOT implemented — separate authorization)

- **Fix-B (correct surface, coverage pass):** in
  `goto_coveraget::branch_coverage()` precompute the set of functions
  reachable from the harness entry by a transitive walk over
  `FUNCTION_CALL` instructions starting at `_ESBMC_Main_<C>` /
  `sol_main`, and have `filter()` skip any function not in that set
  (mirror the same set in `get_total_instrument()` so numerator and
  denominator agree). No ready-made inter-procedural reachable-set
  helper exists (`remove_unreachable.h` is intra-procedural only), but
  the call graph is a trivial BFS over `goto_functions`. Pure
  middleware change; language-agnostic; gives semantics (b).
- **Fix-A (frontend, name-based):** when `tgt_cnt_set.size()==1`,
  stamp `__ESBMC_HIDE` on functions of contracts/libraries not in
  `tgt_cnt_set ∪ linearizedBaseContracts(C)`. Reuses the existing
  Solidity-hide path in `filter()`. Gives semantics (a) — and would
  wrongly mask library branches EscrowDst executes. Rejected unless
  the user explicitly wants (a).

Fix-B is the recommended single fix surface. It must NOT be a frontend
workaround (no-lazy-fix): the defect is in the coverage pass's scoping
contract, so it is fixed there.

---

## 4. Corrective action for commit `efe168f8b8` (separate authorization)

That commit flipped `cov_pilot_cross_chain_swap_EscrowDst/test.desc`
to **CORE** pinning `^Branches : 90$` / `^Reached : 37$` /
`^Branch Coverage: 41.111…%$`. Per §2 those numbers are the *bug's
output*; a CORE pin encodes the bug as correct
(knownbug-tests rule). Required walk-back (await user choice):

1. **Revert just the EscrowDst test.desc + notes** to KNOWNBUG,
   re-pinned on the true symptom (e.g. a comment documenting "90
   branches = unit-wide instrumentation bug, see this doc"), keeping
   the legitimate `convert_type_expr` crash-fix in the same commit; OR
2. `git revert efe168f8b8` wholesale (also loses the crash-fix —
   undesirable); OR
3. amend the commit to drop only the EscrowDst CORE flip.

Option 1 is recommended (smallest correct change, preserves the sound
crash-fix). The crash-fix itself (`struct_type_has_component` gate in
`solidity_convert_type.cpp`) is unaffected by this finding.

---

## 5. Residual / not yet verified

- Exact call-graph of `_ESBMC_Main_EscrowDst` (which of the 48 library
  claims are reachable vs dead) — needs a goto call-graph dump; not
  required to establish the bug, required to validate Fix-B's
  numerator==denominator parity before landing.
- `farming`/`st1inch` `Reached : 0`: same instrumentation-scope
  mechanism *plus* k-induction budget exhaustion at scale — to be
  confirmed with a per-claim trace once Fix-B lands (separate stage).
- Whether any non-Solidity language relies on current unit-wide
  `--contract`-less behavior (C/C++ have no `--contract`; `--function`
  path already scopes via `target_function` — Fix-B keyed on harness
  entry is a no-op when no single entry is selected, preserving
  existing non-Solidity behavior; to be re-verified at implementation
  time).
