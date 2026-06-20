# Plan: Bounded-by-default Solidity transaction harness (dispatcher loop bounding)

**Branch:** `feat/single-fn-loop-elision`
**Status:** PROPOSAL v3 — bounded-by-default; label via frontend `log_warning`
only (verdict line unchanged). Project-owner decision after two adversarial
review rounds.
**Author:** investigation 2026-06-20

> **v3 delta.** Locks the labeling mechanism to a Solidity-frontend
> `log_warning` and explicitly **keeps the `VERIFICATION SUCCESSFUL` line
> unchanged** (§7.3). Records the accepted residual risk that verdict-line
> parsers cannot distinguish a bounded success (§5.1, responding to Codex v2
> critical). Narrows the §4 monotonicity claim to model-level and adds the
> targeted validation requirements Codex v2 asked for (§8).

> **v2 change of direction.** v1 proposed eliding the harness loop only for
> single-function contracts, gated behind `--solidity-precise`. After adversarial
> review (Codex) and user decision, the design is now:
>
> - **Default = bounded.** The harness transaction-dispatcher loop is unrolled a
>   fixed **N = 2** times instead of `while (nondet_bool())`, for **all**
>   contracts (single- and multi-function). This trades soundness of the
>   SUCCESSFUL verdict for guaranteed convergence — a deliberate product choice
>   (option "甲").
> - **`--solidity-precise` = sound mode.** Enabling it restores the unbounded
>   `while (nondet_bool())` loop and relies on k-induction for an unbounded
>   proof (may return UNKNOWN/timeout — honestly inconclusive).
> - **Loud labeling is mandatory.** A bounded run prints a prominent `[approx]`
>   line stating the transaction bound; the bare `VERIFICATION SUCCESSFUL` line
>   text is left unchanged (see §7.3).

---

## 1. Problem statement

A large share of the `esbmc-solidity` suite is forced onto
`--unwind N --no-unwinding-assertions` because k-induction does not converge on
the generated harness. Scan of `regression/esbmc-solidity/*/test.desc`:

- **292** tests use `--no-unwinding-assertions`.
- Of the 278 with a parseable `--contract`, **223 (≈76%)** target a contract
  with exactly **one** public/external non-constructor function.

`--unwind N --no-unwinding-assertions` is itself an unsound *silent* truncation
(memory `feedback_silent_truncation_flags`): paths beyond `N` are dropped with no
diagnostic. The proposal replaces that ad-hoc, silent per-test workaround with a
single, **loudly-labeled**, deterministic bound.

## 2. Root cause of k-induction divergence

The per-contract harness `_ESBMC_Main_<C>` is built in
`multi_transaction_verification` (`src/solidity-frontend/solidity_convert_contract.cpp:653-745`):

```
_ESBMC_Main_C() {
  __ESBMC_HIDE:
  // constructor already ran (deployer binding)
  while (nondet_bool()) {          // <-- lines 700-707  (UNBOUNDED tx sequence)
    _sol_per_tx_reseed();          //     fresh msg.sender/value, block.* per tx
    _ESBMC_Nondet_Extcall_C();     //     inner if-chain dispatcher
  }
}
```

The inner dispatcher `_ESBMC_Nondet_Extcall_<C>` (`get_unbound_function`,
`solidity_convert_constructor.cpp:235-424`) is an if-chain
`if (nondet_bool()) { f1(); return; } if (nondet_bool()) { f2(); return; } ...`
over public/external functions (constructor skipped; fallback/receive ARE
dispatched).

The outer `while (nondet_bool())` is the **only structurally-unbounded loop** for
most contracts and the one k-induction must find an inductive invariant for. When
it cannot, the run diverges. Bounded *inner* source loops (`for (i<3)`, fixed
`.push` chains) are fully unwound by k-induction and are not the problem.

## 3. Design (v2): bounded-by-default dispatcher

Replace the unbounded `while (nondet_bool())` with a **deterministic N-fold
unroll** of `{ reseed(); dispatch(); }`:

```
_ESBMC_Main_C() {            // DEFAULT, N = 2
  __ESBMC_HIDE:
  _sol_per_tx_reseed();  _ESBMC_Nondet_Extcall_C();   // tx 1
  _sol_per_tx_reseed();  _ESBMC_Nondet_Extcall_C();   // tx 2
}
```

