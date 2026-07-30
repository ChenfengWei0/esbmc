# enc decodability

Read in full: `src/goto-programs/goto_coverage.cpp` (6602 lines),
`src/goto-programs/goto_coverage.h` (905), `src/esbmc/bmc.cpp` (3575).
No file was modified; no binary was run.

## 1. How enc is computed

There are TWO accumulators that must agree, and they are computed by two
different pieces of code.

### Phase 1 — the runtime ghost `tr` (what the assert reads)

`tr` is declared and initialised to **1** at the unit's entry, before the
original first instruction, together with `cnt = 0`:

`goto_coverage.cpp:3706-3744`
```cpp
    // DECL tr and initialise `tr = 1` at function entry (in that order),
    // both before the original first instruction.
      ini.code = code_assign2tc(tr, constant_int2tc(utype, BigInt(1)));   // :3722
      cini.code = code_assign2tc(cnt, constant_int2tc(utype, BigInt(0))); // :3738
```

The per-decision update is the `snapshot` lambda, inserted *before* the
decision instruction:

`goto_coverage.cpp:3619-3642`
```cpp
    auto snapshot = [&](goto_programt::targett &sit, const expr2tc &val) {
      a.code = code_assign2tc(
        tr,
        add2tc(
          utype,
          mul2tc(utype, tr, constant_int2tc(utype, BigInt(2))),
          typecast2tc(utype, val)));            // tr = tr*2 + (uint64)val
      ...
      b.code = code_assign2tc(
        cnt, add2tc(utype, cnt, constant_int2tc(utype, BigInt(1))));  // cnt = cnt+1
```

Both are `unsignedbv 64` (`goto_coverage.cpp:3585-3613`) and both instructions
are stamped `location.property("skipped")` so they are not themselves claims.

Which instructions get a snapshot, and in what order — `goto_coverage.cpp:3673-3704`:
* every `GOTO` with a non-`true` guard → `snapshot(it, it->guard)` (one bit);
* every `ASSIGN` / `RETURN` not marked `skipped` → the folded short-circuit /
  ternary operands collected by `collect_short_circuit_decisions`
  (`goto_coverage.cpp:1455-1477`: `or2t.side_1`, `and2t.side_1`, `if2t.cond`),
  snapshotted in collect order `j = 0 .. K-1`, and the whole site is **skipped**
  if `K > SC_DECISION_MAX == 12` (`goto_coverage.cpp:1453`, `:3693-3697`).

So `tr` is a bit string: a leading sentinel `1`, then one bit per decision in
**execution order**, first decision = most significant.

### Phase 2 — the enumerated `enc` (what goes in the claim)

DFS seed, `goto_coverage.cpp:3813-3821`:
```cpp
    stack.push_back(
      {goto_program.instructions.begin(),
       (uint64_t)1,          // enc = 1
       becntt{},
       (uint64_t)0,          // depth = 0
       ...});
```

Conditional GOTO, `goto_coverage.cpp:4276-4291`:
```cpp
            stack.push_back(
              {pc->get_target(),
               enc * 2 + 1,          // guard TRUE / taken  -> :4278
               ...
          idh = step_id(idh, occ, dsite, 0, /*polarity=*/false);
          enc = enc * 2 + 0;         // guard FALSE / fall-through -> :4290
          ++depth;
```

Folded short-circuit operands in an ASSIGN, `goto_coverage.cpp:4407-4425`
(and identically for RETURN operands at `:4332-4351`):
```cpp
            for (uint64_t mask = 0; mask < (uint64_t(1) << K); ++mask)
              for (size_t j = 0; j < K; ++j)
              {
                const bool bit = ((mask >> j) & 1) != 0;
                e = e * 2 + (bit ? 1 : 0);      // :4419
                h = step_id(h, o, asite, (unsigned)j, bit);
                ++d;
              }
```

