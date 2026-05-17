# Project-Run Branch-Coverage Algorithm — finalized & empirically pinned

> Status: **finalized 2026-05-17**, every step backed by measured runs
> (15 cells, 3 test sessions, ESBMC 8.2.0 `build/src/esbmc/esbmc`,
> solc 0.8.30). This is the *external orchestration* algorithm that sits
> on top of the shipped ESBMC primitives (Items 1–5). Item 4's scheduler
> was descoped to "external orchestrator" on 2026-05-17; this document is
> that orchestrator's spec. **No orchestrator code exists yet** — this is
> the pre-implementation spec.

## One paragraph

Given a project (a set of `.sol` targets): **flatten each target to a
single self-contained file first** (mandatory — multi-file breaks
everything, proven below); **classify** every contract in the flat unit
into {has-external-entry, own-abstract-base, third-party-base};
**run** each has-external-entry contract — default per-contract mode if
it has no own abstract base, else `--coverage-whole-unit
--coverage-exclude-contract <each third-party base>`; **order** runs
cheapest-first with ancestors' covering-descendant before heavier
descendants; **accumulate** all runs into one shared
`--coverage-covered-set union.json` (already-covered probes are skipped
and shared bases dedup automatically, crash-safe); **report** the
cumulative `union ÷ static-total` only — per-run % is meaningless.

## Running example

A 4-file project:

```
Ownable.sol   abstract contract Ownable           // 3rd-party (OZ-like), internal/modifier only
Base.sol      abstract contract Base              // project-own abstract base, internal only
Vault.sol     contract Vault is Base, Ownable     // has external functions
Token.sol     contract Token is Ownable           // has external functions
```

---

## Step 0 — Flatten (HARD precondition)

For each target, solc-flatten it + all its imports into one self-contained
`.sol` (no `import`, deduped pragma/license), then generate the `.solast`
from the flat file.

- Example: `Vault.sol` → `Vault.flat.sol` containing `Ownable`+`Base`+`Vault`,
  no imports. Same for `Token.sol` → `Token.flat.sol` (`Ownable`+`Token`).
- Reuse the pilot's `notes/Results/branch_cov/esbmc/inputs/*.flat.sol` /
  `flatten_inputs.sh` machinery — flatten is **load-bearing, not incidental**.

**Why mandatory — measured (session 3, `B.sol` `import "./A.sol"`):**