- Each iteration keeps the inner `if(nondet)` dispatcher, so each "tx" is still
  "0-or-1 nondet-chosen public function" — multi-function contracts retain full
  per-tx function-choice nondeterminism; only the *number* of txs is bounded.
- No `while` ⇒ no unbounded loop ⇒ k-induction converges (or plain BMC suffices).
  Inner bounded loops keep their own unwinding assertions (we do **not** add a
  global `--no-unwinding-assertions`).
- Single-function contracts are just the N-fold unroll with a one-branch inner
  dispatcher — no special case needed. (v1's "single-fn elision" = this with
  N=1; we now default N=2.)

### 3.1 Options (independent sub-options, not welded to `--solidity-precise`)

| Option | Effect | Default |
|---|---|---|
| `--solidity-max-tx N` | Unroll the dispatcher loop exactly N times. | **2** |
| `--solidity-max-tx 0` (or `--solidity-unbounded`) | Restore `while (nondet_bool())` (unbounded; k-induction). | off |
| `--solidity-precise` | **Unchanged** primary meaning (precise contract-instance/address-uniqueness scan). **Additionally** acts as the "sound master switch": implies unbounded dispatcher (as if `--solidity-max-tx 0`) **unless** `--solidity-max-tx` was given explicitly. | off |

Rationale for not welding to `--solidity-precise` (Codex critical #1): the flag's
documented contract is "opt into precise/**sound** modelling." In v2 the
*default* is the under-approximation and `--solidity-precise` makes things
**more** sound (restores the loop) — consistent with the name. A dedicated
`--solidity-max-tx` gives granular control without overloading semantics.

## 4. Soundness posture (explicit, by design)

This is a **deliberate** trade of SUCCESSFUL-soundness for convergence
(option 甲). We do **not** claim the bounded default is sound; we make it
**honest**:

- **FAILED: always sound.** Bounding only removes paths, so any violation found
  is real. (Bug-hunting / audit use is unaffected — the common case.)
- **SUCCESSFUL: bounded, not a proof.** A bounded SUCCESSFUL means "no violation
  within N transactions." Bugs needing >N txs (state accumulation, reentrancy,
  TOD) are missed. This is surfaced loudly (§7.3) and removed by
  `--solidity-precise`.
- **Model-level monotonicity (narrowed claim, per Codex v2 medium):** at the
  *abstract model* level the bounded run's reachable states are a subset of the
  unbounded run's, so a *model-level* result can only flip FAILED→SUCCESSFUL,
  never the reverse. This is **not** automatically true of the *generated code*:
  rewriting `while{body}` into N duplicated `reseed(); dispatch();` blocks
  changes nondet/reseed allocation sites and trace shape, which could in
  principle alter solver/unwinding behavior. Therefore the "never
  SUCCESSFUL→FAILED" property must be **validated with GOTO/VCC + verdict
  evidence** (§8), not asserted. Until then, treat it as the intended design
  goal, not a guarantee.

With N=2 the canonical `count++; assert(count<2)` example now **FAILS** by
default (two calls reach `count==2`); `count<3` would need N≥3. The general
"misses >N" property remains and is the labeled, accepted cost.

## 5. Response to the adversarial review (Codex, verdict needs-attention)

- **critical #1 (`--solidity-precise` silent downgrade):** RESOLVED by
  inverting the binding. Default is bounded; `--solidity-precise` now *increases*
  soundness. Not welded — `--solidity-max-tx` is the granular control (§3.1).
- **critical #2 (single-fn predicate unsound):** DISSOLVED — v2 drops the
  "prove it's safe to elide" framing entirely. We bound *all* contracts and do
  not claim SUCCESSFUL = unbounded proof. There is no soundness predicate to get
  wrong; there is a bound and a label.
- **critical #3 (counterexample defeats proofs):** ACCEPTED as the defining
  trade-off, not a bug to hide. N=2 catches the specific example; the general
  ">N misses" cost is documented (§4) and pinned as a KNOWNBUG test (§8).
- **high #4 (overstated scope):** REFRAMED — v2 is a global default change, not a
  "migrate 86 tests" claim; the 223/86 split is no longer load-bearing. Cost is
  now measured by full-suite before/after verdicts (§6, §8).
- **high #5 (sound alternatives not evaluated):** ADDRESSED — sound alternatives
  are not abandoned; they ARE the `--solidity-precise` path (unbounded loop +
  k-induction, with auto-invariant inference as future work, memory
  `reference_k_induction_auto_invariant`). v2 chooses bounded-by-default as the
  *default* posture while keeping the sound path one flag away. Alternatives
  table in §9.