Overflow: a path is **dropped** (not aliased) once `enc >= 1<<62`
(`goto_coverage.cpp:4236-4240`, `:4339`, `:4413`).

### The exit assert

`goto_coverage.cpp:3931-3965`, inside `emit_exit`:
```cpp
      expr2tc g = or2tc(
        notequal2tc(tr, constant_int2tc(utype, BigInt(penc))),
        notequal2tc(cnt, constant_int2tc(utype, BigInt(pdepth))));
      std::string comment =
        id2string(f_it->first) + ":path:" + std::to_string(penc);   // :3952
      path_decision_depth[{comment, loc->location.as_string()}] = pdepth;  // :3962
```

**Recurrence, stated once:**
`enc_0 = 1`; `enc_{k+1} = 2*enc_k + b_{k+1}` where `b = 1` for the guard-true /
taken arm and `b = 0` for the guard-false / fall-through arm, and for a folded
short-circuit site the K operand bits are appended in collect order
`j = 0..K-1` (operand 0 becomes the *higher-order* bit). `depth` = number of
appended bits, so `enc ∈ [2^depth, 2^(depth+1)-1]`.

## 2. Decodable back to (branch point, arm, occurrence)?

**Split answer — YES for the arms, YES for the sites only with the goto program
in hand, NO from `cov-report.json` as it stands.**

* **Arms: yes, arithmetically.** Given `(enc, depth)` the k-th decision's arm is
  `(enc >> (depth - k)) & 1`, k = 1..depth. No extra information is needed. The
  code already does exactly this shift (see §3).

* **Sites: not a function of `enc`.** `enc` is a pure bit accumulator; nothing
  about the source location is mixed into it. The bit→site mapping is
  path-dependent (bit 3 of one path and bit 3 of a sibling can come from
  different instructions once the prefixes diverge). So `enc` alone cannot name
  the sites.

  It *is* recoverable by **replaying the DFS** against the instrumented goto
  program, driven by the bits instead of by fan-out: the walk is deterministic
  once the arm choices are fixed. At a conditional GOTO the next bit picks
  `pc->get_target()` vs `std::next(pc)`; at a short-circuit ASSIGN/RETURN site
  the next K bits are consumed in order `j = 0..K-1` and control always
  continues to `std::next(pc)` (`goto_coverage.cpp:4438`); loop budgets
  (`becnt`, `goto_coverage.cpp:4216-4255`) depend only on the walked prefix. So
  the sequence of sites is uniquely determined by `(enc, depth)` **plus the
  program**. This is what the enumeration itself relies on.

* **Occurrence index: computed but hashed away.** The DFS does maintain a
  per-site occurrence counter, `goto_coverage.cpp:3796` and `:3882-3896`:
  ```cpp
    using occt = std::map<uint64_t, unsigned>;
    auto step_id = [&dfs_decision_sites](
                     uint64_t idh, occt &occ, const std::string &site,
                     unsigned sub, bool polarity) {
      dfs_decision_sites.emplace(site, sub);
      const uint64_t sk = fnv1a(site + "#" + std::to_string(sub));
      const unsigned n = occ[sk]++;                  // <- the occurrence index
      uint64_t h = fnv1a("|", idh);
      h = fnv1a(hex64(sk), h);
      h = fnv1a(polarity ? "T" : "F", h);
      h = fnv1a(std::to_string(n), h);
      return h;
    };
  ```
  `n` is folded into a 64-bit FNV-1a hash (the cross-run `stable_id`) and then
  discarded. FNV is one-way here, so the stable id is **not** a decode route —
  and it is not emitted into the JSON either. Occurrence is however trivially
  re-derivable during the replay above (count how many times each `(site, sub)`
  has been seen).

Conclusion for your projection: the decode is *possible* but it must happen
**inside the tool**, during or right after enumeration. Nothing in the current
report lets an external consumer do it.

## 3. Already implemented?

**Partially, and only in one gated mode. No function name, no JSON field.**

