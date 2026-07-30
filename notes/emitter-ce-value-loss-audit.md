I've read the full emission path. Here is the report.

---

# Audit: how a counterexample value can be lost or replaced on the Foundry emission path

Read-only. Nothing was built or run. Files read in full: `src/goto-symex/foundry.h`, `src/goto-symex/foundry.cpp` (all 3199 lines), `src/goto-programs/goto_coverage.h` (path_ce_t + named_obstacle_paths regions), `src/esbmc/bmc.cpp` (harvest + report), `src/c2goto/library/solidity/solidity_blockchain.c`, `src/c2goto/library/solidity/solidity_misc.c`, and four regression directories.

## 0. The central structural fact

**There are two independent harvests, and the Foundry emitter does not use the one whose header makes the "drop, never guess" promise.**

- `goto_coveraget::path_ce` (`inputs`/`env`/`entry_storage`/`final_state`) is harvested in `bmc.cpp:3073-3455` and consumed **only** by the `--cov-report-json` writer (`bmc.cpp:1626-1787`). Nothing in `foundry.cpp` mentions `path_ce`.
- The Foundry emitter re-harvests from scratch inside `foundry_generator::reconstruct` (`foundry.cpp:1113-2310`) by calling `smt_conv.get(...)` on SSA steps directly.

So the answer to Q1 and the answer to Q2 are about **different code**. The `path_ce` promise is kept; it just does not govern the artifact.

---

## 1. Q1 — Verifying the `path_ce_t` header claim

The claim (`goto_coverage.h:461-462`): *"Both are recorded ONLY from concrete model values; a symbolic or missing value is dropped rather than guessed."*

**VERIFIED TRUE.** The concreteness test is `bmc.cpp:3323-3324`:

```cpp
// Never guess: a value that did not come back from the model as a
// constant is dropped rather than rendered symbolically.
if (!is_constant_expr(st.value))
  continue;
```

`continue` — the step is skipped entirely; no default, no placeholder. Every one of `inputs`, `env`, `final_state` is populated **after** this gate (`bmc.cpp:3422-3433`), and `entry_storage` is a snapshot of `last_state`, which is only ever written after it. Mapping/dynamic-array state has a second, stricter gate at `bmc.cpp:3311-3314` (`is_index2t(lhs) && is_constant_expr(value) && !array && !struct`); a value failing it is **named but not valued** in `state_written_unrendered` (`bmc.cpp:3317-3318`) rather than dropped silently.

Two caveats worth stating precisely:

- The header sentence says "**Both**", naming only `inputs` and `final_state`. In fact `env` and `entry_storage` are under the same gate, and `extcall_returns` is under no gate at all because **it has zero writers** — confirmed by the in-code comment at `bmc.cpp:3408-3418` ("`ce.extcall_returns` has a declaration, three readers and ZERO writers") and the report text at `bmc.cpp:1650-1695`.
- A value can also be lost *before* the concreteness gate: `bmc.cpp:3230` (`is_nil_expr(lhs) || is_nil_expr(value)`), `bmc.cpp:3270-3277` (`_ESBMC_Nondet_Extcall*` → `dropped_internal`), `bmc.cpp:3383-3385` (`get_nondet_symbol` returns nil → `continue`, **not** counted in `dropped_internal`), and `bmc.cpp:3422-3433` (anything not `is_parameter` and not `msg_/tx_/block_` → `dropped_internal`).

---

## 2. Q2 — **THE VERDICT: YES.** A dropped input becomes a defaulted value in the emitted test, silently.

### The line

**`src/goto-symex/foundry.cpp:1351-1352`**

```cpp
      if (a.literal.empty())
        a.literal = default_sol_literal(decl.second);
```

`default_sol_literal` (`foundry.cpp:378-417`) returns `"0"` for `UINT*`/`INT*` (391-392), `"address(0)"` for `ADDRESS`/`ADDRESS_PAYABLE` (**393-394**), `"false"` for `BOOL` (389-390), `bytesN(0x00..)` (397-398), `new T[](4)` (404-408), `hex""`/`""` (412-415).