### 5.1 Accepted residual risk (Codex v2 critical/high #1–2)

Codex v2 objected that bounded-by-default while keeping the bare
`VERIFICATION SUCCESSFUL` line means automation/CI/dashboards that parse only the
verdict line will read a bounded result as a full proof — a *silent* default
soundness change. **This is accepted, knowingly, by the project owner:**

- Mitigation is the frontend `log_warning` (§7.3), which is human-visible in the
  log but **not** consumed by verdict-line parsers. We do **not** add a distinct
  bounded verdict line, precisely to avoid breaking existing `test.desc` regexes
  and external CI parsers.
- Consequence we accept: a consumer that reads only `^VERIFICATION SUCCESSFUL$`
  will not distinguish a bounded result from an unbounded proof. Users who need
  an unbounded proof must pass `--solidity-precise` / `--solidity-max-tx 0`.
- Rationale: today there is **no usable sound default** anyway — the unbounded
  loop diverges under k-induction (Codex v2 high #3), so the status-quo default
  is timeout/UNKNOWN, not a real proof. The project chooses a fast, deterministic
  bounded default plus a log warning over a non-terminating "sound" default.

This section exists so the trade-off is recorded explicitly rather than
discovered later.

## 6. Regression-surface risks of changing the DEFAULT (must validate)

Changing the default harness affects **every** test that does not explicitly pin
dispatcher behavior. Two concrete risks:

1. **`_fail` tests needing >2 top-level calls** would flip to SUCCESSFUL and
   break. Mitigation: scan + run. The single-fn `_fail` tests I inspected all
   fail on a *single* call (e.g. `assert_revert_two_calls_fail` = two *sub*-calls
   in one `check`), so they survive N=2. Multi-fn `_fail` tests that genuinely
   need ≥3 sequenced calls are the risk set — enumerate by running the suite
   under the new default and diffing verdicts.
2. **Verdict-text breakage:** MUST keep the result line exactly
   `VERIFICATION SUCCESSFUL` / `VERIFICATION FAILED`. The bound label goes on a
   *separate* `[approx]` line (§7.3). Changing the result line would break the
   `^VERIFICATION SUCCESSFUL$` regex in hundreds of `test.desc` files.

## 7. Implementation

1. Branch `feat/single-fn-loop-elision` (current).
2. **Option plumbing** (`src/esbmc/options.cpp`): add `--solidity-max-tx N`
   (default 2); document `--solidity-max-tx 0` = unbounded. Keep
   `--solidity-precise` text; note it implies unbounded dispatcher unless
   `--solidity-max-tx` is explicit.
3. **Harness build** (`solidity_convert_contract.cpp:700-707`): read the
   effective bound; if unbounded → existing `code_whilet`; else emit the N-fold
   unrolled block of `reseed(); funccall;`. Factor the body (currently built once
   at 691-698) into a small loop emitting N copies.
4. **Multi-contract** (`prepare_harness_entry_functions`, lines 846/919): the
   per-contract `_ESBMC_Main_` each get the same bounding; the cross-contract
   nondet switch over entries is unchanged.
5. **Labeling** (§7.3).
6. Tests (§8); cppcheck on changed frontend files; clang-format; targeted ctest;
   then a capped full-suite run to catch default-change regressions (§6).

### 7.3 Labeling — frontend `log_warning` only (project-owner decision)

When the dispatcher is bounded (N finite), emit **once** via `log_warning` in the
Solidity frontend at harness-build time (`multi_transaction_verification`), e.g.:

```
log_warning(
  "Solidity harness: transaction sequence bounded to {} tx (default). "
  "A SUCCESSFUL result is bounded, NOT an unbounded proof; bugs requiring "
  "more than {} transactions are not explored. Use --solidity-precise (or "
  "--solidity-max-tx 0) for an unbounded proof.", N, N);
```

- `log_warning` is already used across the frontend (e.g.
  `solidity_convert_contract.cpp:612`), consistent with memory
  `feedback_approx_warning_visibility` (every approximation names the construct).
- **The `VERIFICATION SUCCESSFUL/FAILED` result line is deliberately NOT
  changed.** This is an explicit project-owner decision (see §5.1): the bound is
  surfaced to humans reading the log, not encoded in the machine-authoritative
  verdict line. This keeps the ~hundreds of `^VERIFICATION SUCCESSFUL$`
  `test.desc` regexes (and external CI parsers) intact at the cost documented in
  §5.1.

## 8. Test plan

PASS + FAIL exercising both modes (repo policy):

- `solidity_tx_bound_default_pass` — contract safe within 2 tx; SUCCESSFUL under
  default, and the `[approx]` bound line is asserted present in `test.desc`.
- `solidity_tx_bound_default_fail` — bug reachable in ≤2 tx; still FAILED under
  default (FAILED soundness preserved).
- `solidity_tx_bound_three_call_knownbug` — bug needs 3 sequenced calls:
  SUCCESSFUL under default N=2 (KNOWNBUG, documents the bound), FAILED under
  `--solidity-max-tx 3` or `--solidity-precise`. Pins the trade-off honestly
  (memory `feedback_knownbug_tests`).
- `solidity_tx_bound_precise_unbounded_pass` — `--solidity-precise` restores the
  unbounded loop and proves a genuinely-unbounded invariant (or is documented as
  UNKNOWN/timeout if it diverges).
- **Full-suite validation:** run the suite under the new default (capped 5 min,
  `-j` per memory `feedback_regression_memory_cap`), diff verdicts vs baseline.
  Any flipped `_fail`→SUCCESSFUL is a real over-approximation-of-bound regression
  → either bump that test's `--solidity-max-tx`/`--solidity-precise` explicitly,
  or reclassify. Produce the before/after migration list Codex asked for.

- **Targeted validation requirements (Codex v2 medium + next-steps):** before
  relying on monotonic verdicts (§4), gather concrete evidence for:
  1. **`--unwind` interaction:** confirm `--unwind` no longer governs dispatcher
     iteration count (it now only bounds inner loops); document the semantic
     change and check no test silently depended on `--unwind` for dispatcher
     depth.
  2. **Multi-contract harness:** each `_ESBMC_Main_<C>` gets the unroll; verify
     the cross-contract nondet entry switch (`prepare_harness_entry_functions`)
     still interleaves entries and that bounding per-contract doesn't drop
     cross-contract sequences the suite cares about.
  3. **Reentrancy / TOD:** confirm N=2 still admits the call interleavings those
     tests need (e.g. reentrancy recipe `--incremental-bmc --bound --cvc5`,
     memory `reference_reentrancy_bmc_recipe`); a too-small bound could hide
     reentrancy false-negatives.
  4. **Per-tx reseed freshness:** verify the N duplicated `emit_per_tx_reseed_call`
     sites each produce *fresh* nondet msg.sender/value/block.* (not accidentally
     shared), by inspecting generated GOTO for a reseed-sensitive test (e.g.
     `frame_context_intra`, `tx_origin_msg_sender_independent`).
  5. **GOTO/VCC diff:** for one single-contract, one multi-contract, one
     reentrancy, and one reseed-heavy case, compare `--goto-functions-only` /
     `--show-vcc` between unbounded and bounded harness to substantiate the
     model-level-subset claim at the code level.

## 9. Alternatives considered

| Approach | Keeps unbounded SUCCESSFUL sound? | Converges? | Verdict |
|---|---|---|---|
| **Bounded default N=2 (chosen, 甲)** | No (labeled) | Yes (deterministic) | Default; fast, honest-bounded |
| Unbounded loop + k-induction | Yes | Often no (diverges) | Kept as `--solidity-precise` sound mode |
| Auto-inferred dispatcher invariant | Yes | Sometimes | Future work under sound mode (`reference_k_induction_auto_invariant`) |
| Dispatcher summarization | Yes (if exact) | Maybe | Research; not in scope |
| Status quo (`--unwind N --no-unwinding-assertions`) | No (silent) | Yes | Worse than chosen: silent, per-test, also kills inner-loop assertions |

The chosen default is strictly more honest than the status-quo workaround
(deterministic bound + loud label vs silent truncation) while keeping the sound
path one flag away.

## 10. Open questions

1. Exact default-change regression set — only knowable by running the suite
   under N=2 (§8). Size unknown until measured.
2. Should `--solidity-precise` *also* default-bump `--unwind`/k-induction
   settings so the "sound mode" is actually attemptable, or just restore the
   loop and let it diverge to UNKNOWN?
3. Label channel: stderr `[approx]` line vs a structured field — pick whatever
   the existing `[approx]` warnings use for consistency.
