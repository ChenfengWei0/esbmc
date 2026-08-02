#!/usr/bin/env python3
"""Does each counterexample payload actually WALK the path it is filed under?

THE PROPOSITION THIS TURNS INTO A RUNTIME CHECK. A claim in cov-report.json
carries two things that must agree: a DECISION SEQUENCE (`decisions`, each with a
`branch_claim` and the `arm` taken) and a PAYLOAD (`inputs`, `entry_storage`,
`env`). If the payload is substituted into the sequence, every decision must come
out the way `arm` says. Nothing enforced that, and the first contract it was run
on breaks it.

MEASURED on notes/coverage/poc/Tiny2.sol, `--solidity-max-tx 1`:

    deposit:path:2   decisions: msg.value == 0 [fall-through / ABI gate]
                     env:       msg.value = 0
    deposit:path:6   decisions: !(msg.value == 0) [taken / ABI gate] -> ...
                     env:       msg.value = 0

Those two paths differ on exactly one thing -- whether value was sent to a
nonpayable entry -- and the payload says `msg.value = 0` for BOTH. `path:2` is
depth 1 and exits `revert`: it IS the "value sent to a nonpayable function"
path, so its own payload states the condition under which it is not taken.

The consequence is not a missing test, it is a WRONG one. The emitter renders
both as the same call, `deposit(0)`, and labels the single case with both path
ids -- so a test that can only ever walk one path is recorded as covering two.
That inflates the numerator of the very ratio this pipeline exists to report, and
no stage downstream can notice, because every stage downstream reads the payload
and not the path.

WHAT IT CAN AND CANNOT DECIDE, stated rather than implied. `branch_claim` is a
source-level predicate string. This evaluates the shapes it can parse with
certainty -- comparisons and equalities between a name and an integer, and
negation -- and REFUSES the rest. A predicate it cannot parse is counted as
SKIPPED and printed; it is never counted as consistent. A checker that silently
passed what it did not understand would be the always-true reader this repository
has already shipped once.

Usage:
    ce_consistency.py <cov-report.json> [more.json ...]
"""
import json
import re
import sys
from pathlib import Path

# `name <op> integer`, either order, decimal or 0x.
CMP = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*(==|!=|>=|<=|>|<)\s*"
                 r"(0[xX][0-9a-fA-F]+|\d+)\s*$")
CMP_REV = re.compile(r"^\s*(0[xX][0-9a-fA-F]+|\d+)\s*(==|!=|>=|<=|>|<)\s*"
                     r"([A-Za-z_][\w.]*)\s*$")
# `name <op> name`
CMP_NN = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*(==|!=|>=|<=|>|<)\s*"
                    r"([A-Za-z_][\w.]*)\s*$")

FLIP = {">": "<", "<": ">", ">=": "<=", "<=": ">=", "==": "==", "!=": "!="}


def strip_nots(s):
    """Peel `!(...)` wrappers, returning (core, negation_count)."""
    n = 0
    s = s.strip()
    while s.startswith("!(") and s.endswith(")"):
        # only peel when the parens are balanced across the whole body
        depth = 0
        ok = True
        body = s[2:-1]
        for ch in body:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    ok = False
                    break
        if not ok or depth != 0:
            break
        s = body.strip()
        n += 1
    return s, n


def as_int(tok):
    return int(tok, 16) if tok.lower().startswith("0x") else int(tok)


def unit_simple_name(claim):
    """`sol:@C@C@F@tern_call#92` -> `tern_call`. None when unrecognisable."""
    pf = claim.get("path_function") or ""
    if "@F@" not in pf:
        return None
    tail = pf.split("@F@", 1)[1]
    return tail.split("#", 1)[0] or None


