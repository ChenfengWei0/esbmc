# Stage 3 — diagnosis of the "Reached: 0" cluster (post-2C)

> **CORRECTION 2026-05-15 (same day, later).** The "EscrowDst → CORE"
> conclusion in this document (the `Branches : 90 / Reached : 37 /
> 41.11%` flip) was **WRONG and has been walked back**. The
> library-receiver crash-fix below is sound and is kept; it only
> *unblocked* EscrowDst from aborting. It did NOT make EscrowDst's
> coverage correct: `Branches : 90` for a contract with ~4 own
> branches is the **`--contract` scoping bug** — `--show-claims`
> (captured 2026-05-15) shows 48 library + 38 base-modifier-spliced +
> 4 own. EscrowDst's `test.desc` is reverted to **KNOWNBUG**
> (`^Branches : 90$` + `^Branch Coverage: [1-9]$`). Pinning the
> bug's output as CORE encoded a bug as correct behaviour. Full
> root-cause + fix design: **`COVERAGE_CONTRACT_SCOPING_ROOTCAUSE.md`**.
> Read the EscrowDst rows below through this correction.

Generated 2026-05-15. **Diagnosis only — no fix** (fix is a separate,
separately-authorised stage, per `feedback_strict_stage_authorization`).
Cluster: `cov_pilot_cross_chain_swap_EscrowDst`,
`cov_pilot_farming_FarmingPool`, `cov_pilot_st1inch_St1inch`.

## The plan premise is false — surfaced before proceeding

The Stage-3 plan (and the Stage-0 pin) attributes these three to
`SUCCESSFUL with Branches: N, Reached: 0, Coverage: 0%`. **That is not
the current — nor, by bisection, a recent — behaviour.** Mirroring each
`test.desc` verbatim (coverage mode → `--k-induction`, never `--unwind`;
`contract.solast` positional + `--sol contract.sol`) all three **abort
during GOTO generation** (SIGABRT, exit 134), before symex/solver:

| Pilot | Current symptom (HEAD `9e29c02d4c`) |
|---|---|
| `EscrowDst` | `ERROR: Looking up index of nonexistant member "$balance" in struct/union "Create2"` |
| `FarmingPool` | `ERROR: Looking up index of nonexistant member "$address" in struct/union "SafeERC20"` |
| `St1inch` | `ERROR: Looking up index of nonexistant member "$address" in struct/union "SafeERC20"` |

`Branches`/`Reached`/`Coverage` lines are **never emitted** (the run
dies before coverage). The KNOWNBUG `test.desc` regex
(`^Branches : N$` / `^Reached : 0$` / `^Branch Coverage: 0%$`) requires
all three lines, so it never matches → the KNOWNBUG ctest stays green
**not because the pinned symptom is reproduced, but because the run
crashes before any of those lines exist**. The recorded `Reached: 0`
symptom is stale; the true blocker was masked
(`feedback_coverage_failure_is_signal`: the crash IS the finding).

## Bisection — NOT a Stage-2C / nested-mapping-fix regression

Minimal one-TU bisect: reverted only
`src/solidity-frontend/solidity_convert_expr.cpp` to the fix's parent
commit `02bd18f031` (pre-`9e29c02d4c`), rebuilt `esbmc`, re-ran
`EscrowDst`:

```
exit=134
ERROR: Looking up index of nonexistant member "$balance" in struct/union "Create2"
```

Identical abort. → the deep-nested-mapping fix `9e29c02d4c` is
**exonerated**. Architecturally consistent: the fix is gated by
`array.type().is_array()` (nested-mapping IndexAccess only) and these
crashes are library member-access, not mapping index. Stage 2C
(`3d6d424b73`) is `src/solvers/smt/` only (post-GOTO) and cannot affect
GOTO generation. The uncommitted WIP (`solidity_blockchain.c` /
`solidity_misc.c` `__uint32_t`→`uint32_t` rename;
`solidity_language.cpp` `<cstdio>`+Windows `popen` shim) does not touch
`$balance`/`$address` synthesis — also exonerated. **The crash is
pre-existing**; the Stage-0 `Reached: 0` capture (2026-05-14, via
`run_pilot.sh`) reflected a different binary/flag environment than the
committed `test.desc`, and the pin has been silently green ever since.
File `solidity_convert_expr.cpp` was restored to HEAD and rebuilt; tree
is HEAD-clean for that file.