The preceding lines are the "concreteness test" on this path: `format_sol_value` (`foundry.cpp:287-376`) returns `""` whenever the model value is not a concrete scalar — `if (!is_constant_int2t(value)) return "";` at **360-361**, plus per-type early returns at 310, 337-338, 349-350, 352-353. `foundry.cpp:1346-1349` tries the declared-type reformat and then the recovery-site literal; `1350` records `from_recovered`; `1351-1352` substitutes the default; `1354` records the fact:

```cpp
      const bool from_recovered = !a.literal.empty();
      if (a.literal.empty())
        a.literal = default_sol_literal(decl.second);
      // A non-empty literal not sourced from a recovered value is a type default.
      a.defaulted = !a.literal.empty() && !from_recovered;
```

### `defaulted` has almost no consumer

Across the whole file, `sol_arg::defaulted` is written at `foundry.cpp:1263` and `1354`, and **read in exactly one place**: `foundry.cpp:2085-2096`, guarded by `if (deploy_remapped && kv.first == ctor_ts_contract)`.

Consequences, each a distinct hole:

| Route | Line | Is `defaulted` checked? |
|---|---|---|
| Ordinary method call (dispatcher segment) | `foundry.cpp:2132-2152` | **No.** Emitted with defaults. |
| `--function` / library focus route | `foundry.cpp:2013-2018` | **No.** |
| Coverage-claim fallback route | `foundry.cpp:2218-2223` | **No.** |
| Constructor, args recovered under the deploy contract | `foundry.cpp:2065-2098` | **No** — the guard at 2085 requires `deploy_remapped`. A ctor with 3 params of which 1 was recovered emits `new C(recovered, 0, address(0))`. |
| Constructor, args base-remapped | `foundry.cpp:2085-2096` | Yes → `ctor_unrecovered`, UNSUPPORTED. |
| Constructor synthesised because ctor reads env | `foundry.cpp:2107-2131` | Indirectly — checks `all_mock_or_none` (2115-2118), not `defaulted`. |
| Constructor synthesised because a contract was called but had no ctor call | **`foundry.cpp:2272-2274`** | **No guard at all.** `build_call(cn, cn, {})` with an empty recovered map → every argument defaulted → `new C(0, address(0))` in `setUp()`. |

So `foundry.h:46-48` ("a deploy with any defaulted arg is degraded to UNSUPPORTED") is **true only of the base-remapped constructor**, and is not true of the emitter as a whole.

### Independent, on-disk confirmation

`regression/esbmc-solidity/foundry_covgen_msgsender_not_pinned_knownbug/CityToken.cov.t.sol` is a checked-in generated artifact, and the two renderings are textually distinguishable — `format_sol_value` emits `address(uint160(N))` (`foundry.cpp:370-371`) while `default_sol_literal` emits `address(0)` (`foundry.cpp:393-394`):

- line 22 `try c0.approve(address(0), 0)` — `_to` **defaulted**
- line 62 `try c0.transfer(address(uint160(0)), 0)` — `_to` **recovered as 0**
- line 70 `try c0.transferFrom(address(uint160(0)), address(0), 0)` — `_from` recovered, `_to` **defaulted**
- line 95 `try c0.createToken(0, address(0), "", 0, 0)` — `_owner` **defaulted**, `_name` filler

Both forms in the same file, on the same parameter type. This is the defaulting path firing in production output.

### Two further silent substitutions on the same path

- **`STRING`** (`foundry.cpp:1259-1266`): renders `"aaa…"` of length `recovered_str_len` and then **explicitly sets `a.defaulted = false`** (line 1263). The length is from the model; every byte of content is fabricated. It is therefore invisible to any future `defaulted` check.
- **`BYTES_DYN`** (`foundry.cpp:335-358`): length from the model, content all-zero, and a length `> 4096` is silently rewritten to `32` (line 356). Because `format_sol_value` returns non-empty, `from_recovered` is true and `defaulted` stays false.

### Dedup makes it worse

`fingerprint` (`foundry.cpp:2381-2432`) hashes `a.literal` (line 2394). Two counterexamples that differ **only** in a parameter neither recovered collapse to one emitted case, and `generate()` reports them as "N cases standing for M claims" (`foundry.cpp:2974-2979`) — the collapse is visible as a claim count, but the reason is not.