def lookup(name, claim, allow_inputs=True):
    """Resolve a source-level name against the payload. Returns (value, where)
    or (None, None) when the payload does not mention it -- which is itself
    worth printing, because a decision on a name the payload never bound is a
    path whose test cannot be reconstructed.

    `allow_inputs=False` is passed for a decision that lives in an INLINED
    CALLEE. `inputs` binds the UNIT's parameters; the callee's own parameters
    are different variables that merely may share a name, and binding them by
    bare name is how this checker produced its one DISAGREE (see the header of
    the patch that introduced this). `entry_storage` and `env` stay available
    because a state variable and the environment mean the same thing wherever
    the decision sits.
    """
    buckets = ("inputs", "entry_storage", "env") if allow_inputs \
        else ("entry_storage", "env")
    for bucket in buckets:
        d = claim.get(bucket) or {}
        if name in d:
            v = d[name]
            try:
                return as_int(str(v)), bucket
            except ValueError:
                return None, bucket
    return None, None


def written_on_this_path(name, claim):
    """Did this path WRITE the state variable `name` before the decision?

    ---- WHY A DECISION ON A WRITTEN STATE VARIABLE IS NOT JUDGEABLE ----

    `entry_storage` binds the value at ENTRY. A decision that reads a state
    variable the same path has already assigned reads a LATER version, and the
    payload publishes no per-decision snapshot, so binding it to the entry value
    judges a different quantity that happens to share a name.

    MEASURED, and it was this checker's only DISAGREE across the whole PoC set:

        contract D16_OnlyByOverflow {
            uint256 public bal;                 // constructor sets 500
            function add(uint256 amt) external {
                bal += amt;                     //  <-- written HERE
                if (bal < 500) { flag = 1; }    //  <-- decision reads the NEW bal
            }
        }

        add:path:6   inputs {amt: 2^256-4}   entry_storage.bal 500
                     -> bal wraps to 496, `bal < 500` is TRUE, the `if` body IS
                        the edge taken, and the payload is CORRECT.

    Evaluated against the entry 500 the guard comes out the other way, and the
    checker reported the payload as not walking its own path -- failing the gate
    that stands between the funnel's numbers and being quotable. The payload was
    right and the reader was binding the wrong version: the same shape as every
    other reader-side defect in this tree.

    ⚠ ONE-SIDED, AND THAT IS DELIBERATE. `entry != final` PROVES the variable was
    written, so refusing there is sound. `entry == final` does NOT prove it was
    never written -- a path may write it and write it back -- so this returns
    False there and the decision is still judged. The refusal covers the case we
    can establish, and does not pretend to cover the one we cannot.
    """
    entry = claim.get("entry_storage") or {}
    final = claim.get("final_state") or {}
    if name not in entry or name not in final:
        return False
    return str(entry[name]) != str(final[name])