## Backtrace (root cause, source-grounded)

`EscrowDst` under gdb (representative; if-guard variant):

```
irep2_type.cpp:295   struct_union_data::get_component_number   <- abort
irep2_expr.h:2994    member2t::member2t
migrate.cpp:1517     member2tc  (irep member_exprt -> irep2 member2t)
migrate.cpp:924/973  migrate_expr (operand pair)
goto_convert.cpp:1734 goto_convertt::generate_conditional_branch  <- if-GUARD
goto_convert.cpp:1624 goto_convertt::generate_ifthenelse
goto_convert.cpp:1703 goto_convertt::convert_ifthenelse
goto_convert_functions.cpp:135 convert_function
```

`FarmingPool` (plain-expression variant): same
`get_component_number` (`irep2_type.cpp:295`) via `migrate.cpp:1517`,
but the parent migrate frame is `migrate.cpp:2032` (not the
`:924/:973` if-guard pair) — same root-cause class, different emission
position (`$address in SafeERC20`).

**Root cause.** The Solidity frontend's `transfer`/`send`/balance
lowering (`src/solidity-frontend/solidity_convert_call.cpp`, the
`get_transfer_definition` / `get_send_definition` family, e.g. the
`if (this_balance < val) __ESBMC_assume(false)` guard at ~3000-3015 and
`exprt target_balance = member_exprt(base, "$balance", val_t)` at
:3025) builds `member_exprt(<receiver>, "$balance" | "$address", …)`
where `<receiver>` (`this_expr` / `static_ins` / `base`) resolves to a
**library** struct type. `$balance`/`$address` are synthetic members of
**contract-instance** structs only (see `solidity_convert_builtin.cpp`
:115/:177 attach sites); libraries (`Create2`, `SafeERC20`) carry no
such component. For these real-world 1inch flats —
`using SafeERC20 for IERC20` token transfers (FarmingPool / St1inch)
and `Create2`-based escrow ETH dispatch (EscrowDst) — the
receiver-resolution does not exclude library types. The mis-typed irep
`member_exprt` survives the frontend (no frontend-side component check)
and aborts only at `migrate.cpp:1517` → `irep2_type.cpp:295` during
`goto_convert`, when irep→irep2 migration looks up the non-existent
component.

This is a **pre-existing Solidity-frontend bug**, independent of Stage
2C and the deep-nested-mapping fix, and is the real Stage-3 blocker for
all three (one root-cause class, two emission variants: if-guard vs.
plain expression).

## Fix sketch (NOT implemented — separate authorised stage)

In the `transfer`/`send`/balance receiver-resolution
(`solidity_convert_call.cpp`, the `$balance`/`$address` `member_exprt`
build sites), gate the synthetic-member access on the receiver's
resolved type actually being a contract-instance struct that *has* the
`$balance`/`$address` component (mirror the existing
`to_struct_type(...).has_component("$address")` guard already used at
`solidity_convert_expr.cpp:1126`), and route library-typed receivers
through the library-call path instead of the EOA/transfer model.
Risk surface: must not regress the contract-instance transfer/send
paths (the `has_component` guard is necessary, not sufficient — also
verify the library detour preserves `safeTransfer`/`Create2` value
semantics). Flip targets for that stage = these 3 KNOWNBUG pins +
a new minimal `using SafeERC20 for IERC20` pass/fail pair and a
`Create2`-dispatch pair; full Solidity gauntlet; the verdict must
become a real coverage number (not just "no longer crashes").

## Re-pin — documentation-only, KNOWNBUG retained, regex unchanged

Mirrors the `STAGE2C_FOLLOWUP_REPIN` precedent. The `test.desc`
desired-output regex (`^Branches : N$` …) is the **correct flip
sentinel** — a genuine future fix that emits a real coverage number
will match it and auto-flip KNOWNBUG→CORE. Pinning the crash string
instead would *re-mask* the eventual fix. So: **no `test.desc` regex
change**; only the symptom *documentation* is corrected (this note +
`KNOWNBUGS.md` + a short header comment on each `contract.sol`). The
pins remain KNOWNBUG (no spurious flip; baseline stable — confirmed by
a single `ctest -R cov_pilot_` run).

