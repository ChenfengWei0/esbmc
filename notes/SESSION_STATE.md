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

## The question that is still open

> "当前和 branch coverage 对齐或者更好了吗"

**Not answered yet, and no number from before today may be used.** Both sides
were collected with the buggy frontend, and the branch-coverage baseline is
additionally dated 2026-05-20 while the binary has taken two months of commits.

Sequence to answer it:

1. Re-baseline (branch coverage) — `notes/coverage/scripts/collect.py esbmc <bench>`
   for all six. aqua_Aqua done; the other five were running when this was
   written. Results land in `notes/coverage/data/esbmc_*.json` (UNCOMMITTED
   until all six are in — a half-collected baseline is worse than none).
2. Re-collect the product side — `notes/coverage/scripts/pathcov_all.sh`.
   The pre-fix journals are archived as
   `notes/coverage/pathcov/<bench>/runs.prefix-buggy-frontend.jsonl`, so the
   collector will start from zero rather than resuming onto stale rows.
3. `python3 notes/branch_gate.py` for the gate table.

### What the first re-baselined benchmark already shows

aqua_Aqua, same inputs, same commands, today's binary vs the locked 2026-05-20
numbers: whole-contract pass 10.8s → 666.86s (hits the 600s budget, exit 1),
`dock` 1.67s → 60.04s (hits its 60s budget), reach `dock` 8→2, `pull` 8→7,
`push` 9→8.

**No cause is claimed.** Two months of commits separate the two runs. The
direction is unfavourable to us — a lower branch-coverage baseline flatters the
path-coverage comparison for a reason unrelated to path coverage — which is why
it is recorded rather than left to be noticed later.

## Subgoal status

1. **external invocation scripts** — done (`1f890fb4dd`).
2. **align with / beat branch coverage** — blocked on the two re-collections
   above. Known sub-blockers from the pre-fix run, all to be re-measured, none
   to be quoted: `ImmutablesLib` 0/8 on both Escrows, `FarmingPool` 4/12, and 15
   runs that produced no report.
3. **interval inputs** — not started.
4. **R0/R1/R2 assertions** — not started as code. The file-and-line
   implementation plan is `notes/path-cov-assert-plan.md`; its one UNVERIFIED
   premise is whether the post-state read of `state.<field>` at a path's exit
   observes the unit's writes, and fixture group 1 in that plan settles it.

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
