# Per-contract semantics-A fix — autonomous worklog

Authorized 2026-05-16 (user AFK, blanket S1→S4, no per-stage re-approval;
technical gates R(3-a)/R2/R4 confirmed by code-read not by asking).

Scope: `--contract X` ⇒ branch denominator+numerator counts ONLY decisions
lexically inside `contract X {}`. No `--contract` ⇒ unchanged (aa7c7bf9af
whole-unit dedup). NO new `--coverage-whole-unit` option (descoped by user).

Target flips (this-session measured): EscrowDst 80→4, FarmingPool 318→24,
St1inch 506→48, Aqua-full 166→12, Aqua-min 4→4 (unchanged).

Constraints: never commit; never touch unrelated WT changes
(solidity_blockchain.c / solidity_misc.c / solidity_language.cpp /
EscrowDst test.desc M); coverage runs --k-induction; esbmc with timeout,
never backgrounded; ctest once, ≤5min, mem 4096 -j4.

## S1 FINDINGS (gates confirmed by code-read)

- **R(3-a) RESOLVED**: on-disk `.solast` is raw solc AST (no is_inherited;
  `merge_inheritance_ast`/`add_inherit_label` are ESBMC in-memory xforms,
  solidity_convert_inheritance.cpp:22-44 — they only ADD
  `current_contract`(=copy-site)+`is_inherited`, never touch `scope`).
  IfStatement has NO `scope` (only declarations do). Declarer =
  **enclosing FunctionDefinition's `scope` → ContractDefinition name**;
  FunctionDefinition.scope = true declaring CD id, preserved across
  merge-copy (verified on EscrowDst .solast: deploy→Create2,
  withdraw#1838→EscrowDst, _validateImmutables#1714→BaseEscrow /
  #1764→Escrow). `current_functionDecl` (solidity_convert.cpp:45) is the
  json node of the function being converted; `["scope"]` = declarer id.
  Resolver: `find_decl_ref(int)` (solidity_convert.h:61) → node →
  `["name"]` if ContractDefinition. Free fn scope=SourceUnit (not CD) →
  fallback empty = unattributed = correctly excluded under --contract.

- **R2/R3 RESOLVED & SIMPLIFIED**: numerator is *downstream* of
  instrumentation. bmc.cpp:895 `tracked=reached_claims.size()`, :914
  `reached/total`, both raw, no all_claims intersection. Therefore if
  Layer B simply **does not insert** the assert pair for C-foreign
  decisions (gate the two insert_assert at goto_coverage.cpp:285-286),
  then get_total_cond_assert (denominator, counts instrumented asserts
  only) AND reached_claims (numerator, only instrumented asserts can be
  hit) are BOTH auto-scoped → percentage correct by construction.
  **No bmc.cpp change needed.**

- **R4 RESOLVED**: gate lives only in branch_coverage(); other modes
  (assertion/k-path/branch-func) have their own methods, unaffected.

- **Plumbing**: `--contract` value = `cmdline.getval("contract")` at
  esbmc_parseoptions.cpp:3560-3565 (cmdline in scope at the
  branch_coverage call site). irept API: `location.set(name,irep_idt)`
  / `location.get(name)` (util/irep.h:141-150); locationt : irept.

- **Layer A injection**: stamp `location.set("sol_decl_contract", <name>)`
  in ALL THREE setters in solidity_convert_util.cpp (get_location_from_node
  :27, get_start_location_from_stmt, get_final_location_from_stmt) — they
  already gate on current_functionDecl; add a memoized helper
  resolving current_functionDecl["scope"]→CD name. Stamping all three
  guarantees whatever location lands on the `IF g GOTO` carries it.

## Status
- [x] S1 gates + Layer A injection point
- [x] S2 Layer A frontend stamp (src-span attribution, not scope: modifiers
      have no `scope` field — first attempt zeroed EscrowDst; src-byte-span
      of decision AST node is uniform for fn+modifier, copy-invariant,
      = the lexical-declarer definition, matched offline ground truth)
- [x] S3 Layer B gate + 4-pilot flip — EXACT hits:
      EscrowDst 80→4 (Reached 4/4=100%), Aqua-min 4/3/75% (unchanged),
      Aqua-full 166→12 (8/66.67%), Farming 318→24 (0/0%, upstream blocker),
      St1inch 506→48 (0/0%, upstream blocker). bmc.cpp untouched
      (numerator auto-scoped via not-instrumenting C-foreign decisions).
- [x] S4 ctest sanity + re-pin + docs/memory

## S4 RESULT
- **goto-coverage 109/109 PASS** — no-`--contract` C/C++ branch/cond/
  k-path/assertion path completely unchanged (gate is scope_contract-
  guarded; Layer A's extra irep field absent from as_string() dedup key).
- Affected Solidity set re-pinned + 8/8 PASS:
  - cov_scope_single_contract_pass — PASS (control, unchanged).
  - cov_scope_sibling_contract_knownbug — **KNOWNBUG→CORE** 4/2/50→2/2/100.
  - cov_scope_uncalled_library_knownbug — **KNOWNBUG→CORE** 4/2/50→2/2/100.
  - cov_scope_modifier_crosscontract_knownbug — **KNOWNBUG→CORE**, now
    `No branch detected` (B's only branch is A's modifier `gate`, declared
    in A → attributed to A, excluded from B). This is the named, ACCEPTED
    3b completeness trade-off (no escape hatch) = intended STABLE behavior,
    not a bug ⇒ CORE is the honest classification. .sol header comment
    rewritten (old text described rejected reachability semantics).
    NOTE: directory name `_knownbug` is now a misnomer — optional rename
    `cov_scope_modifier_crosscontract` left for user (dir-name only;
    ctest discovers by dir, no other refs).
  - cov_pilot_aqua_Aqua — CORE 4/3/75% (unchanged, no regression).
  - cov_pilot_aqua_Aqua_full — CORE, denominator 166→12 (8/66.67%).
  - cov_pilot_cross_chain_swap_EscrowDst — **KNOWNBUG→CORE** 90→4,
    4/4/100%.
  - cov_pilot_farming_FarmingPool — KNOWNBUG, denominator 338→24 pinned
    (`^Branches : 24$` matches = fix recorded; `^Branch Coverage: [1-9]`
    deliberately will NOT match the upstream-blocked `0%` → stays
    correctly-failing KNOWNBUG until the pre-existing Reached:0 GOTO-gen
    blocker is fixed; same sentinel design as the original pin).
  - cov_pilot_st1inch_St1inch — KNOWNBUG, denominator 688→48 pinned, same
    [1-9] sentinel. **Pre-existing ctest timeout** (179 KB flat under
    k-induction exceeds the regress timeout) — independent of this change
    (scoping strictly REDUCES symex work); documented out of scope, not
    masked (memory: St1inch solver-hard/heavy).

## NOT COMMITTED (per constraints — user review on wake)
All changes left uncommitted. Unrelated pre-existing WT changes
(solidity_blockchain.c / solidity_misc.c / solidity_language.cpp) were
NOT touched and must NOT be folded into any commit of this work.

## Files changed (mine only — DO NOT commit unrelated WT files)
- src/solidity-frontend/solidity_convert.h (helper decl + cd_spans member)
- src/solidity-frontend/solidity_convert_util.cpp (current_decl_contract +
  stamp in 3 location setters)
- src/goto-programs/goto_coverage.h (scope_contract member)
- src/goto-programs/goto_coverage.cpp (gate in branch_coverage())
- src/esbmc/esbmc_parseoptions.cpp (wire cmdline --contract → scope_contract)

## Log