The map exists — `goto_coverage.cpp:3785-3789`:
```cpp
    std::map<uint64_t, std::string> decision_site;
    const bool trace_decisions =
      outer_on && (f_it->first.as_string() == outer_unit ||
                   f_it->first.as_string().find("@F@" + outer_unit + "#") !=
                     std::string::npos);
```
It is keyed by the **prefix enc value after that decision**, and written at the
four fan-out points, all guarded by `trace_decisions`:
* `:4275` `decision_site[enc * 2 + 1] = dsite + " [guard TRUE]";`
* `:4289` `decision_site[enc * 2 + 0] = dsite + " [guard FALSE]";`
* `:4348` `decision_site[e] = rsite + " (RETURN operand j) [TRUE|FALSE]";`
* `:4422` `decision_site[e] = asite + " (short-circuit operand j) [TRUE|FALSE]";`

And the decode loop exists — `goto_coverage.cpp:5116-5139`, inside the
`if (outer_on)` branch:
```cpp
        if (trace_decisions && pdepth > 0)
        {
          std::string seq;
          for (uint64_t k = 0; k < pdepth; ++k)
          {
            const uint64_t key = penc >> (pdepth - 1 - k);
            auto dit = decision_site.find(key);
            seq += "\n    #" + std::to_string(k + 1) + " ";
            seq += dit == decision_site.end()
                     ? "<not recorded — enc key " + std::to_string(key) + ">"
                     : dit->second;
          }
          log_status(
            "--path-cov-outer-box: path enc={} depth={} DECISION SEQUENCE ...",
            penc, pdepth, seq);
        }
```

Limitations of what exists, all load-bearing for you:
1. **Gated off by default.** `trace_decisions` requires `--path-cov-outer-box`
   *and* the unit matching that spec's `"unit"`. In a plain
   `--solidity-path-coverage` run nothing is recorded. (The gate is deliberate —
   comment at `:3781-3784` cites a unit enumerating 2733 paths.)
2. **Log line only.** It is a `log_status` string; `decision_site` is a local of
   the per-unit loop body and is destroyed at the end of the iteration. It never
   reaches `cov-report.json`.
3. **No occurrence index** in the printed text — only site + polarity.
4. **No guard expression text** — only `location.as_string()` plus an operand
   index. Branch-coverage claim keys are `(from_expr(ns,"",cond), loc)`
   (`goto_coverage.cpp:1626-1636`), so location alone does not key a branch arm.
5. It is *not* a named function anywhere; there is no `decode_enc(...)`,
   no `path_decisions` field, no `stable_id` field.

The only per-path identity data that does reach the report is `path_id` (the
enc, as a decimal string) and `path_depth` — see §5.

## 4. If not, smallest change

Concrete plan, all inside code that already has everything in scope.

**Option A (smaller, O(paths) memory) — publish the map, let the consumer shift.**
1. Change `decision_site`'s value type to a small struct
   `{std::string loc; std::string cond; unsigned sub; bool polarity; unsigned occ;}`
   (`goto_coverage.cpp:3785`). At `:4275`/`:4289` `pc` is in scope so
   `from_expr(ns, "", pc->guard)` gives the branch-coverage condition text;
   at `:4348`/`:4422` re-collect the operands with
   `collect_short_circuit_decisions(src, ...)` into a vector (the source expr
   `rsrc`/`src` is already in scope at `:4318`/`:4397`) and use `ops[j]`.
   `occ` comes from having `step_id` (`:3882-3896`) return or out-param the `n`
   it already computes.
2. Drop the `outer_on` conjunct from `trace_decisions` (`:3786`) — or gate it on
   a new `--path-cov-emit-decisions` flag so the cost stays opt-in.
3. Copy the unit's `decision_site` into a new static
   `std::map<std::string /*unit id*/, std::map<uint64_t, decisiont>>` on
   `goto_coveraget` (header declaration + one definition line at the top of
   `goto_coverage.cpp`).