### The codebase already knows

`foundry.cpp:3066-3113` is an explicit standing note:

> Calls carrying an argument whose CONTENT the reconstruction cannot recover. **REPORTED ONLY -- nothing is refused on this basis yet** … Measured on 1inch aqua: `ship` took four zero addresses, which alias to one storage slot, so the emitted call reverts on a path the census called normal -- the emitted input was simply not the counterexample's input.

The two counters it prints (`foundry.cpp:3104-3113`) key on **`a.sol_type`** (`ARRAY:` / `BYTES_DYN` / `STRING`), not on `a.defaulted`. **A defaulted `uint256` or `address` scalar is counted nowhere and printed nowhere.** That is the gap: the exact class the measured aqua failure belongs to has no counter.

---

## 3. Q3 — Illegal-on-chain values

**Nothing in the emitter rejects, or even flags, a case whose values are illegal on chain. Plainly: there is no such check anywhere in `foundry.cpp`.**

I read the file end to end. The only zero-comparisons present are `v != "0"` for `msg.value` (`foundry.cpp:2029-2030`, 2532-2534, 2806, 3131) — and those exist to *suppress a redundant pin*, not to reject anything.

Specifically:

| Illegal / degenerate value | Emitted? | Line |
|---|---|---|
| `vm.startPrank(address(0))` as deployer | **Yes.** Guard is `if (!d.empty())`; `"address(uint160(0))"` is non-empty. | `foundry.cpp:2520-2525`, emitted at `2736-2737` |
| `vm.prank(address(0))` per call | **Yes.** Guard is `if (!sdr.empty())`. | `foundry.cpp:2826-2831` |
| `vm.warp(0)` | **Yes.** Guard is `if (!t.empty())`; `"0"` is non-empty. | `foundry.cpp:2815-2821` (per-call), `2511-2517`/`2724-2729` (setUp) |
| `tx.origin`, `block.coinbase` | Not pinned at all — recovered by nothing, emitted by nothing. So a path guarded on `tx.origin` is replayed under Foundry's defaults, silently. | — |
| Contract-typed arg with no deployable address | **Rejected** — the one case that *is* handled: non-interface or unrenderable-mock → `out.supported = false`. | `foundry.cpp:1288-1303`, reported `3011-3025` |

And `address(0)` is genuinely reachable in the model. `src/c2goto/library/solidity/solidity_blockchain.c:1-20` states it as a design decision — *"msg.sender can be any address on every call"* — and `solidity_misc.c:123` / `:170` seed it with a bare `(address_t)nondet_uint()` with **no `__ESBMC_assume(msg_sender != 0)`**. Same for `tx_origin` (`:136`, `:172`) and `block_coinbase` (`:141`, `:202`). `block_timestamp` is only constrained non-decreasing (`solidity_misc.c:196-198`), never `> 0`.

The checked-in artifact confirms it fires: `CityToken.cov.t.sol:16` is `vm.startPrank(address(uint160(0)))`, and `CityToken` binds `ceoAddress = cooAddress = msg.sender` in its constructor (`contract.sol:104-107`). Every `test_cov_*` in `CityTokenCovTest_0` therefore runs against a contract whose CEO and COO are `address(0)` — an on-chain impossibility, and one that changes what `require(msg.sender == ceoAddress)` means.

---

## 4. Q4 — What `foundry_covgen_msgsender_not_pinned_knownbug` pins

Directory contents: `NOTES.md`, `test.desc`, `contract.sol` (CityToken, 513 lines), `contract.solast`, `CityToken.cov.t.sol` (generated snapshot, 145 lines), `test.out` (696 KB captured log — not read in full).

**What it pins** (`test.desc`, 7 lines):

```
CORE
contract.solast
--sol contract.sol --contract CityToken --branch-coverage-claims --generate-foundry-testcase --k-induction --base-k-step 2 --k-step 3 --max-k-step 50 --cvc5
^Generated Foundry coverage test with [0-9]+ case\(s\)
^Foundry: [0-9]+ call\(s\) with pinned msg.sender \(vm.prank\)
^  !\(!\(msg\.sender == ceoAddress \|\| msg\.sender == cooAddress\)\)
^  !\(!\(msg\.sender == cooAddress\)\)
```

