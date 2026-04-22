# Minimiser Algorithm

## 1. Purpose and scope

When a verifier such as ESBMC detects a property violation in a smart
contract, the source under verification is often larger than strictly
necessary to reproduce that violation: most functions, state variables,
and supporting declarations are irrelevant to the counter-example. A
smaller reproducing source reduces SMT encoding cost on subsequent runs,
makes the bug easier to inspect, and produces a compact artifact
suitable for paper replication packages.

This document defines the *minimiser*: a three-phase, monotone,
under-approximating source-to-source reduction that preserves a named
violation. The minimiser is backend-agnostic — it uses the Solidity
compiler as a syntactic oracle and the verifier as a semantic oracle.

## 2. Problem statement

**Given.**

- A Solidity program `P` (one or more source files).
- A verifier `V` (here: ESBMC) and a flag set `Φ` such that `V(P, Φ)`
  reports a violation with oracle tuple

$$
o \;=\; (c,\; f,\; t,\; \ell)
$$

where
- `c` — contract name containing the violation;
- `f` — function name (or synthetic entry, e.g. `mintToken_onlyOwner`)
  containing the violation;
- `t` — bug type (e.g. `arithmetic overflow on add`, `assertion`, `TOD
  race on balanceOf`);
- `ℓ` — *function-relative* location (line offset from the start of the
  function body), stable under any reduction that preserves function
  bodies verbatim.

**Required.** Produce a reduced program `P' ⊑ P` (behaviour-subset) such
that `V(P', Φ)` reports a violation with the *same* oracle tuple `o`,
and such that no strict sub-reduction of `P'` (within the set of
reductions the minimiser considers) preserves `o`.

**Invariant (soundness).** Any witness the verifier produces on `P'` is
a witness on `P`. Equivalently: the set of concrete executions of `P'`
is a subset of those of `P`.

## 3. Permitted reductions

Three kinds of source transformations are considered. Each, applied in
isolation, is a strict under-approximation of the input program.

**R1 — Dead-code sweep.** Delete declarations that are syntactically
unreferenced by any retained declaration: unused `import` directives;
`struct`, `enum`, `event`, and custom `error` definitions with no use
site; functions with neither callers in the current program nor an
external-interface role.

**R2 — Function removal.** Delete a public or external function whose
syntactic closure does not intersect the mandatory set (see §5).

**R3 — Visibility restriction.** Change a function's visibility from
`public` or `external` to `internal`, provided the program still
compiles and the oracle still holds. This reduces the attacker's direct
action space — the set of transactions a nondet dispatcher can initiate
— without altering the function's semantics.

Operations explicitly *not* considered in this version:

- Removal or modification of a function body.
- Removal of state variables (risk of breaking inheritance, public
  getters, delegatecall slot aliasing).
- Removal of modifiers or their constituent logic.
- Weakening of `require`/`assert` guards.

This scope guarantees that the function-relative location `ℓ` in `o` is
preserved across transformations, so oracle comparison is a simple tuple
equality.

## 4. Soundness

**Lemma 1 (R1 soundness).** A program obtained from `P` by R1 has the
same behaviour set as `P` restricted to reachable configurations.
Unused `import`s have no runtime effect. Unreferenced type declarations
contribute no clauses to the verifier's encoding. Functions with no
caller and no external-interface role are unreachable, and removing them
cannot introduce a new execution.

**Lemma 2 (R2 soundness).** Deleting a function `g` not in the syntactic
closure of the mandatory set restricts the program's attacker-driven
transaction alphabet: any trace of the reduced program is a trace of
`P` with `g`-invocations elided.

**Lemma 3 (R3 soundness).** Demoting visibility from `public`/`external`
to `internal` restricts the set of top-level entry points available to
the attacker. Internal callers are unaffected; external attackers can
no longer directly invoke the demoted function.

**Theorem (minimiser soundness).** Let `P' ⊑_R P` denote the program
obtained from `P` by a sequence of R1/R2/R3 transformations. Then every
concrete execution of `P'` is a concrete execution of `P`. Consequently,
any witness `V` reports on `P'` is also a witness on `P`.

*Proof sketch.* Each step is a strict under-approximation (Lemmas 1–3).
Composition of under-approximations is an under-approximation. QED.

