# Overnight run log (session started 2026-04-18)

## Open design questions (for morning review, not blocking this run)

**Try/catch in TOD harness** — user asked twice: "总是加 try/catch?" then
"用实例参数后似乎不需要 try/catch?".  My read: with param-form + fresh
instances, require/revert inside sub-calls naturally prunes infeasible
paths via `assume()` semantics; the race check fires only on orderings
that both succeed.  Dropping try/catch entirely would give a cleaner
model, fewer phantom paths, less OOM pressure.  Current tree keeps the
selective-wrap heuristic (wrap only when callee has syntactic revert)
— committed as `9a49f1342b`.  Not changing this during the overnight
run; revisit after batch data shows whether coverage suffers from
missed deep-nested reverts.

## Per-contract upgrade + ESBMC run log

Schema: `<name>: <upgrade_status> | <pair_tried> | <esbmc_verdict>
        | <elapsed> | <notes>`

Will be updated incrementally as each contract completes.

## 2026-04-18 overnight snapshot

**Upgrade pass: 33/33 contracts now compile with solc 0.8.30.**

- Phase B (per-contract upgrade): all 33 success via parallel Explore-agent
  work.  Each agent applied targeted single-line fixes keyed on solc
  errors (missing override, contract→address cast, emit prefix, memory
  location, SafeMath infix rewrite, etc.).
- Phase C (per-contract ESBMC `--tod-race-check=fa,fb` with hand-picked
  pair): sequential run in progress under hardened wrapper.  Early reads:
  - PlayCash burn/burnFrom: **VERIFICATION FAILED** (real TOD race
    confirmed — `__tod_race_check` assertion fires)
  - BB burn/burnFrom, BMUS/Xpense transfer/transferFrom: SUCCESSFUL
  - XCTCrowdSale: 0 candidate pairs (single effective entry)
  - LAAR auto-mode (stale, from prior session): 56 pairs 44 clean 12 err
  - COW transfer/transferFrom: mid-run
- Pair-picking heuristic: for each contract, scan public/external
  function bodies, tally approximate state-var writes, pick pair with
  maximum write-overlap.  This seems to surface the injection-style
  pairs where TransRacer also found bugs.

## Final summary (overnight run complete, 2026-04-18)

### TransRacer 33-contract run (commit 77131523ca)
- 2 TOD found (PlayCash, GOG — both on burn/burnFrom)
- 19 CLEAN on hand-picked pair
- 1 no-pairs (XCTCrowdSale — single effective entry)
- 11 blocked by ESBMC-side pipeline bugs (HARNESS_ORDER_BUG x7,
  FRONTEND_ADDR_BUG x2, HARNESS_EMIT_BUG x1, CRASH x1)

### SolidiFI 49-case run (`Dataset/benchmark_tod/results/esbmc_balance/`)
- 0 TOD found
- 10 CLEAN
- 5 FAILED_OTHER (non-TOD assertion)
- 34 blocked by ESBMC-side pipeline bugs (PARSE_ERROR x14,
  CONVERSION_ERROR x7, CRASH x6, UNKNOWN x4, HARNESS_EMIT_BUG x3)

### Known follow-up work (after user review)
1. **Topological contract-decl sort in TOD harness emitter** — would
   fix ~21 HARNESS_ORDER_BUG / PARSE_ERROR cases across both benchmarks.
2. **Shared pre-race state model** — the single biggest cause of 0
   TOD-Balance findings on SolidiFI.  Current harness allocates c1 and
   c2 independently, so they sit in different "uninitialised" IS slots
   and cannot exhibit a race on a shared variable that only matters
   under a specific Updated State.  Needs: snapshot/restore pattern
   OR re-seed c1 and c2 to the SAME nondet pre-race state.  Per earlier
   user observation: "用实例参数后似乎不需要 try/catch".
3. **Frontend address-vs-contract type ambiguity** — affects
   ProofOfReview, Yihaa in TransRacer set + 7 cases in SolidiFI.  One
   common root cause likely.
