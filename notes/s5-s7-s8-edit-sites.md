# S5 / S7 / S8 — the actual edit sites, and four places the plan was wrong

Produced by a full read of `goto_coverage.cpp` (8140 lines), `goto_coverage.h`
(1134), the driver (2844) and four regression fixtures. **Every line number
below was quoted from the working tree at the time of the read.** I have not
independently re-verified them line by line; they are to be checked against the
file when the edit is made, which is a full read anyway.

`notes/interval-input-scope-and-plan.md` §6 is the source plan. It is right
about the shape of all three changes and wrong about their extent in four ways
that each cost a crash or a false fixture.

## The note's line numbers cannot be offset-corrected

The drift is not uniform, so no constant works:

| note | actual | drift |
|---|---|---|
| `walk_fields` 550-579 | 890-919 | +340 |
| `coord_expressible` 2285-2345 | 2801-2861 | +516 |
| certify box parse 2697-2709 | 3216-3225 | +519 |
| `resolve_coord` 3324-3397 | 3982-4055 | +658 |
| outer-box probe guard 5348-5350 | 6042-6045 | +694 |
| TYPE RANGE publication 5098-5120 | 5793-5815 | +695 |
| certify `>=`/`<=` 5591-5593 | 6325-6327 | +734 |
| certify decimal-in-type 5557-5586 | 6291-6320 | +734 |

## S5 (bool) — EIGHT guard-building sites, not four

The note names 4. Every place a comparison is built over a coordinate:

| site | what | named by the note? |
|---|---|---|
| 6326-6327 | certify `>=`/`<=` | yes |
| 6332 | certify **hole** `!=` | **no** |
| 6043-6045 | outer-box probe | yes |
| 5929 | outer-box **pin** `!=` | **no** |
| 6590-6591 | stage-3 region `>=`/`<=` | **no** |
| 6595 | stage-3 region hole `!=` | **no** |
| **6951-6954** | **stage-3 ordering rungs — THE SIGABRT** | **no** |
| 6983-6986, 7015-7018 | stage-3 abs/delta bounds | **no** |

### The one that crashes

Stage 3 ALREADY handles bool correctly, and does it by asking
`coord_expressible`:

    6892:  const bool interval_ok = coord_expressible(vt, why);
    6893:  const bool equality_ok = interval_ok || is_bool_type(vt);
    6947:  if (!interval_ok) continue;      // skips the ordering rungs

So a bool state variable today gets `eq`/`ne` and is refused the ordering rungs
with a printed reason. **The moment `coord_expressible` accepts bool,
`interval_ok` becomes true, line 6947 stops firing, and 6951-6954 builds
`>=`/`<=`/`>`/`<` over a bool.** Widening the whitelist turns a deliberately
correct path into the crash the whitelist exists to prevent.

Also unsigned-only and therefore silently skipped for bool: the probe
out-of-type drop at 5989, so a driver value of `7` reaches
`constant_int2tc(bool, 7)` at 6043.

### Design points the note does not settle

* **Do not build `constant_int2tc` on bool at all** — use `gen_true_expr()` /
  `gen_false_expr()`, already used in this file at 1849, 4225, 7825. Whether
  `constant_int2tc(bool_type, ...)` is malformed at the SMT layer is
  **UNVERIFIED** (`src/solvers/smt/smt_conv.cpp` would settle it); the bool
  constant route sidesteps the question.
* **Collapse lo/hi/holes into an allowed set `S ⊆ {0,1}`** and emit
  `OR over v in S of equality2tc(...)`. This handles the interval and the holes
  in one edit. The two existing structural gates are already correct for bool:
  `lo > hi` is refused at 6167 and punched-empty at 6203 (`2 >= 2`).
* **`S == {0,1}` must still emit a conjunct.** Emitting nothing while
  `++bounds_emitted` runs at 6352 reproduces the "counter reads the spec, not
  the formula" defect this file already fixed at 6333-6340.
* **The outer box needs no new probe kind**: on `{0,1}`, `c <= 0 ≡ c == false`
  and `c >= 1 ≡ c == true`, so `report_outer_boxes`' tightening (1006-1039) and
  its type-range seed (984-991) are unchanged. Just restrict probe values.

### THE NOTE'S MUST-FLIP FOR S5 IS WRONG, and wrong in the dangerous direction

§6 says "`flag in [0,0]` must be REFUTED". **It cannot be.** On the
`flag == true` path that assumption admits no execution, so the non-vacuity
witness (6402-6456) is not refuted and `report_path_cov_certify` (592-612)
prints `RESULT: VACUOUS` and exits 1.

Writing the fixture to expect REFUTED would pin the wrong outcome — and the
natural way to make it pass is to disable the vacuity gate, which is the
false-certificate hole closed earlier this session. The correct table:

| box on `flag` | target | expected |
|---|---|---|
| `[1,1]` | the `flag==true` path | CERTIFIED, exit 0 |
| `[0,1]` | same | REFUTED, witness `flag=false` |
| `[0,0]` | same | **VACUOUS, exit 1** |
| `[0,0]` | the other path | CERTIFIED |

A second pair the note has no fixture for at all: a bool state variable under
`--path-cov-assert` must STILL emit only `eq`/`ne` after S5 and must not abort,
with a `uint256` twin that still gets all six rungs.

## S7 (signed) — FOUR type-validation copies, and five driver regexes

The note names one validation site. There are four:

* `path_cov_fits_type` **793-805** (`return v >= 0 && v <= tmax`)
* `path_cov_out_of_type_refusal` **808-825** (message hardcodes `[0, ...]`)
* certify's inline copy **6291-6320**
* the outer-box probe drop **5988-6005** (unsigned-only, so signed values wrap
  instead of being dropped — the `geometric-ladder-wraps-on-narrow-types` bug in
  the other branch)

The file itself flags the duplication at 697-703: "a fix to one copy does not
reach the other". Note also that 6303's `v >= 0` rejects a negative `lo` before
the type test is even reached, so the whole block changes, not just `tmax`.

**The driver would silently measure nothing.** Five digits-only patterns in
`scripts/solidity_path_generalise.py`: `TYPE_RANGE_RE` 733-734, `INTERVAL_RE`
740-741, `SHRINK_RE` 705, `PUNCH_PAIR_RE` 726, and the bracket scan at 782. A
published `TYPE RANGE [-578..., 578...]` matches none, so `type_ranges` stays
empty and `_span` (2433-2436) falls back to `(0, UINT256_MAX)`. `geometric_values`
(564-571) has no negative arm.

**`solidity_path_cov_certify_refuses_signed` must be REWRITTEN, not deleted** —
its `test.desc:4` pins the "SIGNED bit-vector" refusal, which becomes the
type-range refusal. Deleting it reopens the documented false certificate with
nothing pinning it.

**The `delta` rung (6988-7019) is unsound on signed even with its direction
conjunct**: `post = 2^255-1, pre = -2^255` satisfies `post >= pre` while the
subtraction is not representable. Refuse `delta` on signedbv or add a
no-overflow conjunct.

## S8 (`rel`) — the note's wrap warning is wrong, and the real hazard is driver-side

* Spec parse at **3216-3225**; malformed input is already fatal via the catch at
  3230-3242, so `rel` inherits that.
* The extra ASSUME goes after the box loop closes at 6353, as a second
  instruction of the same shape as 6343-6352. **`.property("skipped")` at 6349
  is load-bearing** — without it the decision-set census (4561-4568) reads it as
  a lowered-away branch and the unit becomes a named obstacle.
* **The note's wrap warning is WRONG for the certify side.** It says `rel` needs
  the delta rung's protective `a>=b` conjunct. That applies to MEASURING a
  difference (`a-b <= v`), which is the outer-box side the plan already
  excludes. A relational `rel` builds `greaterthanequal2tc(a, b)` directly — no
  subtraction, no wrap.
* **Vacuity is already covered.** An unsatisfiable `rel` produces no execution
  and the non-vacuity witness reports VACUOUS regardless of cause. The four
  syntactic gates (6162-6225) need no `rel` awareness.
* Width/sign mismatch between the two sides IS real; the in-file precedent for
  the typecast is `gen_not_eq_expr` at 7755-7759.

**THE REAL HAZARD IS THE DRIVER EXITING 1 ON A CORRECT RUN.** With a `rel` a
region is a strict subset of its product box, so two genuinely disjoint regions
can share a product point, `certified_overlap` fires and the driver hard-fails:

* `boxes_intersect` 1087-1122, `certified_overlap` 1125-1153, call site and
  `return 1` at 2785-2800.

This is the identical shape that function already suffered for holes, documented
at 1095-1107 ("ignoring them is a live FALSE ALARM ... a pair of perfectly
correct regions would kill the run"). The downgrade must be an explicit printed
statement, never a silent skip. Also: `certify()`'s spec build (1788-1792) must
serialise `rel`; `region_size` (1352-1373) becomes an upper bound and must be
labelled; `ce_in_region` (1320-1349) must also check the CE satisfies `rel`.

## Ordering constraint

S5 and S7 both widen `coord_expressible` (2804 / 2832) and therefore both switch
on stage-3's `interval_ok` (6893, 6947) and `path_cov_fits_type` (793-825). Land
whichever goes first TOGETHER with its 6893/6947 and 793-825 companions, or the
second inherits a branch that is already crashing. S8 touches neither and is
independent.
