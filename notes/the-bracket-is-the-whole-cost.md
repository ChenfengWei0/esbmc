# The geometric bracket is ~92% of the wall clock and contributes nothing at this budget

> **CORRECTIONS, 2026-07-31, after an adversarial review and the full sweep.
> Three claims in the first version of this file were wrong. They are struck
> below rather than deleted.**
>
> **1. THE 28-OF-41 STATISTIC WAS NEVER EVIDENCE ABOUT THE BRACKET.** This file
> originally offered "of 41 non-certified paths, 28 failed with *no outer-box
> round finished — a BUDGET outcome*" as corpus support for "the bracket
> contributes nothing". That reason is produced by ANY outer-box round that
> times out, and the REFINE rounds are outer-box rounds too. `--skip-bracket`
> removes only the geometric one. Refuted by the sweep this file launched: with
> `--skip-bracket` on for all 65 records, the identical reason is still the
> largest bucket at **61**. Everything resting on that statistic is withdrawn.
> What survives is only the paired run below, which is a clean single-variable
> measurement on two named samples.
>
> **2. "TIMES 4 FROM THE JOBS" IS UNSUPPORTED AND WAS WRONG IN KIND.** Every
> number in the combined table is a PER-UNIT wall clock, and per-unit wall clock
> does not improve with concurrency — it gets WORSE. The speedup from jobs lives
> in total makespan, which this file never measured. Worse, the pool is created
> and joined PER BENCHMARK, so each boundary is a barrier: on aqua the eight
> walls are 0.3, 0.3, 15.9, 20.3, 22.0, 24.3, 66.1, 180.4 and the makespan floor
> is `max(180.4, 329.6/4) = 180.4`, set entirely by one unit. Realised speedup
> on aqua is nearer **1.35x**, not 4x.
>
> **3. THE COMBINED TABLE CONFOUNDS THREE VARIABLES, NOT TWO.** `skip_bracket`
> false→true, `jobs` 1→4, **and `memlimit_gib` 8→6** — the `--jobs 1` path
> bypasses the budget arithmetic entirely and hardcodes 8g. Only the paired
> `aqua.push` run isolates one variable.
>
> **AND ONE THING THE REVIEW ESTABLISHED THAT THIS FILE SHOULD HAVE:** `--jobs`
> is not a scheduling flag at these budgets, it is a MEASUREMENT parameter. The
> driver's internal per-esbmc budget is 180s of WALL CLOCK; `aqua.push` takes
> 15.0s at `--jobs 1` and 20.3s at `--jobs 4`, so concurrency inflates per-unit
> wall by ~35%, and any internal query above ~133s serially crosses the 180s
> line at `--jobs 4`. `aqua.ship` is recorded `KILLED` at `wall_s 180.4` with
> `exit 1` — the OUTER 600s timeout never fired, so that bucket was decided by
> the scheduling flag. Nobody has measured it at `--jobs 1`.

The corpus stage-2 sweep was a four-hour serial job, which made it a blocker
rather than a measurement. Two things fixed that, and both are measurements
rather than guesses.

## 1. The bracket does not finish, and nothing is lost by skipping it

Paired run, `aqua.push`, identical command apart from `--skip-bracket`:

| | wall | bracket | certified region |
|---|---|---|---|
| with bracket | 195s | `180.1s`, then `[bracket] {}` — **measured nothing** | `amount in [0, 2^256-2], app/maker/token in [0, 2^160-1], msg.value == 0` |
| `--skip-bracket` | 15s | not run | **byte-identical** |

Every other line of the two logs matches: same level-0 result, same refine span,
same regions, same drop, same accounting footnote.

**Second sample.** The first was the S4 fixture, where the bracket also hit its
budget and level-0 plus refinement still produced every exact domain. Naming
both, per the single-sample rule: `aqua.push` and the `Punch` fixture.

**What this does and does not say.** It does NOT say the geometric bracket is a
bad idea — its stated purpose is to bracket a bound of unknown magnitude in ONE
run, and on a coordinate whose boundary is far from any power of two that is
still the right shape. What it says is narrower and enough to act on: **at the
180s per-run budget this corpus uses, the bracket does not finish on the units
measured, so it contributes nothing and costs about 92% of the wall clock.**
The corpus sweep therefore runs with `--skip-bracket`, and every record carries
that flag so a later reader cannot compare across configurations by accident.

~~The corpus data already agreed before the pair was run: of 41 non-certified
paths in the first partial sweep, 28 failed with "no outer-box round finished —
a BUDGET outcome", which is the bracket timing out.~~ **WITHDRAWN — see
correction 1 at the top.** That reason fires for any outer-box round that times
out, and the refine rounds are outer-box rounds; with `--skip-bracket` on for
the whole sweep it is still the largest bucket, at 61 of the non-certified
paths. The dominant corpus failure is a wall-clock budget outcome in the LADDER
generally, not in the bracket specifically — which leaves the refine rounds, not
the bracket, as the thing to measure next.

## 2. Concurrency: the rule was right, and it is now discharged rather than relaxed

The standing rule has been "never run esbmc concurrently — it exhausted this
machine once and forced a reboot". That rule is correct and its stated REASON is
memory. The crash predates the discipline of passing `--memlimit` on every run.

With a limit enforced per process, "how many fit" stops being a guess:

    MemAvailable 40.6 GiB, budget 60% = 24.4 GiB, 4 jobs x 6 GiB = 24 GiB

`certify_all.py --jobs N` computes that before anything runs, prints it, and
**REFUSES** when it does not fit rather than shrinking the limit:

    REFUSING --jobs 40: ... 0 GiB per job and below the 4 GiB floor. Below the
    floor a unit starts dying of the memory limit rather than of the problem,
    which would make this a measurement change and not a scheduling one.
    Use --jobs 5 or fewer.

Silently shrinking the limit is the failure this repository keeps meeting from
the other side — a bound that quietly rewrites the thing it was supposed to
bound. Measured while running: 4 concurrent jobs, 3 GiB resident of 42.

## The combined effect, measured

| unit | before | after |
|---|---|---|
| aqua.rawBalances | 193s | **16s** |
| aqua.safeBalances | 198s | **22s** |
| aqua.dock | 126s | **24s** |
| aqua.push | 197s | **20s** |

Same CERTIFIED verdicts, same regions. About 10x per unit from the ladder, times
4 from the jobs.

## What travels with each record now

`skip_bracket`, `level0`, `probes`, `refine_rounds`, `shrink_rounds`,
`unit_timeout_s`, `jobs`, `memlimit_gib`. Two units measured under different
ladders are not comparable, and this project has already paid once for a ratio
whose numerator and denominator came from runs that shared only a benchmark
name.
