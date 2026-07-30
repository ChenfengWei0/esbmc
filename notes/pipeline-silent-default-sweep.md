I have read all thirteen files in full, plus `collect.py` (which all of them depend on) and the on-disk artifacts. Report follows.

---

# READ-ONLY DEFECT SWEEP — `notes/` measurement pipeline

**Nothing was built, run, or modified.** All conclusions are from reading source and inspecting existing artifacts.

## Repository state I found (this drives the ranking)

Three facts change how urgent several findings are:

1. **`notes/coverage/data/` is currently half re-collected.** `esbmc_aqua_Aqua.json` (19:25), `esbmc_cross_chain_swap_EscrowDst.json` (19:27), `esbmc_cross_chain_swap_EscrowSrc.json` (19:28) are today's binary; `farming`, `limit_order_protocol`, `st1inch_St1inch` are still the locked 2026-05-20 files (Jul 2 mtime). `git status` confirms exactly three modified.
2. **No `lcov.info` exists for any of the five projects**, and four of five source roots in `PROJECT_SRC` are gone (only `cross-chain-swap/src/contracts` survives). So `native_carried = True` on every benchmark right now, and `collect.py native <project>` would `sys.exit`.
3. **Every `notes/coverage/pathcov/<bench>/index.json` and `runs.jsonl` has been quarantined by rename to `*.prefix-buggy-frontend.*` — but `reports/` was NOT quarantined.** All six `reports/` directories still hold the buggy-frontend `cov-report.json` files (aqua: 8 files, farming: 16 files, ~54 MB). `st1inch_St1inch/reports/` is empty.

---

# RANKED FINDINGS

## 1. `t2_runnability.py:272` — the per-unit timeout is the *slice remainder*, and a slice artifact is written as a measured `TIMEOUT`. **ALREADY MATERIALISED.**

```python
cap = int(min(UNIT_CAP, left - 15))     # line 272; left = deadline - time.time()
```

**Trigger state:** a unit reached near the end of a foreground slice. The only floor is `if left < 30` (line 268), so `cap` can be as low as **15 s** for a unit whose honest budget is 540 s.

**It has already happened.** `notes/runnability-distribution.md:206`:

```
| `farming` | `FarmingPool` | `rescueFunds` | - | - | - | - | 85.3 | 84 | TIMEOUT | 9536 |
```

The unit ran 85.3 s against a cap of 84 s. The rows immediately after it jump back to `cap=539`, i.e. a new slice began. This unit plausibly finishes in ~86 s and is filed permanently as "did not finish".

**Why it is permanent and why it spreads:**
- `already_done()` (lines 214–218) keys on `(bench, contract, function)`, so re-running **never retries** it at full cap.
- `already_done()` line 219 counts it toward `touts` with `if "TIMEOUT" in line` — a 15 s slice-artifact timeout is indistinguishable from a 540 s genuine one. Two of them fire `if touts >= TIMEOUTS_PER_PROJECT: break` (line 264) and **silently truncate the rest of the project**. This mechanism has already truncated `cross_chain_swap_EscrowDst`: its two 540 s timeouts ended the project, and six of its 18 collector entries (`ImmutablesLib.protocolFeeRecipient`, `protocolFeeRecipientCd`, `ProxyHashLib.computeProxyBytecodeHash`, `TimelocksLib.get/rescueStart/setDeployedAt`) have no row at all, while `EscrowSrc` — same library code — has all of them. That truncation is by design; a truncation triggered by two *short-cap* rows would not be.
- `runnability_mkj.py:30` folds it into J with the same substring test, so the plan-required triple "M units, K measured, J timed out" reports it as a budget result.

**Both halves present.** (1) silent substitution of the cap; (2) the row's `completed` cell reads `TIMEOUT`, identical to a real one — the `cap(s)` column carries the evidence but nothing consumes it.

**One-line fix:** refuse to start a unit whose available cap is below `UNIT_CAP` — `if left - 15 < UNIT_CAP: print("[slice] insufficient budget for a full-cap unit; stopping"); return 0` — and, in `already_done()`, only count a `TIMEOUT` row toward `touts` when its `cap(s)` cell equals `UNIT_CAP`.

---

## 2. `pathcov_collect.py:191–192` + `branch_gate.py:265–266` — `reports/` is never cleaned, and the gate's numerator globs it. **Loaded and armed right now.**

