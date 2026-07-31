# A dying path-coverage run used to keep nothing. Step 1: the payload.

`scope-and-resources.md` §2 measured the loss and named the change sites. This
is the record of what was actually built, in the order the loss requires. One
section per step; each step is its own commit with its own must-flip pair.

**Bookkeeping note on step 1's commit boundary.** The code below landed in
`d75df81ba3`, whose message is about `forge_roundtrip.py`. Two of us share this
worktree; the other agent ran `git add <one script>` followed by a bare
`git commit`, and `git commit` writes the WHOLE INDEX, so this work went in
under their message. History is deliberately NOT rewritten for it — the tree is
local and un-pushed, and a wrong message on one commit is a bookkeeping problem
while a rewrite under a shared worktree is a real one. This file is the honest
record instead. Both of us now use `git commit -F <msg> -- <paths>`, which
commits only the named paths.

---

## Step 1 — persist the counterexample payload when the claim is decided

### The measurement this exists for

The aqua whole-contract run at `--memlimit 8g` died **51.5 % through the solve**
having **DECIDED 938 claims and REFUTED 5** of that contract's 15 complete paths
— and produced no artefact at all. All five are in the 20 g run's F set, so all
five were genuine: `rawBalances:path:2`, `rawBalances:path:7`,
`safeBalances:path:2`, `safeBalances:path:14`, `ship:path:1756`.

The loss is structural, not bad luck:

| fact | site |
|---|---|
| `cov-report.json` is written exactly once | `bmc.cpp:1976`, gated `:1336` |
| from `report_coverage`, which sits AFTER the job loop | `bmc.cpp:3661-3670` |
| inside `run_thread`'s try | `bmc.cpp:2405` |
| the only verification-phase catch | `bmc.cpp:2559-2563` |

So a `bad_alloc` in any job unwinds **past** the report into the catch. A caught
OOM costs the entire report, not part of it.

### Why the payload had to come first

A mid-solve persistence mechanism already existed — the covered-set writer at
`bmc.cpp:3552-3553`, firing per witnessed claim — and it persisted **only stable
path ids**. Enabling it before persisting the payload would have converted a
witness lost to an OOM into a permanently payload-less `F`: the next round sees
the path via `path_witnessed_earlier`, does **not** re-instrument it, and reports
it under `ce_extraction.payload_absent_reason` forever. The round that could
still have produced the inputs is the round that skips the path. That is a
regression dressed as a fix, and it is the whole reason the ordering is fixed.

### What was built

**1. The covered set carries the payload** (`write_path_covered_set_atomic`).
On-disk `version` 2 → 3, and a version ≤ 2 file is now **refused on load**, so an
old payload-free file is rejected rather than silently read as "these paths have
no inputs". The fingerprint check stays first, so
`solidity_path_cov_covered_set_fail_closed`'s message is unchanged.
`bmc.cpp:1793`'s `payload_absent_reason` becomes a lookup: when the covered set
carries the payload it is emitted, labelled with its provenance (it was harvested
under a *different* run's bound and slicing configuration, and a consumer that
read it as this run's output would attribute this run's `bound` block to it).

**2. A CE journal, `cov-ce-journal.json`, with no opt-in flag.** The covered set
is only written under `--coverage-covered-set`, and `pathcov_collect.py` has
never passed it — the run that lost five witnesses was not passing one. The
journal is written whenever the run asked for the payload at all
(`--cov-report-json`), refreshed by an atomic `.tmp`+rename at the moment a path
is witnessed, and **never read back in**, so it cannot accumulate across runs or
change what a re-run does. `complete` is `false` on every incremental write and
`true` only on the one written beside the final report.

Both censuses are **read back out of the published file**, not taken from the
in-memory maps that produced them. A count of what the writer believes it wrote
would have printed correct-looking numbers throughout the period in which
nothing called `write_path_covered_set_atomic` at all.

### Testability is a shipped feature here

These paths only run on a run that does **not** reach a clean exit, and a
`test.desc` is one invocation with no environment of its own —
`testing_tool.py` additionally **strips** `--timeout` and `--memlimit`
(`UNSUPPORTED_OPTIONS`). There is no way to produce a dying run from a
regression except to ask the tool for one, so `--path-cov-fault-after N` is a
real option under DEBUG rather than a throwaway build. Its sibling
`--path-cov-fault-sigterm N` landed in the same commit and is **not** exercised
by step 1; it exists for step 2's signal arm.

### Must-flip pair

| direction | test | what it pins |
|---|---|---|
| fires | `solidity_path_cov_ce_journal_survives_death` | with `--cov-report-json` and `--path-cov-fault-after 1`: `CE journal cov-ce-journal.json updated after claim 1 of 4: 1 witnessed path(s) on disk, 1 with non-empty inputs (complete=false)`, and `Coverage report written to cov-report.json` never appears |
| stays dark | `solidity_path_cov_ce_journal_absent_without_report` | same contract, same four witnessed paths (`Path Status: F 4, I 0, U 0`), no `--cov-report-json`, and no journal line anywhere |

The pair establishes the right distinction: the journal follows the **request for
a payload**, not the presence of a witness. The claim index in the line
(`after claim 1 of 4`) is load-bearing — without it the message is compatible
with the old end-of-run write and the test would pass on a build that had
changed nothing about *when* the payload lands.

### Red before

Against the pre-change binary, snapshotted out of `build/` before the rebuild:

* the positive test fails with `ERROR: unrecognised option '--path-cov-fault-after'`;
* artefact level, same contract, same clean run with `--cov-report-json`: the old
  binary leaves `cov-report.json` and nothing else; the new one leaves
  `cov-ce-journal.json` as well, and `cov-report.json` is the same 14053 bytes —
  so the report a *surviving* run produces is untouched.

The journal's contents on that run are the deliverable, not a marker:
`inputs: a = 101`, `entry_storage: x = 0`, `final_state: x = 1`,
`path_id_stable: e3d346f76399dc6e`, `scoped_to_claim: true`.

### ctest

`solidity_path_cov` 78/78 (76 baseline + 2 new), `foundry_covgen` 41/41.
Re-verified against the current HEAD build after the commit boundary moved.

### Known cost, stated rather than discovered

Each publish serialises the whole witnessed set, i.e. quadratic in |F|. |F| is
single digits per unit on every contract measured so far, and the same shape was
already accepted for the covered-set writer. A contract with thousands of
witnessed paths would need an append-only journal instead.
