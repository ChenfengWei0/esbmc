# Where the work stands (2026-07-30, evening)

Written to survive a context compaction. Read this, then
`notes/path-cov-assert-plan.md` and `notes/emitter-ce-value-loss-audit.md`.

## Landed today

| commit | what |
|---|---|
| `476fb89df7` | per-path DECISION SEQUENCES published — puts path coverage and branch coverage on one denominator |
| `1f890fb4dd` | external invocation scripts (`notes/coverage/scripts/pathcov_*`) + first three reproducers |
| `5ee20ea2e9` | **frontend fix**: a local declared with an initialiser inside an INHERITED function was zeroed |
| `4bd98cd328` | **collector fix**: the baseline collector silently rewrote its own scope when its source trees disappeared |
| `8fb9162cd1` | **gate fix**: a bar of 0 passed anything; stale `reports/` inflated the numerator; `N/A: 0 units` asserted a cause it never checked |
| `1a2eeea2de` | `t2_runnability.py` capped a unit at the SLICE REMAINDER and filed the artifact as a measured TIMEOUT |
| `3efdda1b18` | `reports/` reconciled with the journal; empty exclude list refused; `REFUSE` no longer the default verdict |
| `7c2440da67` | **the re-collected baseline** |

## The question that is still open

> "当前和 branch coverage 对齐或者更好了吗"

**Not answered yet, and no number from before today may be used.** Both sides
were collected with the buggy frontend, and the branch-coverage baseline is
additionally dated 2026-05-20 while the binary has taken two months of commits.

Sequence to answer it:

1. Re-baseline (branch coverage) — **DONE**, committed `7c2440da67`.
2. Re-collect the product side — `notes/coverage/scripts/pathcov_all.sh 180`.
   The pre-fix journals AND the pre-fix `reports/` trees are archived as
   `notes/coverage/pathcov/<bench>/{runs,index}.prefix-buggy-frontend.*` and
   `reports.prefix-buggy-frontend/`, so the collector starts from zero. 180s per
   run is twice the baseline's 90s outer budget per focused run, and that ratio
   is the justification — not a number picked to make something fit.
3. `python3 notes/branch_gate.py` for the gate table.

### The re-baseline result

Same inputs, same commands, today's binary against the dataset locked
2026-05-20:

| benchmark | denom | locked | re-collected |
|---|---|---|---|
| aqua_Aqua | 8 | 7 | 7 |
| cross_chain_swap_EscrowDst | 18 | 18 | 18 |
| cross_chain_swap_EscrowSrc | 16 | 16 | 16 |
| farming | 26 | 26 | 26 |
| limit_order_protocol | 3 | 3 | 3 |
| **st1inch_St1inch** | 86 | **83** | **72** |

`branchesTotal` unchanged everywhere (METHODOLOGY 8.2), checked rather than
asserted.

Five reproducing to the unit is what makes the sixth attributable: the
intervening two months of commits are inert for this measurement, and the only
benchmark that moved is the only one carrying the shape the inherited-local
fix addresses. Direction as expected — removing manufactured coverage lowers the
bar.

**A correction worth keeping:** an earlier reading of this same diff reported
"the baseline is dropping" from the per-function `rawReached` fields (8→2, 8→7,
9→8 on aqua). Those are single focused runs' raw branch-arm counts. The gate
uses `total.esbmcReached`, the union over all runs intersected with the
canonical decision lines, and that did not move at all. Report the field the
conclusion depends on, not the field that changed.

## Subgoal status

1. **external invocation scripts** — done (`1f890fb4dd`).
2. **align with / beat branch coverage** — blocked on the two re-collections
   above. Known sub-blockers from the pre-fix run, all to be re-measured, none
   to be quoted: `ImmutablesLib` 0/8 on both Escrows, `FarmingPool` 4/12, and 15
   runs that produced no report.
3. **interval inputs** — not started.
4. **R0/R1/R2 assertions** — not started as code, but the design is no longer
   speculative. `notes/path-cov-assert-plan.md` carries the file-and-line plan,
   and its appendix records that the one load-bearing premise is **CONFIRMED**:
   an exit read of `member(sol:@_ESBMC_Object_<C>, field)` does observe the
   unit's writes. The frontend routes the write through a `this` POINTER, but
   symex dereferences before recording, `symex_assign_member` rewrites
   `a.c = e` into `a = with(a, c, e)`, and `slice.cpp:90` asserts every SSA lhs
   is a bare symbol — which `goto_coverage.cpp:2769-2782` already states, from
   measurement, is the object symbol.

   Six conditions came with it. The one that would otherwise be a silent hole:
   `resolve_coord` picks the contract object by SUBSTRING match on
   `scope_contract`, so `--contract Escrow` matches `_ESBMC_Object_EscrowSrc`
   and an empty `scope_contract` matches anything. Reading the wrong object
   makes `post == pre` hold vacuously with no error — on exactly the
   EscrowSrc/EscrowDst shape. The mode must match the unit's own contract
   exactly (`contract_of`, `goto_coverage.cpp:6601-6615`). Also:
   `coord_expressible` is the wrong gate for R1, since `==`/`!=` is expressible
   on the `bool` it refuses.

## Open, deliberately not guessed

- Why branch coverage reaches 38 edges at `--unwind 4` and 94 at `--unwind 8`
  on the real st1inch benchmark.
- Whether `cov_pilot_farming_FarmingPool` and `cov_pilot_aqua_Aqua_full` timed
  out before today's frontend fix. Ten of the thirteen non-`napp` regression
  timeouts are provably unaffected (their contracts contain no inheritance at
  all, so `is_inherited` is never set); those two and `cov_pilot_st1inch_St1inch`
  are the inheritance-heavy flattened projects, and only st1inch has measured
  before/after evidence (`0 VCC / 1167 assignments` → `45 VCC / 4321`).
- The Foundry emitter substitutes `0` / `address(0)` for a counterexample value
  it could not recover, and emits it as if it were the counterexample's own
  value (`foundry.cpp:1351-1352`). It also emits `vm.startPrank(address(0))` and
  `vm.warp(0)`, which cannot happen on chain. Twenty-one loss sites are
  tabulated in `notes/emitter-ce-value-loss-audit.md` with a proposed fix in the
  named-obstacle shape (mark → exclude → count on stdout). Not implemented.

## Environment facts worth not re-deriving

- `regression/testing_tool.py` STRIPS `--timeout` and `--memlimit`
  (`UNSUPPORTED_OPTIONS`, line 137), so a `test.desc` carrying them is bounded
  only by `ESBMC_REGRESS_TIMEOUT` (180s in this build). A timed-out test is an
  unconditional ctest failure in CORE *and* KNOWNBUG alike.
- `regression/CMakeLists.txt` discovers test directories at CONFIGURE time —
  a newly created regression directory needs `cmake .` in `build/` before ctest
  can see it.
- `notes/coverage-comparison/<project>/` no longer holds the 1inch source trees
  or `_results/lcov.info`. The scope input that depended on them is now pinned
  in `notes/coverage/inputs/own_contracts.json`; the native column is carried
  forward and labelled.