```python
reports_dir.mkdir(exist_ok=True)        # pathcov_collect.py:192 -- never emptied
for stale in workdir.glob("*"):         # :113-114 -- only `work/` is cleaned
    stale.unlink()
```
```python
rdir = Path(meta.get("reportsDir", PATHCOV / bench / "reports"))   # branch_gate.py:265
return meta, sorted(rdir.glob("*.json"))                           # :266
```

**Trigger state — this is the current disk state.** `index.json` and `runs.jsonl` were quarantined (renamed to `*.prefix-buggy-frontend.*`) but the ~54 MB of buggy-frontend `reports/*.json` were left in place. The next `pathcov_collect.py <bench>` sees no journal, re-runs from scratch, and writes into that same directory. If it is interrupted (which the design explicitly anticipates — the whole resumable-journal comment at lines 194–199 exists because a sweep outlives a supervising call), `index.json` records only the runs that finished, while `branch_gate.py` sums the numerator over **every** `*.json` in `reports/`, including the quarantined ones.

**What you would see:**
```
| `aqua_Aqua` | 8 | 7 | 7 | 6 | 7 | PASS |
```

**Why it is indistinguishable from a correct run:** `pathcov_reached_flat_lines` computes `stats["reports"]` (line 202) and `stats["missing_reports"]` (line 200) — and `main()` prints **neither**. The "What the product side actually saw" table (lines 346–350) prints `len(meta['runs'])`, `noreport`, `killed`, `f_claims`, `f_without_sequence`, `decision_steps`, `unrecorded_steps`, `synthetic_dropped`. The one number that would expose the mismatch — how many report files the numerator was actually read from — is calculated and discarded. `noreport` counts runs *without* a report; nothing counts reports without a run, so the `(partial)` suffix (lines 334–336) does not fire.

**One-line fix:** in `pathcov_collect.py`, delete `reports_dir` contents whenever the journal is absent; and in `branch_gate.py`, build the report list from `meta["runs"]` tags (`rdir / f"{r['tag']}.json"`) rather than from `glob`, `sys.exit`-ing on any extra file found in `rdir`.

*(Related, lower severity: `reportsDir` is stored as an absolute path baked into the index. If the repo moves, `rdir.glob` returns `[]`, `ours` becomes 0, and the verdict is a clean unqualified `FAIL` — wrong direction but at least not a false PASS.)*

---

## 3. `summarize.py:31` — a missing benchmark silently leaves the aggregate, and the aggregate carries no provenance. **Live in the current half-collected state.**

```python
if not p.exists(): continue     # line 31
```

