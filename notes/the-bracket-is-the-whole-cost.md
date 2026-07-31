# The geometric bracket is ~92% of the wall clock and contributes nothing at this budget

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

The corpus data already agreed before the pair was run: of 41 non-certified
paths in the first partial sweep, **28 failed with "no outer-box round finished
— a BUDGET outcome"**, which is the bracket timing out. It was the dominant
failure reason, and it was a property of the ladder rather than of the paths.

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