## Fix LANDED 2026-05-15 (separately authorised: user "修复")

**Root cause refined during fix.** The poison originates one level
upstream of the `solidity_convert_call.cpp` transfer/balance sites:
`address(this)` / `address(instance)` is lowered to
`member_exprt(src_expr, "$address", t)` in
`solidity_convert_type.cpp:1751` (`convert_type_expr`, the
`is_address_type(dest) && src is CONTRACT/UNSET` branch). Inside a
`library` function body `src_expr` is the library struct (`Create2` /
`SafeERC20`), which has no `$address`. That mis-typed member then feeds
the `.balance` check (EscrowDst `library Create2`:
`if (address(this).balance < amount)`) or stands alone (FarmingPool /
St1inch `library SafeERC20`), and aborts at `migrate.cpp` →
`irep2_type.cpp:295` in `goto_convert`.

**Patch (one site + one helper, minimal).**
- New `solidity_convertert::struct_type_has_component(type, comp)`
  (`solidity_convert_builtin.cpp`): resolves a type through
  pointer/symbol-type indirection to its struct and tests
  `has_component` (mirrors the precedent at
  `solidity_convert_expr.cpp:1126`).
- `solidity_convert_type.cpp` address-cast branch: build the
  `$address` member **only** when `struct_type_has_component(
  src_expr.type(), "$address")`; otherwise (library `this`, no
  component) substitute `_ESBMC_enclosing_contract_address` — a library
  runs via DELEGATECALL in the caller's context, so `address(this)` is
  the enclosing contract's address. This is the **same model** the
  library branch in `solidity_convert_call.cpp:~3336` already uses
  (`is_library ? _ESBMC_enclosing_contract_address : this.$address`).
- `get_builtin_property_expr` Site 2 guard **not** added: per
  `feedback_minimise_flag_discipline` / Incremental Patch Testing,
  Site 1 alone fixes the diagnosed root cause (verified below), and
  with `address(this)` no longer producing `this.$address` the Site 2
  first-branch never receives a library member. Defense-only; omitted
  to avoid redundant code.

**Soundness & completeness (both axes, per
`feedback_completeness_soundness_report`).** Completeness: strictly
improved — previously aborted (zero coverage on any contract whose
reachable code calls a library using `address(this)`); now completes.
Soundness: `address(this)` in a library = the ambient
`_ESBMC_enclosing_contract_address` (the currently-executing contract's
address, written at every contract-method entry) — exact when the
library is invoked from a tracked contract method, sound
over-approximation otherwise; identical to the pre-existing library
model in `solidity_convert_call.cpp`. No new unsoundness; the abort it
replaces performed no analysis at all.

**Verification (empirical, mirroring each `test.desc`).**

| Pilot | Before (HEAD `9e29c02d4c`) | After fix |
|---|---|---|
| `EscrowDst` | SIGABRT `$balance in Create2` | exit 0, `Branches : 90 / Reached : 37 / Branch Coverage: 41.111111111111114%` — **deterministic** (2 re-runs identical) |
| `FarmingPool` | SIGABRT `$address in SafeERC20` | library crash GONE; **new independent** SIGABRT `nonexistant member "getTotalSupply" in "struct BytesStatic"` (abi/bytes path — distinct root cause) |
| `St1inch` | SIGABRT `$address in SafeERC20` | library crash GONE; runs to completion (`VERIFICATION SUCCESSFUL`), k-induction coverage non-convergent within `--timeout` (`Reached : 0` via budget-burn — documented orthogonal class) |

- **EscrowDst → CORE. ❌ WALKED BACK 2026-05-15 (see top banner).**
  This flip was wrong: `Branches : 90` is the `--contract` scoping
  bug, not real coverage (48 lib + 38 base-modifier-spliced + 4 own,
  per `--show-claims`). Reverted to **KNOWNBUG** pinned
  `^Branches : 90$` + `^Branch Coverage: [1-9]$` (the latter fails on
  today's `41.111…%` → stable KNOWNBUG, mirrors the
  FarmingPool/St1inch sentinel). The crash-fix that unblocked the run
  is unaffected and kept. Root cause + fix:
  `COVERAGE_CONTRACT_SCOPING_ROOTCAUSE.md`.