4. In `bmc.cpp`, in the `if (is_path_cov)` block right after `path_depth` is
   written (`bmc.cpp:1534-1539`), emit the array by doing the same
   `penc >> (pdepth-1-k)` walk, or emit the per-unit map once under a new
   top-level `report["decision_sites"]` key.

   Size: ~15 lines in `goto_coverage.cpp`, ~4 in the header, ~12 in `bmc.cpp`.

**Option B (simplest, O(paths × depth) memory) — materialise the list per path.**
Do steps 1–2 above, then extend `emit_exit` (`goto_coverage.cpp:3931-3965`),
which already has `penc`, `pdepth` and the claim key in scope and already writes
`path_decision_depth[{comment, loc}]` at `:3962`. Add next to it:
```cpp
      for (uint64_t k = 0; k < pdepth; ++k) { auto d = decision_site.find(penc >> (pdepth-1-k)); ... }
      path_decisions[{comment, loc->location.as_string()}] = seq;
```
plus a matching `claim_entry["decisions"]` in `bmc.cpp` near line 1539.
Size: ~20 lines total. Cost note: on the measured 120166-path benchmark this
stores 120166 × depth entries, which is exactly the reason the existing map is
gated — Option A avoids that.

**Caveats that will bite the branch-arm projection** (these are facts from the
code, not opinions — they mean the two denominators are *not* the same set):

* **Polarity ↔ branch claim key is inverted.** `goto_coverage.cpp:1677-1689`:
  "a probe assert(P) fails when P is false, so `assert(it->guard)` covers the
  FALL-THROUGH edge and `assert(!it->guard)` the GOTO-taken edge". So path
  polarity `TRUE` (taken) maps to the branch claim
  `(from_expr(ns,"",gen_not_expr(cond)), loc)` and polarity `FALSE` maps to
  `(from_expr(ns,"",cond), loc)`. Note `gen_not_expr` strips a leading `not`
  (`goto_coverage.cpp:6231-6236`), so the text must be produced the same way.
* **Path coverage adds a decision branch coverage never sees.** The synthesised
  ABI non-payable gate (`goto_coverage.cpp:3535-3564`) is a conditional GOTO
  `msg_value == 0` whose `location` is *copied from the unit's first body
  instruction* (`:3523`, `:3538`). It is snapshotted and enumerated like any
  other decision but has no branch-coverage counterpart. Matching on
  `(cond, loc)` rather than `loc` alone is what keeps it from colliding with a
  real arm.
* **Internal calls are physically inlined** into the unit before enumeration
  (`sol_path_inlinet::expand_here`, `goto_coverage.cpp:2209-2254`), so one
  callee decision appears in *many* units' path sets — the projection is
  many-to-one. The inlined copies carry `location.set("sol_path_inlined", true)`
  (`:2246`) but `location.as_string()` is unchanged (branch coverage relies on
  the same property, `:1621-1625`), so their arms *do* match branch claim keys.
* **Degradation and the depth bound withdraw call points**
  (`goto_coverage.cpp:3069-3155`, `:3158-3195`): those callees' arms are then
  absent from every path of that unit while still being in branch coverage's
  denominator. `degraded_call_sites` records exactly which.
* **Loops give the same arm several occurrences per path**
  (`path_cov_unwind`, `:4216-4255`); branch arms have no occurrence dimension,
  so the projection collapses them.
* **Different scoping filters.** `branch_coverage()` skips instructions whose
  file is not in `location_pool` and applies `scope_contract` /
  `exclude_contracts` per decision (`:1568-1619`). The path DFS applies
  `location_pool` / `scope_contract` at the **unit** level only
  (`:3409-3426`) and then branches on *every* conditional GOTO in the expanded
  body. So a path decision can exist with no branch-arm counterpart.

## 5. cov-report.json per-path fields

Written by `report_coverage()` in `bmc.cpp`; the file name is hardcoded:

`bmc.cpp:1828-1830`
```cpp
    std::ofstream out("cov-report.json");
    out << report.dump(2) << std::endl;
    log_success("Coverage report written to cov-report.json");
```

Emission is gated on `--cov-report-json` (`bmc.cpp:1322`) and the claim loop runs
over `goto_coveraget::all_claims` (`bmc.cpp:1378`).

### Fields on every claim (all modes) — `bmc.cpp:1392-1398`
| field | line |
|---|---|
| `condition` (prettified `claim_msg`) | 1393 |
| `file`, `line`, `column`, `function` (parsed from the location string by `parse_claim_location`, `bmc.cpp:728-766`) | 1394-1397 |
| `status` — set to `"covered"`/`"uncovered"` here, then **overwritten** for path claims | 1398 |
| `feasibility` — **k-path only**, not path coverage | 1407 |

### Additional fields for a path claim (`if (is_path_cov)`, `bmc.cpp:1415-1706`)
| field | value | line |
|---|---|---|
| `status` | tri-state `"F"` / `"I"` / `"U"` (overwrites the string above) | 1446 |
| `u_reason` | one of `named-obstacle`, `unit-not-entered`, `bounded-holds`, `solver-unknown`, `not-solved-this-run`; only when status == `"U"` | 1453 |
| `u_reason_detail` | prose; only for `named-obstacle` / `unit-not-entered` | 1459, 1473 |
| `bounded_holds` | `true` when the solver returned UNSAT and the run cannot prove unreachability | 1480 |
| `not_solved_this_run` | `true` when unwitnessed and no verdict recorded | 1484 |
| `bound.max_tx` | string, `"default"` when unset | 1486 |
| `bound.unwind` | string, `"default"` when unset | 1487 |
| `bound.kind` | `"bounded"` (always, see `path_cov_can_prove_unreachable()`, `bmc.cpp:722-725`) | 1489 |
| `bound.tx_exploration` | prose describing what the tx driver actually explored | 1490 |
| `bound.loops_truncated` | `true` only when some loop hit its bound | 1492 |
| `exit_kind` | `"revert"` / `"undetermined"` / `"normal"` | 1506 |
| `revert_kind` | `"rollback"` or `"custom-error"` | 1511, 1513 |
| `exit_kind_undetermined_reason` | prose, only when `exit_kind == "undetermined"` | 1515 |
| `witnessed_in_earlier_round` | bool | 1520 |
| **`path_id`** | **`enc` as a decimal STRING** — `claim_msg.substr(pos+6)` after `":path:"` | 1527 |
| `path_function` | the unit id, `claim_msg.substr(0, pos)` | 1528 |
| **`path_depth`** | uint64, from `goto_coveraget::path_decision_depth` | 1538 |

### CE payload, only when `goto_coveraget::path_ce` has an entry (`bmc.cpp:1544-1696`)
| field | line |
|---|---|
| `inputs` (object name→value) | 1557 |
| `env` (object) | 1558 |
| `extcall_returns` (array of `{symbol, value}`) — **always empty today**; nothing writes `ce.extcall_returns` | 1567 |
| `ce_extraction.extcall_returns_unavailable_reason` | 1598 |
| `entry_storage` (object) | 1620 |
| `ce_extraction.entry_storage_unavailable_reason` | 1624 |
| `state_at_revert_point` + empty `final_state` (when `revert_pre_rollback`) | 1638-1639 |
| `final_state` (object) otherwise | 1642 |
| `state_written_value_unavailable` (array of names) | 1651 |
| `ce_extraction.harness_nondets_dropped` (size_t) | 1655 |
| `ce_extraction.sliced` (bool) | 1663 |
| `ce_extraction.payload_symbols_exempt_from_slicing` (bool) | 1664 |
| `ce_extraction.compact_trace` (bool) | 1666 |
| `ce_extraction.scoped_to_claim` (bool) | 1667 |
| `ce_extraction.post_state_may_include_later_tx` (prose) | 1669 |
| `ce_extraction.post_state_unavailable_reason` (prose) | 1679 |
| `ce_extraction.final_state_unavailable_reason` (prose) | 1690 |
| `ce_extraction.payload_absent_reason` — when status is `"F"` but no payload (cross-run carry-over) | 1702 |