Three of the four regexes are the authorised (guard-true) arms of `onlyCLevel` / `onlyCOO` in the `--branch-coverage-claims` reached list; the fourth proves at least one `vm.prank` was emitted. Counts are deliberately loose (`[0-9]+`) because the exact number follows solver model choices, and the pin line "is only printed when N > 0, so a zero-pin regression cannot match it" (`NOTES.md:16-18`).

**The known bug it names, and its status:** `NOTES.md:20` reads **"## The bug this covers (fixed)"**. The bug was in `foundry_generator::reconstruct`: it refused to pin the sender whenever *any* SSA step wrote the `msg_sender` global outside the per-tx reseed. That test was syntactic, and symex merges a branch-local write into an unconditional step whose RHS is `cond ? new : old`, so every `$transfer`/`$send`/`$call` wrapper appeared as a guard-true `msg_sender` write on **every** path. One `.transfer()` anywhere suppressed the pin for all 29 cases, including `setCEO`, which makes no call at all. The fix decides on the **model value** instead — a write leaving `msg_sender` equal to the tx's top-level sender does not shadow it. That fix is live at `foundry.cpp:1804-1826` (the `shadowed` computation) and `1866-1872` (`sender_dirty` only on a read taken while shadowed).

**What it does not pin, and this matters for the present audit:** the directory name still says `knownbug`, but the residual defect is *not* the one documented. The generated artifact it ships as its own evidence deploys under `address(0)` (`CityToken.cov.t.sol:16`) and passes six `address(0)` arguments that were never recovered. `test.desc` has no line that would notice either. Per `NOTES.md:22-25`, `testing_tool.py` treats every line after the argument line as a **required** regex and has no disallowed-pattern section, so a negative can only be expressed by pinning a mutually-exclusive positive — which is exactly the technique `foundry_covgen_bytes_arg_defaulted_knownbug/NOTES.md:20-25` documents and which the fix below reuses.

---

## 5. Q5 — The NAMED OBSTACLE shape, in three sentences

**Mark.** `goto_coveraget::named_obstacle_paths` (`goto_coverage.h:402-403`) records, per path and keyed by the same `(comment, location)` pair the whole census uses, the paths where the model and the EVM disagree; the header states the rule as an obligation, not a suggestion — *"a marked path must be excluded from the sibling set used for the stage-3 subtraction AND must not be turned into a test. Marking without excluding would be worthless."* (`goto_coverage.h:391-393`).

**Exclude.** `foundry.cpp:1662-1666` sets `path_named_obstacle` off the **refuted** claim (route-independent: the segment-attached check at `1631-1632` is guarded on `!segs.empty()` and the coverage-claim fallback at `2162-2225` has no segment), `foundry.cpp:2284-2286` stamps it onto every call of the case, and `foundry.cpp:2355-2360` refuses in `collect()` — **before** `test_cases.push_back`, deliberately, so a fingerprint collision cannot let a clean case absorb an obstructed one and ship it under clean provenance (`foundry.cpp:2346-2350`).

**Count on stdout.** `suppressed_obstacle` (`foundry.h:144-148`) is incremented at `foundry.cpp:2358` and printed by `generate()` at `foundry.cpp:2912-2919` as an **absolute number, before the `test_cases.empty()` early return** (`2921-2925`) — because a run that refused N counterexamples and a run that witnessed nothing would otherwise print the identical "no test cases collected" line, and those are opposite situations (`foundry.cpp:2902-2911`).

---

## Table: every place a CE value can be lost or replaced

