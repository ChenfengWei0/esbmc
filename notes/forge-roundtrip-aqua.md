# What the GENERATED suite actually covers, measured by forge (aqua)

Run it with `python3 notes/coverage/scripts/forge_roundtrip.py aqua_Aqua`.
Nothing here needs the project's repository: the contract sources are inside the
flat, and the project's own coverage is already recorded in the locked JSON.

## The number

| aqua_Aqua, 8 canonical decisions | reached |
|---|---|
| bar — ESBMC branch coverage (`esbmcReached`) | **7** |
| native — the project's own suite, forge lcov | **6** |
| **ours — the GENERATED suite, forge lcov** | **2** |
| (the gate's proxy — decisions our witnessed paths walked) | 4 |

**The proxy overstates us by a factor of two.** `branch_gate.py` measures which
canonical decisions the VERIFIER's exploration touched; forge measures what the
emitted tests execute. Those are different questions and on aqua they differ by
4 vs 2. Every number this project has reported about its own reach so far is the
first kind.

## Why 2, and it is not a coverage problem

Six test files were emitted from eight runs, none killed, none refused for an
ambiguous name. `forge test`: **6 passed, 1 failed**. Three distinct defects,
all in stage 4:

### 1. Two of the six tests have an EMPTY BODY, and they PASS

```solidity
// claim: sol:@C@Aqua@F@dock#3088:path:12, sol:@C@Aqua@F@dock#3088:path:2
function test_cov_0() public {
}
```

`ship` is identical. Both report `[PASS] test_cov_0() (gas: 188)` — they pass
because they do nothing. The comment names two witnessed paths and the body
executes neither. A green test that calls nothing is worse than a missing one:
it is counted as emitted, it is counted as passing, and the only thing that
distinguishes it from a real test is reading the body.

Nothing in the emitter refuses this. The `cases` counter counts it, the file
count counts it, and `forge test` reports it green.

### 2. A test the emitter ASSERTED exits normally REVERTS

```solidity
// [asserted] path exits normally; a revert fails the test
c0.pull(address(0), bytes32(0x00..00), address(0), 0, address(0));
```

`[FAIL: SafeTransferFromFailed()]`. The emitter chose the asserted form
precisely because it believed the path does not revert, and it is wrong. This is
a RED test on the UNMODIFIED contract, which is the one outcome the method must
never produce — it is the anti-goal the revert-fidelity work
(`notes/coverage-comparison/_foundry_roundtrip/RESULTS.md`, Phase 2) was for,
and the try/catch fallback that work shipped does not cover this case.

### 3. Every recovered argument is zero, and zeros alias

```
c0.rawBalances (address(0), address(0), bytes32(0), address(0))
c0.safeBalances(address(0), address(0), bytes32(0), address(0), address(0))
c0.push       (address(0), address(0), bytes32(0), address(0), 0)
```

One value in the whole suite is non-zero (`pull`'s `2^248`). Aqua's storage is a
four-level mapping keyed on those addresses, so a call with four zero keys
indexes one slot and trips the first `require`. The emitter audit predicted this
exact failure from the code (`notes/emitter-ce-value-loss-audit.md`, and the
standing note at `foundry.cpp:3066-3113` which already records "`ship` took four
zero addresses, which alias to one storage slot, so the emitted call reverts on
a path the census called normal"). This is that prediction confirmed end to end,
with a coverage number attached.

## Status of the three, after the first two were addressed

| defect | state | effect on the number |
|---|---|---|
| 1. empty test body | **FIXED** in the emitter (`collect()` refuses a case whose every call is a constructor, and counts it) | emitted files 6 -> 4, coverage UNCHANGED at 2/8 -- which is the proof that those two tests contributed nothing |
| 2. asserted-normal test that reverts | **GUARDED** at the pipeline level, not the emitter | 1 RED test found and disabled; coverage UNCHANGED at 2/8 |
| 3. every recovered argument is zero | **OPEN** | this is what the remaining 6 of 8 rests on |

Defect 2 could not be fixed in the emitter and the reason is worth stating: the
exit census is not wrong about the MODEL. The model gives an external call a
nondet return and may choose success where the chain fails -- `pull`'s case
calls `safeTransferFrom` on `address(0)`. No amount of reading the census closes
that, so the check has to be empirical: `forge_roundtrip.py` now runs every
emitted test on the unmodified contract, DISABLES the red ones (renamed out of
forge's `test*` prefix, so the artefact still shows what was generated and why
it was not counted), counts them, and measures coverage over the suite that
actually passes.

Both changes moved the coverage number by ZERO, and that is the useful part: the
two empty tests and the one red test were contributing nothing, so removing them
costs nothing and makes the 2/8 an honest 2/8 rather than a 2/8 propped up by
three artefacts.

## What this changes

The bottleneck for the paper's claim is **stage 4, not stage 1**. Path
enumeration witnessed 13 F claims across 7 units on aqua; the suite built from
them executes two decisions. Closing the gate on the proxy metric would not move
this number at all.

Concretely, in the order their cost is now measured:

1. **Refuse an empty test body.** A case whose reconstruction produced no call
   must not be emitted as a passing test. Counted on stdout, in the shape the
   named-obstacle refusal already uses (mark → exclude → count).
2. **Do not emit the asserted form unless the path's exit kind says so.** The
   exit census already classifies revert / rollback / undetermined exits per
   path; `pull`'s case shows the emitter is not consulting it.
3. **Stop defaulting recovered values to zero** — or, where a value genuinely
   was not recovered, refuse the case rather than emit a call that exercises a
   different input. `foundry.cpp:1351-1352` substitutes the type default and the
   `defaulted` flag it sets is read in exactly one place, which is not on any of
   these routes.

## Provenance

`notes/coverage/scripts/forge_roundtrip.py`, aqua_Aqua, ESBMC 8.2.0, forge
1.7.1, solc 0.8.30, `--solidity-path-coverage --solidity-max-tx 1
--generate-foundry-testcase` per unit, 180s per run. The lcov reach is computed
with the SAME two operations `collect.py` applies to both of its columns —
`{BRDA lines with a non-zero arm} ∩ {canonical decision lines}`, capped per file
— so the three columns above are commensurable by construction.