- **FarmingPool / St1inch → KNOWNBUG re-pinned.** The original regex
  pinned the *buggy* `^Branches : N$ / ^Reached : 0$ /
  ^Branch Coverage: 0%$` output. KNOWNBUG semantics
  (`regression/testing_tool.py:282`): **all** regexes matching → ctest
  FAIL (exit 77, "reclassify as CORE"). With the early crash removed,
  both contracts now *emit* those exact 0-coverage lines before the new
  crash / before the timeout → all three matched → **spurious exit-77**.
  Corrected by re-pinning both to the desired-output sentinel
  `^Branch Coverage: [1-9]` (matches any non-zero coverage, never the
  current `0%`) — KNOWNBUG stays PASS now and a genuine future fix
  (non-zero coverage) flips it. This corrects the Stage-0
  pinned-buggy-output anti-pattern, exposed as a real failure by the
  fix.
- **Regression.** Focused gauntlet `ctest -R
  'address|balance|transfer|library|eoa|cov_pilot|aqua|nested_mapping|
  mapping_struct_smtsort|nested_inf_array_of_struct'` → **exit 0, all
  130 PASS** (ctest returns non-zero on any failure; exit 0 is the
  authoritative all-pass). cppcheck clean for the changed frontend
  files (no `unreadVariable`/`unusedVariable`/`variableScope`).
- **Re-pin confirmation** `ctest -R
  'cov_pilot_(cross_chain_swap_EscrowDst|farming_FarmingPool|
  st1inch_St1inch|aqua)'` (11 tests): EscrowDst **CORE PASS**;
  FarmingPool **KNOWNBUG PASS** (sentinel ≠ `0%`, no spurious exit-77);
  the 5 aqua CORE-flip pins + `aqua_Aqua` PASS. Two Timeouts:
  `cov_pilot_aqua2A_4lvl_..._uint256` (pre-existing, unchanged from the
  pre-fix baseline) and `st1inch_St1inch`. **St1inch empirical (solo,
  `/usr/bin/time`):** exit 1, **91.83 s wall**, ESBMC's own
  `--timeout 90` fires (`ERROR: Timed out`) after emitting
  `Branches : 688 / Reached : 0 / Branch Coverage: 0%`. Solo it stays
  under any reasonable ctest wall and the sentinel `^Branch Coverage:
  [1-9]` does not match `0%` ⇒ **KNOWNBUG PASS**. It only trips the
  180 s ctest Timeout under `-j4` contention with the other heavy
  k-induction coverage jobs (host overload, the
  `feedback_regression_memory_cap` hazard). Pre-fix St1inch
  ctest-passed by *fast-crashing* (`$address` SIGABRT ~37 s); post-fix
  it is a genuine ~92 s k-induction-budget-burn (0% coverage) — the
  true post-fix behaviour and an **accepted** k-induction timeout per
  AGENTS.md, **not a Stage-3 logic regression** (the diagnosed library
  crash is verifiably gone). Same accepted class as the `_uint256`
  pin; surfaced (not masked) per `feedback_coverage_failure_is_signal`.

**Two newly-exposed independent blockers (NOT this stage's bug;
recorded, not chased — `feedback_strict_stage_authorization`).**
1. FarmingPool: `nonexistant member "getTotalSupply" in "struct
   BytesStatic"` — an abi/bytes-cast lowering gap, distinct root cause.
2. St1inch: k-induction branch-coverage budget-burn (Reached:0 within
   timeout) — same family as `cov_pilot_aqua2A_4lvl_..._uint256`,
   pre-existing orthogonal. Each needs its own separately-authorised
   stage.

## Scope

Stage-3 diagnosis + the diagnosed library-receiver fix complete and
verified. The two newly-exposed blockers above, the `Reached: 0`
library-function pin (`cov_pilot_lop_MakerTraitsLib_useBitInvalidator`,
KNOWNBUGS row b) and the LOP library-coverage methodology (Stage 4)
remain out of scope (separate authorisations).