| # | Site | file:line | What is lost | What the consumer of the artifact sees |
|---|---|---|---|---|
| 1 | `path_ce` harvest: nil lhs/value | `bmc.cpp:3230` | whole step | JSON only; not the emitter |
| 2 | `path_ce`: `_ESBMC_Nondet_Extcall*` | `bmc.cpp:3270-3277` | dispatcher choice bits | counted in `harness_nondets_dropped` |
| 3 | `path_ce`: non-scalar mapping/array write | `bmc.cpp:3311-3318` | the value | name listed in `state_written_value_unavailable` — **honest** |
| 4 | `path_ce`: **concreteness gate** | **`bmc.cpp:3323-3324`** | whole step | absent from JSON — **honest, claim verified** |
| 5 | `path_ce`: `get_nondet_symbol` nil | `bmc.cpp:3383-3385` | whole step | absent, and **not** counted in `dropped_internal` |
| 6 | `path_ce`: no bucket for a local | `bmc.cpp:3422-3433` | ext-call returns | `extcall_returns` always `[]`, with a reason string |
| 7 | Emitter: `format_sol_value` non-constant | `foundry.cpp:360-361` (+310, 337, 349, 352) | the model value | falls through to #8 |
| 8 | **Emitter: default substitution** | **`foundry.cpp:1351-1352`** | **the model value** | **`0` / `address(0)` / `false` / `new T[](4)` in the emitted call — indistinguishable from a real value** |
| 9 | Emitter: STRING filler, flag cleared | `foundry.cpp:1259-1266` | all content | `"aaa…"`; `defaulted` **forced false** at 1263 |
| 10 | Emitter: BYTES_DYN content + length clamp | `foundry.cpp:335-358` | content; length `>4096`→`32` | `hex"00…"`; `defaulted` false |
| 11 | Emitter: unresolvable overload | `foundry.cpp:1221-1227` | whole call | `// UNSUPPORTED` comment — honest |
| 12 | Emitter: unknown signature | `foundry.cpp:1233-1241` | whole call | `// UNSUPPORTED` — honest |
| 13 | Emitter: non-mockable contract arg | `foundry.cpp:1288-1303` | whole call | `// UNSUPPORTED` — honest |
| 14 | Emitter: segment with no method | `foundry.cpp:2133` | whole call | nothing emitted, **nothing counted** |
| 15 | **Emitter: all-default synthesised ctor** | **`foundry.cpp:2272-2274`** | every ctor arg | `new C(0, address(0))` in `setUp()`, **no guard, no report** |
| 16 | Emitter: non-remapped ctor, partial recovery | `foundry.cpp:2065-2098` | unrecovered ctor args | `new C(recovered, address(0))` — the 2085 guard needs `deploy_remapped` |
| 17 | Emitter: dedup on literal | `foundry.cpp:2381-2432` (2394) | distinct CEs sharing a default | one case; collapse visible only as a claim count |
| 18 | **Emitter: `address(0)` deployer** | `foundry.cpp:2520-2525` → `2736-2737` | — | `vm.startPrank(address(0))`; **owner becomes address(0)** |
| 19 | **Emitter: `address(0)` prank** | `foundry.cpp:2826-2831` | — | `vm.prank(address(0))` |
| 20 | **Emitter: `vm.warp(0)`** | `foundry.cpp:2815-2821`, `2511-2517` | — | `block.timestamp == 0` |
| 21 | Emitter: `tx.origin` / `block.coinbase` never pinned | — | the whole env dimension | replay runs under Foundry defaults, silently |

Rows 8, 15, 16, 18, 19, 20 are the ones that produce a **RED test on the unmodified contract**. Rows 4, 11, 12, 13 are the discipline the rest should match.

---

## Proposed fix, in the named-obstacle shape

### Part A — the class-(b) refusal (unambiguous; do this first)