def evaluate(pred, claim, allow_inputs=True):
    """Return (True/False, None) if decidable, else (None, reason)."""
    core, nots = strip_nots(pred)
    m = CMP.match(core)
    rev = False
    if not m:
        m = CMP_REV.match(core)
        rev = bool(m)
    if m:
        if rev:
            lit, op, name = m.group(1), m.group(2), m.group(3)
            op = FLIP[op]
        else:
            name, op, lit = m.group(1), m.group(2), m.group(3)
        val, where = lookup(name, claim, allow_inputs)
        if val is None:
            return None, f"payload does not bind `{name}`"
        if where == "entry_storage" and written_on_this_path(name, claim):
            return None, f"state variable `{name}` is written on this path"
        rhs = as_int(lit)
    else:
        m = CMP_NN.match(core)
        if not m:
            return None, f"unparsed predicate `{core}`"
        ln, op, rn = m.group(1), m.group(2), m.group(3)
        val, lwhere = lookup(ln, claim, allow_inputs)
        rhs, rwhere = lookup(rn, claim, allow_inputs)
        if val is None:
            return None, f"payload does not bind `{ln}`"
        if rhs is None:
            return None, f"payload does not bind `{rn}`"
        for nm, wh in ((ln, lwhere), (rn, rwhere)):
            if wh == "entry_storage" and written_on_this_path(nm, claim):
                return None, f"state variable `{nm}` is written on this path"
    res = {"==": val == rhs, "!=": val != rhs, ">": val > rhs,
           "<": val < rhs, ">=": val >= rhs, "<=": val <= rhs}[op]
    if nots % 2:
        res = not res
    return res, None


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    total = agree = disagree = skipped = 0
    bad = []
    skip_reasons = {}

    def note_skip(reason):
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
    for p in sys.argv[1:]:
        rp = Path(p)
        if not rp.exists():
            print(f"MISSING {p}")
            continue
        d = json.loads(rp.read_text())
        for c in d.get("claims", []):
            if c.get("status") != "F":
                continue           # only a witnessed path has a payload
            for i, dec in enumerate(c.get("decisions", [])):
                pred = dec.get("branch_claim")
                arm = dec.get("arm")
                if pred is None or arm is None:
                    skipped += 1
                    note_skip("the decision record has no branch_claim or arm")
                    continue
                total += 1
                # ---- THE CONVENTION, ESTABLISHED AGAINST SOURCE ----
                #
                # `branch_claim` is the PROBE GUARD, and a probe guard is FALSE
                # on the edge the execution actually took (goto_coverage.cpp
                # `insert_swap`: `assert(guard)` covers fall-through and
                # `assert(!guard)` covers goto-taken, so the string printed is
                # the one the witnessing execution REFUTES). `arm` is already
                # baked into the string -- the two arms of one source decision
                # appear as `P` and `!(P)` -- so `arm` is descriptive here and
                # the check does not consult it.
                #
                # This was got backwards first, and the mistake was legible
                # precisely because it flipped nearly everything: 15 of 19
                # decisions "disagreed" on a contract whose tests mostly pass.
                # A checker whose verdict is wrong for every row is reporting
                # its own convention, not the data. Pinned against Tiny2, whose
                # arithmetic makes the direction unambiguous:
                #
                #   withdraw:path:31  amt=100, bal 500 -> 499  (the `else` arm,
                #                     `bal -= 1`)   decision `amt > 100`
                #                     => the guard `amt > 100` is FALSE. ok.
                #   withdraw:path:30  amt=500, bal 500 -> 0    (the `then` arm,
                #                     `bal -= amt`) decision `!(amt > 100)`
                #                     => the guard `!(amt > 100)` is FALSE. ok.
                #
                # Both arms of the same source decision, both consistent, and
                # the final-state arithmetic independently says which arm ran.
                want = False
                # A DECISION INSIDE AN INLINED CALLEE DOES NOT SPEAK THE
                # UNIT'S NAMES. Internal calls are physically inlined before
                # enumeration, so a callee's `if (y > 200)` arrives here with
                # `function: leaf2` while `inputs` binds the UNIT's `y`. Those
                # are different variables that happen to share a spelling, and
                # resolving one against the other is what produced this
                # checker's only DISAGREE on a payload that was correct.
                unit = unit_simple_name(c)
                dfn = dec.get("function")
                in_callee = bool(unit and dfn and dfn != unit)
                got, why = evaluate(pred, c, allow_inputs=not in_callee)
                if got is None:
                    skipped += 1
                    # Name the SHAPE, not the instance: bucketing on the raw
                    # predicate text would give one bucket per contract and
                    # defeat the point.
                    #
                    # The bytes helper gets its own bucket because it was once
                    # proposed as the acceptance criterion for the bytesN
                    # payload defect ("a correct fix must move these OUT of
                    # skipped"). That criterion was wrong and is recorded here
                    # so it is not re-proposed: the defect was an aux-ordering
                    # bug in the frontend, it IS fixed, and these decisions are
                    # still refused -- because the refusal is about the
                    # CHECKER's inputs, not about the payload. `branch_claim`
                    # here is the LOWERED form, a call's return value, and the
                    # report carries no operands for it, so nothing in this
                    # file could ever decide it. Moving them out needs the
                    # report to publish the source-level predicate
                    # (`b == bytes32(uint256(1))`) instead of the lowering.
                    if "_bytes_static_equal" in pred:
                        note_skip("bytesN equality lowered to a helper call "
                                  "(`_bytes_static_equal`); the report "
                                  "publishes no operands for it, so this "
                                  "checker cannot decide it either way")
                    elif "return_value$" in pred:
                        note_skip("a call's return value, not a payload name")
                    elif in_callee and why and \
                            why.startswith("payload does not bind"):
                        note_skip("a decision inside an INLINED CALLEE reading "
                                  "a name that is the callee's own local; the "
                                  "report publishes no argument mapping, so "
                                  "this checker cannot bind it and must not "
                                  "guess from the caller's payload")
                    elif why and why.startswith("state variable"):
                        # ITS OWN BUCKET, because it is a different fact from
                        # every other refusal here: the payload DOES bind the
                        # name, and binds it correctly -- for ENTRY. The
                        # decision reads a later version and the report
                        # publishes no per-decision snapshot. Folded into
                        # "payload does not bind", a fix that made the report
                        # publish those snapshots would be invisible.
                        note_skip("the decision reads a state variable this "
                                  "path WRITES before it; `entry_storage` "
                                  "binds the entry version and the report "
                                  "publishes no per-decision snapshot, so this "
                                  "checker cannot bind the version the guard "
                                  "sees")
                    elif why and why.startswith("payload does not bind"):
                        note_skip("payload does not bind a name the decision "
                                  "reads")
                    else:
                        note_skip("predicate shape this checker cannot "
                                  "evaluate")
                    continue
                if got == want:
                    agree += 1
                else:
                    disagree += 1
                    bad.append((p, c.get("condition"), i, pred, arm,
                                dict(c.get("inputs") or {}),
                                (c.get("env") or {}).get("msg.value"),
                                dec.get("synthetic_abi_gate", False)))

    print("## Payload-vs-path consistency\n")
    print(f"  decisions evaluated  {total}")
    print(f"  agree                {agree}")
    print(f"  DISAGREE             {disagree}")
    print(f"  skipped (unparsed / name not in payload)  {skipped}")
    # ---- WHY EACH SKIP HAPPENED, GROUPED ----
    #
    # A skipped decision is the checker refusing to judge, and an anonymous
    # refusal count cannot be used as an acceptance criterion. Grouping them by
    # reason makes "did this fix move THOSE decisions out of skipped?" a
    # question the output answers.
    #
    # It is not cosmetic here: the bytesN payload defect (D11_Bytes32Equality)
    # is pinned two ways, and one of the two is "these decisions must leave
    # `skipped` without producing a DISAGREE". Without the grouping, a fix that
    # moved six unrelated predicates out and left the bytes ones in would look
    # identical.
    if skip_reasons:
        print("\n  refusals by reason:")
        for r, n in sorted(skip_reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {n:>4}  {r}")
    print("\n  A skipped decision is NOT a passing one. It is a predicate this "
          "checker refuses to judge, and it is counted so the agree figure "
          "cannot be read as coverage of the whole set.\n")
    if bad:
        print("## Every disagreement, in full\n")
        for f, cond, i, pred, arm, inputs, mv, gate in bad:
            tag = "  [SYNTHETIC ABI VALUE GATE]" if gate else ""
            print(f"  {cond}  decision #{i}{tag}")
            print(f"      branch_claim  {pred}")
            print(f"      arm           {arm}   (the guard must evaluate "
                  f"FALSE on the edge actually taken; it evaluates TRUE)")
            print(f"      inputs        {inputs}")
            print(f"      env.msg.value {mv}")
            print(f"      report        {f}\n")
    return 1 if disagree else 0


if __name__ == "__main__":
    sys.exit(main())
