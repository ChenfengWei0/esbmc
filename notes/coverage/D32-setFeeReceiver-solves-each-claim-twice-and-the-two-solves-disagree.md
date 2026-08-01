# D32 — `setFeeReceiver`'s formula is SMALL, each claim is solved TWICE, and the two solves of one key disagree

**Read 2026-08-01** out of the completed
`solver_arms/st1inch/z3+node-flat/run.log` (33 KB, read in full). No new run.
Everything below is quoted from that log.

## Context: this replaces a withdrawn explanation

D30 proposed that st1inch's no-verdicts came from the 30-deep 256-bit `mul`/`div`
chain in `VotingPowerCalculator`'s constructor. `exp_chain_sweep.py` refuted it
the same day — a generated contract with that exact chain at depth 30 witnesses
every path of the same four-line setter in 0.003 s. D30 is withdrawn. This is
what the log actually says.

## 1. The truncated loops are STRINGS, not the exponent table

```
WARNING: Coverage may be UNDER-REPORTED: 3 loop(s) hit the unwind bound while
--no-unwinding-assertions was active …  Loops truncated:
  loop 35 at …/solidity/solidity_string.c line 245 function nondet_string
  loop 55 at …/solidity/solidity_string.c line 206 function _str_assign
  loop 56 at …/solidity/solidity_string.c line 209 function _str_assign
```

and the body of the log is ~55 lines of `Unwinding loop 55/56 iteration 1..3`,
`Not unwinding … iteration 4`, over and over. `St1inch` is an `ERC20` +
`ERC20Permit`, so it carries `name`/`symbol` strings and EIP-712 hashes the name;
ESBMC models Solidity strings with loops in `solidity_string.c`.

⚠ **This is what got TRUNCATED, which is not the same as what makes the solver
run out of memory.** Both are worth knowing and they are different claims. The
truncation is stated by the tool; the attribution of the OOM is not, and is not
made here.

## 2. The formula is SMALL, and the encoder is fast

```
Symex completed in: 0.096s (1526 assignments)
Slicing time: 0.002s (removed 651 assignments)
Generated 10 VCC(s), 10 remaining after simplification (875 assignments)
Encoding to solver time: 0.011s … 0.021s
Solving with solver Z3 v4.13.3
WARNING: z3 returned `unknown` (reason: out of memory)
Runtime decision procedure: 10.597s
```

**875 assignments, encoded in 15 milliseconds, and then z3 exhausts 8 GiB in
~10 seconds.** So "the formula is enormous" is not supported by any count in this
log. Whatever explodes, it explodes inside the decision procedure, on a problem
whose textual size is modest. That is a much narrower target than "st1inch is
big".

## 3. ⛔ EACH CLAIM IS SOLVED TWICE, AND THE TWO SOLVES DISAGREE

Five paths. **Ten solves.** In order:

| # | claim | time | outcome |
|---|---|---|---|
| 1 | `path:15` | 10.597 s | out of memory |
| 2 | `path:14` | 10.835 s | out of memory |
| 3 | `path:13` | **0.011 s** | **✓ PASSED** |
| 4 | `path:12` | **0.011 s** | **✓ PASSED** |
| 5 | `path:2` | 9.545 s | out of memory |
| 6 | `path:15` | 11.292 s | out of memory |
| 7 | `path:14` | 10.214 s | out of memory |
| 8 | `path:13` | **9.764 s** | **out of memory** |
| 9 | `path:12` | **10.776 s** | **out of memory** |
| 10 | `path:2` | 10.027 s | out of memory |

`path:13` decides in **eleven milliseconds** on its first solve and **runs out of
memory** on its second. Same key, same run, same binary, same flags.

The tool flags the duplication itself:

```
Verdicts Preserved: 2 — a claim already DECIDED whose later solve returned no
verdict kept its decision. Non-zero also means the same claim key was solved more
than once, which is a SEPARATE DEFECT
```

⇒ The verdict-preservation fix (task #27) is doing its job — those two decisions
survived — but the duplication is still there, and **the two instantiations of
one claim key are not the same query.** An eleven-millisecond solve and an
out-of-memory solve cannot be the same formula.

⇒ **So `solver-unknown 3` is not a clean measurement of "three paths the solver
cannot decide".** At least two of the five claims ARE decidable in milliseconds,
in this very run, and are recorded as `bounded-holds` only because the
preservation fix caught them. Whatever makes the second instantiation expensive
is the thing to find, and it is a property of the instantiation, not of the path.

## 4. The exit code says SUCCESSFUL while coverage is 0%

```
Properties: 10 verified ✓ 2 passed
…
Path Coverage: 0%
Path Status: F 0, I 0, U 5
VERIFICATION SUCCESSFUL
```

Already on record as a rule ("exit codes are not comparable across bounding
strategies") and here is the sharpest instance: rc = 0, `VERIFICATION
SUCCESSFUL`, and zero coverage.

## What to do next, in order of what the evidence supports

1. **Why is one claim key solved twice, and why do the two differ?** This is the
   only lead that is (a) directly evidenced here, (b) already named by the tool
   as a defect, and (c) capable of changing the number — two of five claims
   decided in 11 ms on one instantiation. Everything else is speculation until
   this is understood.
2. Only then, the OOM itself. `875 assignments / 15 ms to encode / OOM in 10 s`
   is a strange shape and it deserves its own reduction; but reducing the wrong
   query would waste the reduction.

## What this note does NOT claim

It does not say strings cause the out-of-memory — it says strings are what the
UNWIND BOUND truncated, which the tool states, and that the two facts are
different. It does not say the duplicate solve is the whole of st1inch's zero:
`bounded-holds 2` are decided-negative, so even with all three unknowns resolved
this unit reaches at most 3 of 5. And it is ONE unit.