**Detect.** Two predicates, evaluated where the pin is already decided, on the **rendered literal** (so there is no second stringification that can disagree with the emitter's):

1. `deployer && format_sol_value("ADDRESS", msg_sender) == "address(uint160(0))"` — decided at `foundry.cpp:2054-2058` (`attach_ctor_env`) and rendered at `2520-2525`.
2. `prank && format_sol_value("ADDRESS", msg_sender) == "address(uint160(0))"` — decided at `foundry.cpp:2142-2150`, rendered at `2826-2831`.
3. `warp && format_sol_value("UINT256", block_timestamp) == "0"` — decided at `2140` / `2049-2053`, rendered at `2815-2821` / `2511-2517`.

Store as `bool unreplayable_env` + `std::string unreplayable_reason` on `sol_call`, alongside `named_obstacle` (`foundry.h:78-96`). Set them in `reconstruct()` **after** all reconstruction routes finish, at `foundry.cpp:2281-2286`, next to the obstacle stamp — same reason given there: the mark must not depend on which route produced the calls.

**Refuse.** In `collect()`, immediately after the obstacle loop at `foundry.cpp:2355-2360`:

```cpp
  for (const auto &c : tc)
    if (c.unreplayable_env)
    {
      ++suppressed_unreplayable_env;
      unreplayable_reasons.insert(c.unreplayable_reason);
      return;
    }
```

Before `test_cases.push_back`, for the identical reason spelled out at `foundry.cpp:2346-2350`: a fingerprint does not carry the flag, so a clean case absorbing a poisoned one would ship it under clean provenance.

**Count on stdout.** In `generate()`, immediately after the obstacle warning at `foundry.cpp:2919` and **before** the `test_cases.empty()` check at `2921`:

```
WARNING: Foundry: N counterexample(s) REFUSED -- their environment cannot exist on
chain (msg.sender / deployer == address(0), or block.timestamp == 0). address(0)
cannot originate a transaction, so a test replaying one is RED on the UNMODIFIED
contract. The paths remain in the coverage denominator; what is refused is turning
them into tests. Reasons: <sorted reason list>
```

Absolute number, never a ratio — matching `foundry.cpp:2912-2919`.

**Root-cause companion (not a substitute).** The refusal is a backstop; the model is where the state should not exist. `src/c2goto/library/solidity/solidity_misc.c:123` and `:170` seed `msg_sender = (address_t)nondet_uint()` with no lower bound. Adding `__ESBMC_assume(msg_sender != 0)` there (and for `tx_origin`) removes a state the chain does not have. Do **both**: the assume stops manufacturing the value, the refusal makes any future reintroduction non-silent. Note the assume is a real semantic change and will move coverage numbers on `require(msg.sender != address(0))`-style guards — expected, and the correct direction, but it must be landed as its own measured change, not folded in.

### Part B — the class-(a) counter (report first, refuse later)

Row 8 cannot be turned straight into a refusal, and `foundry.cpp:3082-3086` says why: the reconstruction *cannot presently tell* "sliced because irrelevant" (a faithful default) from "relevant but unrecoverable" (a wrong test). Refusing all defaults would over-refuse; the existing note is right that the rule has to hang on whether the **type** has a recovery path.

So: add the counter that is currently missing, next to the two at `foundry.cpp:3104-3113`, keyed on `a.defaulted` for types that **do** have a recovery path (`UINT*`, `INT*`, `ADDRESS`, `ADDRESS_PAYABLE`, `BOOL`, `BYTES<N>`):

```
Foundry: N call(s) carry a DEFAULTED scalar argument (a type format_sol_value can
render, for which no model value was recovered; the emitted call exercises a
different input than the counterexample)
```

Also count `foundry.cpp:2272-2274`'s all-default synthesised deploys separately — they are the highest-risk instance, because a defaulted ctor arg reverts `setUp()` and takes the **whole suite** red, which is precisely the reasoning already written at `foundry.cpp:2080-2084` for the remapped case. That population size is what decides whether row 8 becomes a refusal.

### Part C — the regression, and how it fault-injects

Model it on `solidity_path_cov_foundry_obstacle_not_emitted` (which pins the marking number and the emission number **on one run** so neither can stay right by accident).

New directory `foundry_covgen_zero_sender_refused_fail/`, contract:

```solidity
contract Zs {
    address public owner;          // NOT set from msg.sender: stays address(0)
    uint256 public x;
    function only(uint256 v) public {
        require(msg.sender == owner);   // reachable ONLY with msg.sender == 0
        if (v > 1) x = 1;
    }
}
```

`owner` is never assigned, so the guard-true arm of `only` is reachable in the model **exclusively** under `msg_sender == 0` — the refusal is then deterministic and does not depend on which model value the solver happens to pick (which is what makes the CityToken artifact's `address(uint160(0))` a lucky observation rather than a pin).

`test.desc` pins three lines on one run:

```
^WARNING: Foundry: 1 counterexample\(s\) REFUSED -- their environment cannot exist on chain
^Generated Foundry coverage test with 1 case\(s\): Zs.cov.t.sol$
^Foundry: 1 of 1 case\(s\) name the obligation they were reconstructed from
```

Fault injection: delete the `if (c.unreplayable_env)` block in `collect()`. The REFUSED line disappears (regex 1 fails) **and** the guard-true case is emitted, so the count becomes `2 case(s)` (regex 2 fails). Exactly one test goes red, and it cannot go green by accident in either direction — over-refusing drops the count to `0 case(s)` and produces `WARNING: No Foundry test cases collected` instead, which is mutually exclusive with regex 2. `testing_tool.py` has no disallowed-pattern section (`foundry_covgen_bytes_arg_defaulted_knownbug/NOTES.md:22-25`), so this mutually-exclusive-positive construction is the only way to express the negative, and it is the construction the existing obstacle test already uses.

Pair it with `foundry_covgen_zero_sender_emitted_baseline_fail` on a contract where `owner = msg.sender` in the ctor and the solver is free to pick a nonzero sender, pinning `0 counterexample(s) REFUSED` is **not** printed and `1 case(s)` is — so an over-eager predicate is caught by a second test rather than by the same one.

---

## UNVERIFIED

1. **Where `named_obstacle_paths` is populated, and where the "NAMED OBSTACLE report above" referenced by `foundry.cpp:2918` is printed.** I read `goto_coverage.h` but not `goto_coverage.cpp` (292 KB); I have the map's contract and all three of its `foundry.cpp` readers, but no `file:line` for the marking site. Settled by: `src/goto-programs/goto_coverage.cpp`.
2. **Whether the `--generate-foundry-testcase` non-coverage path (`generate_single`, `foundry.cpp:3165-3190`, called from `bmc.cpp:186`) is also reachable with an obstacle case.** It calls `reconstruct` directly and **bypasses `collect()` entirely**, so it bypasses the existing obstacle refusal as well as any refusal added there. This looks like a real second hole in the *existing* mechanism, but I did not confirm that `generate_single` is reachable in a Solidity path-coverage configuration. Settled by: `src/esbmc/bmc.cpp` `error_trace()` call-site conditions plus the option wiring in `src/esbmc/esbmc_parseoptions.cpp`.
3. **The exact `msg_sender` value distribution.** `solidity_misc.c:123`/`:170` cast a 32-bit `nondet_uint()` into `address_t`, so top-level senders are drawn from `[0, 2^32-1]` while address *parameters* are full 160-bit (both appear in `CityToken.cov.t.sol`: `4294967295` for the deployer, `1461501637330902918203684832716283019655932542975 == 2^160-1` for arguments). I did not verify whether that narrowing is deliberate. Settled by: `src/c2goto/library/solidity/solidity_types.h` and the `nondet_uint` declaration it resolves to.
4. **`test.out` in the knownbug directory** (696 KB) was not read in full; my account of what that regression pins comes from `test.desc` and `NOTES.md`, which are the files the harness actually acts on.

---

# ADVERSARIAL VERIFICATION OF THE ABOVE (second, independent reader)

The report above is one reader's. Before building a refusal on it, a second
reader was asked to REFUTE its load-bearing claims, defaulting to "refuted"
where the evidence was not conclusive. Results, so nobody acts on the parts
that did not survive.

## Survived

| claim | verdict |
|---|---|
| an unrenderable value becomes a TYPE DEFAULT and is emitted as if it were the counterexample's own value (`foundry.cpp:1350-1354`) | CONFIRMED |
| `sol_arg::defaulted` is read in exactly ONE place, `foundry.cpp:2085-2089`, gated on `deploy_remapped && kv.first == ctor_ts_contract` | CONFIRMED |
| the method-call (`2132-2152`), `--function`/library (`2013-2018`) and coverage-claim-fallback (`2218-2223`) routes emit without consulting it | CONFIRMED |
| the synthesised constructor at `foundry.cpp:2272-2274` passes an EMPTY recovered map, with no guard and no report | CONFIRMED |
| the env guards test only non-emptiness so a zero passes (`2515`, `2524`, `2819`, `2829`) — while the msg.value guards three lines away DO exclude zero (`2533`, `2806`) | CONFIRMED |
| the model seeds `msg_sender` / `tx_origin` / `block_coinbase` from a bare `nondet_uint()` and never assumes non-zero; `block_timestamp` is seeded unconstrained at `solidity_misc.c:146` and only constrained non-decreasing afterwards | CONFIRMED |

## Did NOT survive — do not repeat these

1. **`generate_single` is NOT a hole in the existing named-obstacle refusal.**
   Flagged above as an unverified second bypass; it is unreachable in any
   coverage mode. Every coverage option sets `multi-property` unconditionally
   (`esbmc_parseoptions.cpp:3884, 3908, 3958, 3998, 4025, 4140`), and
   `generate_single`'s only call site sits inside `error_trace`, which
   `report_trace` reaches only under
   `if (!options.get_bool_option("multi-property"))` (`bmc.cpp:2177-2179`).
   Coverage collects through `foundry().collect(...)` (`bmc.cpp:3066`), which
   does go through the refusal.

2. **The `address(0)` and `warp(0)` rows do NOT produce a red test.** The
   summary table lists them among the rows that "produce a RED test on the
   unmodified contract". That does not follow, and this report's own model-side
   claim is why: with no `msg.sender != 0` and no `block.timestamp > 0` axiom,
   ESBMC and Foundry AGREE at those values, and the cheatcodes replay faithfully
   what the solver chose. Those tests are GREEN and UNREALISABLE, not red. Rows
   8/15/16 keep the red-test severity: a defaulted constructor argument can trip
   a constructor `require`, revert `setUp()`, and take the whole suite with it.

3. **`address(0)` is a string the emitter never emits for a sender.**
   `format_sol_value` always renders an address as `address(uint160(N))`
   (`foundry.cpp:370-371`); `address(0)` comes only from `default_sol_literal`
   (`:393-394`), which is never used for the sender. What is actually emitted is
   `vm.startPrank(address(uint160(0)))`, as `CityToken.cov.t.sol:16` shows.
   **A detector keyed on the literal `address(0)` for a sender would never
   fire** — the exact failure `foundry.cpp:92-95` already warns about. The
   proposed fix above uses the right string; the table does not.

4. Row 15 is narrower than stated: the route fires only for a PARAMETERISED
   constructor that does NOT read deploy-time env (otherwise the guarded route
   at `2107-2131` already pushed one and `has_ctor` blocks it), and is further
   blunted when a parameter is a non-mockable interface handle (`build_call`
   sets `supported = false` at `:1300`).

5. Two citation slips in §4: `test.desc` carries TWO claim-arm regexes, not
   three (lines 6-7 of a 7-line file; line 4 is the case count, line 5 the prank
   count); and the "no disallowed-pattern section" fact comes from
   `foundry_covgen_bytes_arg_defaulted_knownbug/NOTES.md:23-25`, not from the
   msgsender directory's NOTES.md.

6. **Open, and it bears on that regression's credibility:** the checked-in
   `foundry_covgen_msgsender_not_pinned_knownbug/CityToken.cov.t.sol` contains
   ZERO occurrences of `vm.prank(` — only `vm.startPrank` at 16 and 89 — while
   its `test.desc` requires a `Foundry: N call(s) with pinned msg.sender
   (vm.prank)` line, and `generate()` deliberately excludes constructor carriers
   from that counter (`foundry.cpp:3127`, `:3144`). Either the artifact is a
   stale pre-fix snapshot (29 `test_cov_*` functions, matching that directory's
   "all 29 cases"), or the descriptor is not currently satisfied by it, or the
   cases came through the coverage-claim fallback route (`2162-2225`), which
   attaches no env pins at all. Not decidable without running it. The
   both-renderings-on-one-parameter evidence at `CityToken.cov.t.sol:70`
   survives all three readings, because it comes from `build_call`.

## Provenance

Both halves were produced by subagents reading source only; nothing was built or
run. The reports were first committed by mistake as the agents' raw JSONL
transcripts rather than as their text, and replaced in the commit that added
this section. The lesson is one already written down in these notes: a file was
persisted without being read.