**Trigger state:** any `esbmc_<bench>.json` absent — e.g. mid-collection, or a bench whose `collect.py` run hard-failed (see #6 on the driver). The bench vanishes from *both* the numerator and the denominator of:

```python
e_pct = round(100*s_esbmc/s_denom, 2) if s_denom else 0     # :56
print(f'  {"AGGREGATE":<32} {s_denom:>14}  {s_esbmc:>4}/{s_denom:<4} ({e_pct:>5.1f}%) ...')
```

**What you would see** — a well-formed headline row with no benchmark count:
```
  AGGREGATE                                    47    35/47  ( 74.5%)    28/47  ( 59.6%)
```
Nothing distinguishes a 6-benchmark aggregate from a 4-benchmark one.

**A second, currently-active half of the same defect:** even with all six files present, `summarize.py` prints them side by side with **no provenance column**. Right now three rows come from today's binary and three from the May 2026 binary. The commit message for `4bd98cd328` establishes that this exact mix is not comparable ("the same inputs and the same commands give a slower, lower answer on today's binary … reach moves 8 → 2 for dock"). The `nativeSource` field, which `collect.py` now writes precisely so a carried-forward native column announces itself, is read by nothing — `summarize.py` never touches it.

**One-line fix:** `if not p.exists(): sys.exit(f"missing {p}: the aggregate is over all {len(BENCHES)} benchmarks or it is not an aggregate")`, and print `d.get("nativeSource")` / the file mtime per row.

---

## 4. `branch_gate.py:294, 330` — a baseline of `esbmcReached: 0` makes the gate pass unconditionally.

```python
bar = p2.get("esbmc")                                            # :294
...
verdict = "PASS" if (bar is not None and ours >= bar) else "FAIL" # :330
```

**Trigger state:** the baseline JSON's `per_function.total.esbmcReached` is `0`. That is **exactly the artifact the collect.py bug produced** (`esbmcReached 0, esbmcCoveragePct 0.0`), and the denominator is unaffected by that bug (`branchesTotal` comes from the AST walk, which never touched the deleted tree), so the row looks fully populated:

```
| `aqua_Aqua` | 8 | 0 | 0 | 6 | 0 | PASS |
```

`bar is not None` guards only the *absent* case; a zero passes the guard and then `ours >= 0` is a tautology. The `(partial)` suffix does not fire because the defect is on the baseline side, which `killed`/`noreport`/`f_without_sequence` do not observe. **This is the completed loop of the bug you just fixed**: `collect.py` could write a 0, and `branch_gate.py` would read that 0 as a bar cleared.

Same line family: `denom = p2.get("denom") or p1.get("denom")` (line 293) — `or` on a denominator, so a P2 denominator of 0 silently displays P1's.

**One-line fix:** `if not bar: sys.exit(f"{b}: baseline esbmcReached is {bar!r} -- a zero or absent bar is not a gate")`, and use `p2.get("denom") if p2.get("denom") is not None else p1.get("denom")`.

---

## 5. `branch_gate.py:310–327` — `N/A: 0 units` asserts a cause it never checked, and it can absorb a total collection failure.

```python
killed   = sum(1 for r in meta["runs"] if r.get("killedByOuterTimeout"))   # :308
noreport = sum(1 for r in meta["runs"] if not r.get("reportPresent"))      # :309
units    = sum(r.get("unitsEnumerated", 0) for r in meta["runs"])          # :310
if units == 0:
    verdict = "N/A: 0 units (in-scope code is internal-only)"              # :327
    ours = "-"
```

`unitsEnumerated` is set only when this regex matches the run's stdout (`pathcov_collect.py:157`):

```python
re.search(r"instrumented (\d+) complete path\(s\) across (\d+) unit\(s\)", out)
```

`.get(..., 0)` therefore substitutes 0 for *"the summary line was not found"*, and the `units == 0` branch then **states a specific cause that was never observed**. `killed` and `noreport` are computed two lines above and **not consulted in this branch at all**.

**Three input states that produce it, only one of which is honest:**
- (honest) a pure-internal library — `limit_order_protocol`, correctly.
- every run crashes or is killed *before* the instrumentation line is emitted → `units == 0` → the benchmark silently leaves the gate with a methodological-sounding exemption.
- an `index.json` with `runs: []`.

And a fourth that would take out all six at once: any change to that English summary sentence in a future ESBMC build. Every benchmark would print

```
| `st1inch_St1inch` | 21 | 12 | 14 | 16 | - | N/A: 0 units (in-scope code is internal-only) |
```

which is byte-identical to the legitimate `limit_order_protocol` row.

**Near-miss evidence that this class is real:** `st1inch_St1inch/index.prefix-buggy-frontend.json` records **22 runs, every one `"exitCode": -6` (SIGABRT), every one `"reportPresent": false`**. That bench escapes this trap only because ESBMC happened to print the instrumentation line before aborting (`unitsEnumerated: 39` on all 22). Had it aborted earlier, 22 crashed runs would have been reported as a scope exemption.

**Answering your question 5 directly:** yes to both. `PASS` where the honest answer is `FAIL` comes from #4 (bar 0) or #2 (stale reports inflating `ours`). `N/A: 0 units` where the honest answer is `FAIL` comes from any run set that produced no summary line.

**One-line fix:** `if units == 0 and (killed or noreport or not meta["runs"]): verdict = f"NO MEASUREMENT: {killed} killed, {noreport} without a report"` — and derive the internal-only claim from `nonUnitFunctionsPresent`, which `pathcov_collect.py:162` already records and nothing reads.

---

## 6. `collect_all.sh:33–36` and `pathcov_all.sh:14–19` — the drivers swallow the new hard-fails and still announce completion.

```sh
set -u                                    # collect_all.sh:10  -- no `set -e`
for b in "${ESBMC_BENCHES[@]}"; do
  python3 "$PY" esbmc "$b"                # :35  -- exit status ignored
done
```
```sh
set -u                                    # pathcov_all.sh:11
for b in ...; do python3 "$S" "$b" --timeout "$T"; done
echo "=== ALL DONE ==="                   # :19  -- unconditional
```

**This directly weakens fix #1 of `4bd98cd328`.** `own_contract_names()` now `sys.exit`s when the pinned entry is missing — but under `collect_all.sh` that bench is simply skipped, the **previous (locked or stale) `esbmc_<bench>.json` stays on disk untouched**, the loop continues, and the driver ends with `printf '\a'`. A subsequent `summarize.py` then reports six rows, one of which was not re-collected, with nothing anywhere saying so. Half (1) is fixed in the collector; half (2) is re-introduced by the driver.

`pathcov_all.sh` is worse in one respect: `=== ALL DONE ===` prints even if every benchmark `sys.exit`ed on `missing AST` (`pathcov_collect.py:187`).

**One-line fix:** add `set -e` (or `python3 "$PY" esbmc "$b" || exit 1`) to both, and make `ALL DONE` conditional.

---

## 7. `focus_enumeration_gate_check.py:32–38` — an empty exclude list is used as "no restriction" when it means "maximal scope". *This is the collect.py bug, verbatim, in a second script.*

```python
ex = []                                                  # :32
for fn in rep["per_function"]["functions"]:
    if fn["function"] == "claim" and fn["contract"] == "FarmingPool":
        toks = fn["commandUsed"].split()
        ex = [toks[i + 1] for i, t in enumerate(toks) if t == "--coverage-exclude-contract"]
print(f"[excludes] {len(ex)} (taken from FarmingPool.claim's recorded command)")   # :38
```

**Trigger state:** `esbmc_farming.json` contains no `per_function.functions` entry with `contract == "FarmingPool"` and `function == "claim"`. **`farming` is one of the five benchmarks still being re-collected right now.** Any rename, any attribution change, or a re-collection whose Pair-2 enumeration differs, and `ex` stays `[]`.

Consequence: every subsequent `--focus-function` run is launched with **zero** `--coverage-exclude-contract` flags — the whole flat including the entire OpenZeppelin tree is in scope. The instrumented counts this script exists to compare become numbers from a different configuration.

**What you would see:**
```
[excludes] 0 (taken from FarmingPool.claim's recorded command)
--- claim
    instrumented = 41182 across 3 unit(s)
```
The `0` is printed but framed as informational, and the script's entire premise (`notes/runnability-distribution.md:14–16`: *"same 22 `--coverage-exclude-contract` flags … verified byte-identical"*) is silently void. The measurement that this script produced is the one that invalidated the T2 distribution table — so a wrong `ex` here propagates to a published invalidation claim.

**One-line fix:** `if not ex: sys.exit("no exclude list recovered for FarmingPool.claim -- an empty list is the WIDEST scope, not the recorded one")`.

**Also in this file — `notes/focus_enumeration_gate_check.py:23`, environment read:**
```python
UNWIND = os.environ.get("GATE_UNWIND", "")
```
An unset variable changes the measurement (`--unwind` is appended at line 58) and **the output never records which configuration produced it**. The two runs whose 4→6 comparison table sits at `runnability-distribution.md:40–44` are distinguishable only by whoever remembered to set the variable. Fix: print `UNWIND or "(unset; no --unwind)"` in the per-target header.

---

## 8. `collect.py:452–453` — `rawBranches` / `rawReached` default to 0 when the log lines are not found, and `status` stays `"ok"`.

```python
bs = re.findall(r"^Branches\s*:\s*(\d+)\s*$", out, re.M)
rs = re.findall(r"^Reached\s*:\s*(\d+)\s*$", out, re.M)
b = int(bs[-1]) if bs else 0        # :452
r = int(rs[-1]) if rs else 0        # :453
...
elif rc != 0 and not bs: status = f"error rc={rc}"     # :457
```

A run that exits 0 and prints no `Branches:` line is recorded as `"status": "ok", "rawBranches": 0, "rawReached": 0` — "the run measured zero branches" and "we could not parse the run" collapse into the same record.

**There is a live instance in the working tree.** `git diff notes/coverage/data/esbmc_aqua_Aqua.json` shows `BalanceLib.load` and `BalanceLib.store` moving from locked `rawBranches: 0, rawReached: 0` to today's `rawBranches: 2, rawReached: 1` on unchanged commands. That is either a genuine two-month binary delta or the parse failing in May — and the JSON cannot tell you which, because both render as `0`.

**One-line fix:** `"branchesParsed": bool(bs)` in the record, and `status = "unparsed"` when `not bs`.

---

## 9. `collect.py:390` (with `:274`) — `" ".join()` on a string: `no_function.commandUsed` is character-spaced and unusable as provenance.

```python
cmd = all_cmds[0]                 # :274 -- a STRING
...
"commandUsed": " ".join(cmd),     # :390 -- joins its CHARACTERS
```

Verified in all three re-collected files and in the locked `HEAD` version — **pre-existing, not introduced by `4bd98cd328`**:

```json
"commandUsed": "/ h o m e / s a m s o n / w o r k s p a c e / e s b m c / b u i l d ... - - c o v e r a g e - e x c l u d e - c o n t r a c t   B a l a n c e L i b ..."
```

**Why this belongs in this sweep:** the recovery procedure documented in `own_contracts.json` is *"(top-level contracts declared in the flat) minus (every `--coverage-exclude-contract` in that file's `per_function` commandUsed strings)"*. It uses `per_function` — which is correctly joined from a list. Anyone who instead reaches for `no_function.commandUsed` (the natural choice for the Pair-1 scope, and the field the JSON schema advertises) will find **zero** `--coverage-exclude-contract` tokens, because every token is a single character. `own = all contracts` — the maximal scope, silently. That is the same defect with the sign flipped, one plausible keystroke away, and the field also destroys reproducibility of the Pair-1 command.

Also at line 274 the field only ever holds `all_cmds[0]`; the per-method Pair-1 commands are executed and never recorded anywhere.

**One-line fix:** `"commandUsed": all_cmds[0] if isinstance(cmd, str) else " ".join(cmd), "allCommandsUsed": all_cmds`.

---

## 10. `collect.py:335–338` — the native carry-forward will reproduce a previously-corrupted zero.

```python
if not prev_native:
    sys.exit(f"... refusing to report native reach as 0")     # :303-306
...
elif native_carried and marker in prev_native:
    native_reach = prev_native[marker].get("reached", 0)      # :336
```

The guard fires only when *no* previous file carries a `reached` key at all. A previous run corrupted by the original bug wrote `"native": {"reached": 0, ...}` — present, so `prev_native` is non-empty, so the guard passes, and the zero is carried forward into a run whose `nativeSource` string now **certifies** it as *"reproduced rather than recomputed as 0"*. The provenance label makes a corrupted zero look audited.

This is not hypothetical for the current state: `native_carried` is `True` for all six benchmarks, since no `lcov.info` exists anywhere.

**One-line fix:** carry forward only from a blob whose `nativeSource` starts with `"lcov: "` (i.e. was itself measured), and `sys.exit` otherwise.

---

## 11. `certify_gate_audit.py:120` — `REFUSE` is the default verdict, so "the tool said nothing" scores as a pass on 7 of 16 cases.

```python
got = "REFUSE"                                     # :120 -- the DEFAULT
for ln in (p.stdout + p.stderr).splitlines():
    if t == "VERIFICATION SUCCESSFUL": got = "SUCCESSFUL"
    elif t == "VERIFICATION FAILED":   got = "FAILED"
```

The file's own contract (docstring lines 19–20) defines the third state as *"a non-zero exit with a named reason, no verdict line"*. The check asserts **neither** half: `p.returncode` is printed at line 130 and never compared, and no reason string is ever looked for. Seven of the sixteen cases expect `REFUSE`.

**Trigger state:** any run that produces no verdict line for a reason unrelated to the gate — a memlimit hit, a crash before the verdict, or `--path-cov-certify` becoming a no-op. Those seven rows go green.

This inverts the very defect the file was written to catch. `ASSUMPTION_CASES` line 86 documents *"no assume and no assert are emitted at all and the run printed SUCCESSFUL with exit 0 — a certificate for a query never asked."* The mirror — printed **nothing** with exit 0 — is currently scored as correct behaviour.

**One-line fix:** `if got == "REFUSE" and p.returncode == 0: got = "SILENT-EXIT-0"` so it can never satisfy an expected `REFUSE`.

---

## 12. `t2_runnability.py:186, 193–194` — `completed = yes` is reachable with no report at all. *Latent; the previously-fixed half of this same bug is documented in the file.*

```python
r["unit"] = unit_rows(os.path.join(cwd, "cov-report.json"), u["function"])   # :186
r["completed"] = ((not timed_out) and (r["contract_wide"] is not None)
                  and not r["tool_failure"])                                 # :193-194
```

`unit_rows` returns `None` when the report file is missing (line 133) — and `r["unit"]` is **not part of the `completed` test**. So a run that finishes, prints the instrumentation line, does not trip `INTERNAL DEFECT`, and writes no `cov-report.json` is recorded as:

```
| `farming` | `FarmingPool` | `foo` | - | - | - | - | 3.2 | 540 | yes | 0 |
```

Dashes plus `yes` — which is precisely the shape the docstring at lines 109–120 says was the bug: *"Twenty-two rows of dashes were therefore recorded as successful measurements."* That fix added the `INTERNAL DEFECT` guard; it did not close the structural hole, which is still open for any *other* reason a report goes missing.

I checked all 104 rows of `runnability-distribution.md`: **no row currently has `-` with `yes`**, so this has not yet fired. `runnability_mkj.py` would count such a row in K (measured), not in J, T, or N.

**One-line fix:** add `and r["unit"] is not None` to line 193.

---

## 13. `runnability_mkj.py:30, 35, 37, 45` — the K/J/T/N counts are substring matches against a hand-maintained markdown table.

```python
if "TIMEOUT" in line:          d["J"] += 1     # :30
if "TOOL-FAILURE" in line:     d["T"] += 1     # :35
elif "| no |" in line:         d["N"] += 1     # :37
r = rows.get(b, {"K": 0, "J": 0, "T": 0, "N": 0})   # :45
```

- Case-sensitive, whitespace-exact. A row rendered `| No |` or `|no|` drops out of N; a differently-cased timeout drops out of J. The bench still reports a K, so the row is counted as measured and its outcome is lost, with no line saying so.
- `rows.get(b, {...})` at least prints `(not started)` when K is 0 — that half is honest.
- **M is not cross-checked against K.** `m = len(rep["per_function"]["functions"])` comes from the collector JSON being re-collected right now; nothing verifies that the K rows are even a subset of those M entries. If the re-collection changes farming's function list, `M=` and `K=` are computed against different unit sets and printed side by side as a triple.
- Duplicate rows are not detected. If `already_done()`'s parse in `t2_runnability.py` ever breaks (an unescaped `|`, a header change), units are re-run and **appended a second time**; `mkj` would then report `K > M` without comment.

**One-line fix:** parse the `completed` cell by column index rather than substring, and `sys.exit` if any K row's `(contract, function)` is absent from the M list or appears twice.

---

## 14. `coordinate_settability_census.py:59, 74, 76` — the census scope is a directory scan, and an invisible bucket sits in the denominator.

```python
asts = sorted(f for f in os.listdir(INPUTS) if f.endswith(".solast"))   # :59
if not asts: print("no .solast under", INPUTS); return 1                # :60-61
```

This is the same *shape* as the original bug — the scope of the measurement is discovered by scanning a directory — with two mitigations: the directory is inside the repo, the files are git-tracked, and the fully-empty case exits 1. But **partial** absence is silent: it has no `BENCHES` list (every other script in this sweep has one), so if one `.solast` is missing, the `**total**` row's `settable %` is computed over five corpora and looks identical to a six-corpus figure. `collect.py:192–195` regenerates `.solast` files on an mtime comparison, so their presence is a side effect of another script's run order.

Second, smaller defect at lines 74/76/82:
```python
c[mu if mu in c else "other"] += 1     # :74
n = sum(c.values())                    # :75 -- includes "other"
pct = f"{c['mutable'] * 100.0 / n:.0f}%" if n else "n/a"
```
`"other"` (a `VariableDeclaration` whose `mutability` solc did not state) is counted in `n` but has **no printed column**. A reader summing the three visible columns gets a different total from the printed `state vars` column, and the `settable %` denominator silently includes a bucket nobody can see.

**One-line fix:** replace `os.listdir` with the six-name `BENCHES` list and `sys.exit` on any missing `.solast`; print `other` as a column.

---

## 15. `t2_runnability.py:54–55` — the evidence logs for every recorded row are written into a foreign, session-scoped scratchpad.

```python
LOGDIR = ("/tmp/claude-1000/-home-samson-workspace-paper-review/"
          "e0047351-2714-4000-919d-058ca8af97c5/scratchpad/t2logs")
```

An absolute path outside this repository, into a **different project's** session-specific directory. `os.makedirs(..., exist_ok=True)` (line 251) means it never fails — it just recreates a directory belonging to a dead session. Every `TIMEOUT` and `TOOL-FAILURE` row in `runnability-distribution.md` cites evidence that lives there. This is half (1) only: the run still happens, but the audit trail behind 104 published rows is unrecoverable and nothing says so.

**One-line fix:** `LOGDIR = os.path.join(os.path.dirname(OUT), "t2logs")`.

---

## 16. `t2_runnability.py:233–236` — the `HEADER` constant still asserts a claim the file it writes has already refuted.

```python
"`ctr` is the contract-wide instrumented count and is CONTEXT ONLY -- it is "
"identical for every unit of a contract\n"
"because `--focus-function` does not change enumeration (T2.0), so it "
"carries no distribution information.\n\n"
```

`runnability-distribution.md:13` states in bold that **the gate fails on real input**, and line 203 of the same file shows `FarmingPool.exit` with `ctr = 1004` against every other FarmingPool row's `9536`. The header is written only when `OUT` does not exist (line 250), so today's file carries a hand-corrected preamble that the script would **silently overwrite with the refuted version** if the table were ever regenerated from scratch.

**One-line fix:** replace that sentence with the measured finding, or `assert` that all `ctr` values within a contract agree before printing the claim.

---

## 17. `ast_decisions.py:82–85` — a flat with no `// File` markers yields a denominator of 0, which prints as 0.0% coverage.

```python
if not blocks or blocks[0][0] > 1:
    first = blocks[0][0] if blocks else eof
    blocks.insert(0, [1, first-1, "<preamble>"])     # :84
```

If neither the hardhat nor the forge marker regex matches (a third flattener, or a format change), the whole file becomes one `<preamble>` block. `is_project_own_marker` rejects `<preamble>` explicitly (`collect.py:109`), so `own_markers` is empty, the per-file loop body never executes, and `total_denom == 0`. Then `collect.py:83`:

```python
def pct(n, d): return round(100.0 * n / d, 2) if d else 0.0
```

`"branchesTotal": 0, "esbmcReached": 0, "esbmcCoveragePct": 0.0` — a zero-denominator rendered as zero coverage, exit 0. `branch_gate.py` would then read `bar = 0` and PASS (finding #4). Requires a flattener change, so it is ranked last among the real findings — but it is the same shape and it feeds directly into #4.

**One-line fix:** in `collect_esbmc`, `if not own_markers: sys.exit(f"{flat}: no project-own file markers -- the denominator would be 0, which is not a measurement")`.

---

## 18. `scripts/test_solidity_path_generalise.py:789–790, 825–827` — the suite deliberately *pins* two empty-on-missing-input defaults.

```python
check("missing-ast-yields-no-mutability", state_mutability("/no/such/ast"), {})   # :789
check("none-ast-yields-no-mutability",   state_mutability(None), {})              # :790
check("missing-ast-declares-nothing",    declared_struct_fields("/no/such/ast"), set())  # :827
```

The **polarity is safe**: `unsettable_coords(coords, {})` returns `{}` (pinned at line 783) and `lowering_artifacts(coords, set())` returns `{}` (line 825), so an empty map means *nothing is filtered out* — the opposite of the collect.py bug, and the comments at lines 786–788 and 822–824 say so deliberately. **This is the correct direction and I am recording it as such**, since it is the only place in the sweep where the empty-default was reasoned about.

The residual concern is not soundness but diagnosis: the file's own docstring (lines 747–759) says the entire reason `unsettable_coords` exists is that ranging over immutable coordinates made EscrowSrc's *"0-of-4 certification result … never a search-power problem"*. If the AST goes missing, the filter silently disappears and that misdiagnosis becomes available again — with no test that a missing AST is **reported**.

**UNVERIFIED:** whether the driver announces an empty mutability map. Settling it requires reading `scripts/solidity_path_generalise.py` (2015 lines, outside this sweep) for the call sites of `state_mutability` / `declared_struct_fields`. I did not read it and did not run anything.

Everything else in this 957-line file is clean and is, frankly, the model the rest of the pipeline should follow: `verdict()` has an explicit third `UNKNOWN` state pinned by a must-flip pair (lines 101–119), `round_accounting` refuses to print 0.0 for "no query reported a decision time" (lines 739–744), and `divergence_text` keeps "we looked and they agree" separate from "we could not look" (lines 384–388).

---

# CLEAN / NO FINDING

- **`notes/coverage/scripts/ast_decisions.py`** — clean apart from #17. No external reads, no exception swallowing; `extract_ast_json` raises loudly on a malformed AST (line 27).
- **`notes/emitter_attribution_parse_log.py`** — one narrow issue only, and it is half (2) at most: the `CLAIM` regex (line 15) requires literal `✓`/`✗`. A log in any other format yields an empty `per`, and the script prints its header `verdicts per unit (F = witnessed with a counterexample):` followed by nothing, exit 0 — "no claims in this log" and "I could not parse this log" are the same output. Low stakes (it reads a log the caller names explicitly). Fix: `if not per: sys.exit(f"no claim lines matched in {sys.argv[1]}")`.

---

# PER-FILE ANSWERS TO YOUR FOUR QUESTIONS

| file | Q1 reads outside repo | Q2 silent defaults changing a count/denominator/scope/verdict | Q3 can print & exit 0 without measuring | Q4 empty ≡ wrong constraint |
|---|---|---|---|---|
| `branch_gate.py` | no (all paths under `HERE`) | `:293` `or` on denominator; `:294`+`:330` bar 0 ⇒ PASS; `:308-310` three `.get` defaults; `:89` `if not sec: continue` | yes — #2, #4, #5 | yes: `units==0` ⇒ scope exemption; empty `canon` ⇒ `ours=0` |
| `pathcov_collect.py` | `REPO`, `ESBMC`, `INPUTS`, `OUT` all absolute but in-repo; `cwd` per run | `:265` uses `--contract`-only scoping deliberately; guards `solast` at `:186` | yes — index written even with `runs: []` | `reports/` accretion (#2) |
| `pathcov_all.sh` | no | no `set -e` | `ALL DONE` unconditional (#6) | — |
| `collect_all.sh` | no | no `set -e`, exit status ignored (#6) | yes — bench skipped, stale file kept | — |
| `summarize.py` | no | `:31` silent drop; `:56-57` `if s_denom else 0` | yes (#3) | — |
| `ast_decisions.py` | no | `:91` `return None` (unreachable in practice) | — | `<preamble>` ⇒ empty scope (#17) |
| `certify_gate_audit.py` | `/tmp` via `mkdtemp` (write); `esbmc` binary path | `:120` `got="REFUSE"` default (#11) | yes — 7 rows green on a silent binary | — |
| `focus_enumeration_gate_check.py` | **`GATE_UNWIND` env var** (`:23`) | `:32` `ex=[]` (#7); env default `""` unrecorded | yes — prints a full comparison table under the wrong scope | **yes, the headline case (#7)** |
| `runnability_mkj.py` | no | `:30/:35/:37` substring counts; `:45` `.get` default | yes — all-zero K with `(not started)` label | — |
| `t2_runnability.py` | **writes `LOGDIR` into another project's session scratchpad** (`:54`); `/tmp` via `mkdtemp` | `:272` cap from slice (#1); `:185` `contract_wide=None`; `:193` `completed` ignores `unit` (#12) | yes (#12) | `touts` conflates short-cap and full-cap timeouts |
| `emitter_attribution_parse_log.py` | `sys.argv[1]` | none | yes — empty output, exit 0 | — |
| `coordinate_settability_census.py` | no (in-repo `INPUTS`) | `:59` scope by directory scan; `:33-38` `except: return None` (loudly labelled `UNREADABLE`); `:74` invisible `other` in denominator | partial-scope census, yes (#14) | yes, weakly |
| `test_solidity_path_generalise.py` | no | `:789/:825` empty defaults — **pinned, and in the safe polarity** (#18) | no | pinned as *no constraint*, which is correct here |

---

# ONE STRUCTURAL OBSERVATION

`pathcov_collect.py` gets this right and says so out loud (lines 152–156): *"THREE DIFFERENT ZEROS, kept apart. '0 units enumerated', '0 paths in the units there are', and 'no report at all' are three distinct outcomes that all present as an empty numerator."* It then records `killedByOuterTimeout`, `reportPresent`, `unitsEnumerated`, `nonUnitFunctionsPresent` and `noVerificationTargets` as five separate fields.

Every consumer downstream then collapses them again. `branch_gate.py` reads `unitsEnumerated` with a default of 0 and ignores `nonUnitFunctionsPresent` entirely (#5). `t2_runnability.py` drops the `unit is None` case (#12). `runnability_mkj.py` re-derives the distinction by substring-matching rendered markdown (#13). The producer's discipline does not survive one hop, and findings #1, #5, #12 and #13 are all instances of that single gap.