| Run | Result | Verdict |
|---|---|---|
| `--contract B` default, multi-file | **`No branch detected`** (0 branches — even B's own gone) | broken |
| `--contract B --coverage-whole-unit`, multi-file | Branches:6; A's `aStep` keyed `file B.sol line 5` (it lives in **A.sol:5**) | mis-attributed |
| `--contract B --coverage-exclude-contract A`, multi-file | **`No branch detected`** (excluding base nukes B too) | broken |
| same A reached via `C.sol` | `aStep` keyed `file C.sol line 5` (≠ B's `file B.sol line 5`) | union cannot dedup |
| **after hand-flatten A+B → one file**, `--contract B` default | **Branches:2 Reached:2 100%** (A correctly scoped out) | restored |
| **after flatten**, `--contract B --coverage-whole-unit` | **Branches:4 Reached:4 100%** (`aStep @ flat.sol:5`, `run @ flat.sol:8`) | restored |

Mechanism (observation-level): solc inlines the imported AST into one
unit, but ESBMC maps the imported file's source byte-offsets back into
the `--sol` file → wrong file/line, and the `sol_decl_contract` scope
stamp collapses. Flattening eliminates the second file entirely, so loc
strings and the stamp are correct again.

## Step 1 — Classify every contract in the flat unit

| Test | Class | Example | Handling |
|---|---|---|---|
| ≥1 public/external function | **has-external-entry** | `Vault`, `Token` | run it as an entry (Step 2) |
| abstract + only `internal` | **own-abstract-base** | `Base` | never run standalone; covered via a descendant's whole-unit run |
| 3rd-party (by import path / name) | **third-party-base** | `Ownable` | always in `--coverage-exclude-contract` of every whole-unit run |

The classifier is **"has an externally-callable entry", NOT "abstract vs
concrete"** — measured:

- abstract base, only `internal` → `--contract A` ⇒ `Generated 0 VCC(s)`,
  Branches:2 **Reached:0**, 0% (harness has nothing callable; uncoverable).
- abstract base, has a `public` fn → `--contract APub` ⇒
  Branches:2 **Reached:2 100%** (fully coverable standalone).

## Step 2 — Pick the run mode per has-external-entry contract

- **No own abstract base** → default per-contract mode (cheapest, scope-clean):
  ```
  esbmc Token.flat.solast --sol Token.flat.sol --contract Token \
        --branch-coverage-claims --k-induction --unlimited-k-steps \
        --coverage-covered-set <proj>/union.json \
        --memlimit 8g --timeout T --quiet --no-assertions
  ```
  Measured semantics: counts **only** the contract's own decisions; the
  inherited 3rd-party base is auto-scoped-out (semantics A). Single-file
  `B is A` default run: Branches:2/2/100%, only `run` counted, `A`'s
  decision scoped out.

- **Has an own abstract base** (`Vault is Base`) → whole-unit + exclude 3rd-party:
  ```
  esbmc Vault.flat.solast --sol Vault.flat.sol --contract Vault \
        --coverage-whole-unit --coverage-exclude-contract Ownable \
        --coverage-covered-set <proj>/union.json \
        --branch-coverage-claims --k-induction --unlimited-k-steps \
        --memlimit 8g --timeout T --quiet --no-assertions
  ```
  Measured semantics:
  - whole-unit covers the entry's own + **all transitive own ancestors'**
    decisions in one run (3-level `Leaf is Mid is Base`: one Leaf
    whole-unit run hit `g`, `m`, `baseStep`).
  - `--coverage-exclude-contract Ownable` drops the 3rd-party base's
    decisions from **both denominator and numerator** (measured 6→4),
    while the project-own base stays in scope (`ProjBase` kept).
  - This whole-unit run is the **only** path that covers an
    own-abstract/internal base (Step 1).

## Step 3 — Order the runs

1. An ancestor's "covering descendant" runs **before** heavier descendants
   (once it whole-unit-runs, the shared base is in the union → later runs
   skip it).
2. Within a tier, **fewer static branches first** (fast union fill, so
   later/expensive k-induction queries chase fewer remaining probes).

Example order: `Token` (no base, smallest) → `Vault` (whole-unit, also
covers `Base`).

## Step 4 — One shared union, auto skip + dedup, crash-safe

Every run carries `--coverage-covered-set <proj>/union.json`:

- Run 1 writes covered probes into `union.json`.
- Run 2 reads it as seed: **already-covered probes are not re-instrumented
  / not re-solved** — measured: run 2's solver log contained **no**
  `Solving claim '… v > 10 …'` for the base decision run 1 already covered,
  while run 2's own new decision (`b > 2`) **was** solved.
- The same base decision reached via two different descendants appears in
  `union.json` **exactly once per edge** — measured: final union had
  `v > 10` / `!(v > 10)` each once (not twice) after both `D1` and `D2`
  ran through `baseStep`.
- Crash/timeout-safe: the union is atomically written per covered edge
  (Item 2e), so a SIGKILL/timeout mid-run still banks partial progress;
  the next run continues from the banked union.

## Step 5 — Report

**Project coverage = covered probes in `union.json` ÷ static total probes.**

Per-run % is discarded — measured to be misleading: a single whole-unit
run shows a low number because unexercised sibling contracts in the file
sit in its denominator (Leaf run = 50%; D1 run = 40% → after D2 with the
shared union = 60% → climbs further). Only the accumulated union is honest.

---

## End-to-end on the running example

```
0. flatten:  Vault.sol → Vault.flat.sol (Ownable+Base+Vault, no import)
             Token.sol → Token.flat.sol (Ownable+Token,      no import)
1. classify: Ownable = 3rd-party (exclude)
             Base    = own-abstract-base (no standalone)
             Vault, Token = has-external-entry
2-3. order:  Token (no base, smaller) first ; Vault (whole-unit, carries Base) next
4. run:
   ① esbmc Token.flat.solast --contract Token \
        --coverage-covered-set union.json --k-induction --unlimited-k-steps ...
        (default mode; only Token's own decisions; Ownable auto-scoped-out)
   ② esbmc Vault.flat.solast --contract Vault --coverage-whole-unit \
        --coverage-exclude-contract Ownable \
        --coverage-covered-set union.json --k-induction --unlimited-k-steps ...
        (Vault + Base counted & covered; Ownable out of denom+numer;
         probes Token already covered are skipped this run; Base deduped)
5. report:   covered(union.json) ÷ static-total  =  project branch coverage
```

---

## Soundness boundary

- **Sound** on a single (flattened) compilation unit — proven across
  cells: abstract-internal standalone (0 VCC), abstract-public standalone
  (100%), default-mode own-decision scoping, whole-unit transitive
  ancestor coverage, 3-level inheritance, whole-unit+exclude
  denom+numer drop with own-base kept, cross-run skip, cross-descendant
  union dedup, crash-safe atomic accumulation.
- **Refuted** for genuine multi-file (un-flattened) projects: default
  mode → 0 branches; whole-unit → ancestor decisions mis-keyed to the
  descendant file; exclude → removes the descendant's own decisions;
  cross-file dedup impossible (per-file loc strings differ). **Remedy
  (Step 0 flatten) proven to fully restore correctness.**

## Honest residuals (not yet closed)

1. **Flatten robustness** — cross-library name collisions, multiple solc
   versions: the pilot's `flatten_inputs.sh` owns this; not stress-tested
   here.
2. **Cost metric for Step 3** — Step 5/C4 shows the whole-unit
   denominator is the whole flat file, so "cheapest" must be measured on
   the whole-unit static probe count, not a contract's own. ESBMC has no
   "count-only" dry-run mode (`--coverage-count-only` does not exist) yet.
3. **Classifier AST predicate** — "has externally-callable entry" was
   determined behaviourally here; the precise `.solast` predicate
   (≥1 non-abstract public/external function, or a public constructor
   path) is not yet pinned to AST fields.

## Relationship to shipped ESBMC primitives

| Algorithm step | Relies on (shipped) |
|---|---|
| Step 0 flatten | external (pilot `flatten_inputs.sh`) |
| Step 2 default-mode own-decision scoping | semantics A (`628ebad61f`) |
| Step 2 whole-unit ancestor coverage | `--coverage-whole-unit` (`1b5a05a43a`) |
| Step 2 exclude 3rd-party from denom+numer | `--coverage-exclude-contract` Item 5-d (`4d951f8bbf`) |
| Step 4 cross-run skip + dedup | `--coverage-covered-set` Item 2 (`96db5e9b1d`) |
| Step 4 crash-safe accumulation | Item 2e atomic write (`0bcf799929`) |
| Step 5 honest denominator | Item 2c static no-skip universe |
| The orchestrator itself (Steps 0–5 driver) | **does not exist — this spec** (Item 4 descoped) |