### Top level (`bmc.cpp:1735-1826`)
`coverage_type` (`"solidity-path"`), `source_files[]`, `claims[]`,
`summary.total` / `.covered` / `.uncovered` / `.percentage`, and for path
coverage additionally `summary.paths_total`, `.F_feasible_with_ce`,
`.I_proven_unreachable`, `.U_undecided`, `.U_of_which_bounded_holds`,
`.U_reasons{<5 tokens>}`, `.revert_exit_paths`,
`.bound.{max_tx,unwind,kind,tx_exploration}`, `.note`,
`.known_limitation_entry_state`.

**Not emitted anywhere:** the decision sequence, the per-decision source
locations, the occurrence indices, and the content-addressed
`path_stable_id` (which exists in memory, `goto_coverage.h:156-157`, and is
written only to the cross-run covered-set file by
`write_path_covered_set_atomic`, `goto_coverage.cpp:134-172`).

## Uncertainties

1. **`decision_site` keyed by prefix-enc has one path I did not prove
   collision-free.** Two distinct partial paths cannot share an `enc` value
   (enc encodes the whole prefix), so the map is unambiguous *within a unit*.
   `enc` values do collide **across** units — stated at `goto_coverage.cpp:3779`
   — and the map is a loop-body local, so a published version must be keyed by
   unit id as well. I did not find a case where this is wrong today, but nothing
   in the code enforces it either.
2. **`(location, sub)` may not be a unique decision-site key.** A conditional
   `GOTO` uses `sub = 0` (`:3677`, `:4273`) and a short-circuit operand 0 of an
   `ASSIGN`/`RETURN` also uses `sub = 0` (`:3700`, `:4420`). If a `GOTO` and an
   `ASSIGN` ever carry the same `location.as_string()`, they share both the
   `phase1_decision_sites` entry and the `occ` counter slot. The `tr`-completeness
   check (`:4716-4741`) would not notice, because it compares the same key set on
   both sides. I could not construct a Solidity example that produces this from
   reading alone; settling it needs either a frontend read of how locations are
   stamped on lowered `&&`/`||` versus their enclosing `if`, or a run.
3. **Whether the replay decoder is exact under `--path-cov-certify` /
   `--path-cov-outer-box`.** Both modes insert extra instructions (an entry
   `ASSUME`, `:5529-5537`; coordinate snapshot DECL/ASSIGN, `:5013-5027`) *after*
   the DFS has run, all marked `skipped`. They are not decisions, so a replay
   over the post-instrumentation program should still be faithful, but I did not
   verify that no inserted instruction can alter `target_number`s used by the
   loop budget keys (`becnt[pc->get_target()->target_number]`,
   `:4223`) — `compute_target_numbers()` is called at `:3746`, before those
   insertions.
4. **Outer-box claims share the `path_id` parse.** In `--path-cov-outer-box`
   mode the probe claims are commented `<unit>:path:<enc>#ub_<coord>_<val>`
   (`:5271-5273`) and are inserted into `all_claims` (`:5275`), so if
   `--cov-report-json` is also on, `bmc.cpp:1527` will produce a `path_id` of
   `"12#ub_a_5"` rather than an integer. I did not check whether any consumer
   currently parses `path_id` as an integer.
5. I did not inspect `src/goto-programs/k_path_spanning.*`, the Foundry
   generator, or `esbmc_parseoptions.cpp` (where the CLI wires
   `cov_context`, `path_cov_unwind`, `scope_contract`). Nothing in the three
   files I read suggested `enc` is post-processed elsewhere, but the claim
   "no other decode exists in the tree" is scoped to those three files.