Corollary: if `V(P', Φ)` reports oracle `o`, then `V(P, Φ)` would also
report `o` — the reduced artifact is a valid reproducer.

## 5. Mandatory set

Before any reduction, the minimiser fixes a set `M` of declarations
that must be retained across all phases:

1. The contract `c` containing the violation.
2. The function `f` containing the violation (including its outer
   wrapper if `f` is a synthetic `*_<modifier>` aux; in that case the
   original function is locked as well).
3. The constructors of `c` and of every contract in `c`'s linearized
   base list.
4. Any modifier referenced by a retained function (because removing a
   modifier alters a retained function's semantics).
5. Every function whose name appears in the verifier's counter-example
   trace `π` (the set of contract methods stepped through between
   constructor and violation; extracted from the verifier's internal
   trace, not from stderr parsing — see `--dump-violation-info`).

State variables referenced by any declaration in `M` are retained
transitively by the Solidity compiler itself when the minimiser
re-compiles `P'`; the minimiser need not enumerate them.

## 6. Algorithm

The minimiser runs three sequential phases. Let `C(P)` be the solc
compilation predicate (`true` iff `P` compiles without error), and let
`V(P, Φ) ≡ o` denote that the verifier reports oracle `o` on `(P, Φ)`.

```
MINIMISE(P, Φ, o):
  # Phase 0 — cheap, compiler-driven dead-code sweep
  P₀ := DEAD_CODE_SWEEP(P)
  assert C(P₀)

  # Phase 1 — compile-driven syntactic closure
  (P₁, level) := BUILD_CLOSURE(P₀, M, Φ, o)
  if level = FAILED:
    return P (give up; record failure)

  # Phase 2 — verifier-driven semantic reduction
  P₂ := SEMANTIC_REDUCE(P₁, M, Φ, o)
  return P₂
```

### 6.1 Phase 0 — dead-code sweep

```
DEAD_CODE_SWEEP(P):
  ast := solc --ast-compact-json P
  U := {unused imports} ∪ {unreferenced structs, enums, events, errors}
     ∪ {functions with no caller and no external-interface role}
  return P with U deleted
```

This phase performs no verifier calls and changes no behaviour
reachable from any retained declaration.

### 6.2 Phase 1 — syntactic closure with four-level fallback

Starting from the mandatory set `M`, add declarations iteratively until
`solc` accepts the program and `V` reproduces `o`. If a level fails,
escalate.

```
BUILD_CLOSURE(P₀, M, Φ, o):
  # Level 0 — mandatory-only
  S := M
  while true:
    P_S := keep_only(P₀, S)
    if not C(P_S):
      missing := parse_solc_errors(solc P_S)
      if missing = ∅:  break  # compile error unrelated to missing decls
      S := S ∪ missing
      continue
    break
  if C(P_S) and V(P_S, Φ) ≡ o:
    return (P_S, 0)

  # Level 1 — union with all public/external functions
  S := S ∪ public_external_functions(P₀)
  P_S := close_syntactically(P₀, S)  # same iterative compile loop
  if C(P_S) and V(P_S, Φ) ≡ o:
    return (P_S, 1)

  # Level 2 — full P₀
  if V(P₀, Φ) ≡ o:
    return (P₀, 2)

  # Level 3 — diagnostic: retry without Phase 0 (is the sweep the cause?)
  if V(P, Φ) ≡ o:
    return (P, 3)          # report "Phase 0 corrupted reproduction"

  return (⊥, FAILED)        # neither original nor reduced reproduces — give up
```

Each level is recorded in the run manifest. The rationale for four
levels:

- L0 is the target: minimal attack surface.
- L1 handles setup-function scenarios where an exploit needs state set
  by functions unreachable from the bug location's call graph.
- L2 handles exotic cases where even L1 is insufficient (e.g. the bug
  depends on a library contract whose interaction is not captured by
  the counter-example trace).
- L3 is a diagnostic for Phase 0 false positives (e.g. a function
  misclassified as dead because it implements an interface not visible
  to the sweep).

If L3 succeeds, the minimiser returns the original program unchanged
and flags the manifest; no false minimisation is output.

### 6.3 Phase 2 — semantic reduction with weighted ordering

Iterate over non-mandatory declarations in `P₁`, attempting R2 then R3
on each. Candidates are ordered by a heuristic weight favouring
deletions most likely to succeed.

```
SEMANTIC_REDUCE(P₁, M, Φ, o):
  candidates := declared_functions(P₁) ∖ M
  sort candidates by DESC weight(f)   # see weight function below
  P := P₁
  for f in candidates:
    # R2 — try delete
    P' := delete_function(P, f)
    if C(P') and V(P', Φ) ≡ o:
      P := P'
      mark(f, "deleted")
      continue

    # R3 — try visibility → internal (applies to public/external only)
    if visibility(f) ∈ {public, external}:
      P' := set_visibility(P, f, internal)
      if C(P') and V(P', Φ) ≡ o:
        P := P'
        mark(f, "internalized")
        continue

    mark(f, "preserved")
  return P
```

**Weight function (v1).** Simple, cheap, empirical:

$$
\mathrm{weight}(f) \;=\; 3 \cdot [f \notin \pi] \;+\; 2 \cdot [\mathrm{callers}_P(f) \cap \mathrm{retained} = \emptyset] \;+\; 1 \cdot [\mathrm{vis}(f) \in \{\mathit{public}, \mathit{external}\}]
$$

Higher weight → tried first. A future version may add a fourth signal
(does `f` write state referenced by the violated property) at a small
AST-analysis cost.

This is a greedy pass. Under a monotone oracle (see §4) any iteration
order converges to some 1-minimal fixed point; the heuristic affects
*which* 1-minimal is reached and how fast, not whether one is reached.

## 7. Oracle comparison

Two oracle tuples `o = (c, f, t, ℓ)` and `o' = (c', f', t', ℓ')` are
equal iff `c = c'`, `f = f'`, `t = t'`, and `ℓ = ℓ'` — component-wise
string/integer equality. `ℓ` is function-relative and therefore stable
under R1/R2/R3 (which do not modify function bodies). The verifier
exposes `o` via `--dump-violation-info <path.json>`; the minimiser
never parses the verifier's free-form stderr.

## 8. Termination

Phase 0 is a single pass over the AST: O(|P|) time, terminates.

Phase 1 at each level adds at least one symbol per iteration of the
inner compile loop; bounded by the number of top-level declarations in
`P`. Escalation across levels is bounded by 4 steps.

Phase 2 tries each candidate at most twice (R2 then R3). `|candidates|
≤ |declared_functions(P)|`. Total verifier calls: `O(|F|)` where `F` is
the set of Solidity functions.

## 9. Output

The minimiser produces, side by side:

1. The reduced program (multi-file structure preserved).
2. A `manifest.json` recording:
   - The oracle tuple.
   - Every declaration removed in Phase 0.
   - The final syntactic closure and the Phase 1 level used.
   - Per-candidate Phase 2 outcomes (deleted / internalized / preserved,
     with verifier-call timings).
   - Aggregate counts: `|P|` vs `|P'|` functions, lines, bytes;
     verifier-call budget spent; compiler-call budget spent; wall time.

The multi-file structure allows downstream tools to diff the reduced
program against the original cleanly. The manifest is the
methodology-paper data source.

## 10. Limitations

- **Trace precision.** The mandatory set depends on the set `π` of
  functions visible in the verifier's counter-example. For ESBMC, `π`
  is extracted inside the frontend (from the symbol table crossed with
  per-step source locations) rather than by parsing `--show-stacktrace`
  — see the internal `--dump-violation-info` flag. A conservative `π`
  only slows Phase 1; it does not affect soundness.

- **Compiler-lock-in.** The Solidity version is fixed to the single
  `solc` on PATH (currently 0.8.30). Inputs written against earlier
  versions must be upgraded before minimisation; see
  `feedback_upgrade_to_08.md` for the policy.

- **Verifier determinism.** The algorithm assumes `V(P', Φ) ≡ o` is a
  deterministic predicate. If `V` is nondeterministic (solver tactic
  variation, timeout-influenced answers), retries at the oracle
  boundary may be necessary; this is a verifier-configuration concern,
  not a methodological one.

- **Single violation.** The minimiser preserves exactly one violation.
  Programs with multiple independent bugs require either repeated runs
  (one per oracle) or a lifted oracle `{o_1, …, o_k}` — the latter is
  future work.
