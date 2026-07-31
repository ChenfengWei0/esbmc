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


def lookup(name, claim):
    """Resolve a source-level name against the payload. Returns (value, where)
    or (None, None) when the payload does not mention it -- which is itself
    worth printing, because a decision on a name the payload never bound is a
    path whose test cannot be reconstructed."""
    for bucket in ("inputs", "entry_storage", "env"):
        d = claim.get(bucket) or {}
        if name in d:
            v = d[name]
            try:
                return as_int(str(v)), bucket
            except ValueError:
                return None, bucket
    return None, None


def evaluate(pred, claim):
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
        val, _ = lookup(name, claim)
        if val is None:
            return None, f"payload does not bind `{name}`"
        rhs = as_int(lit)
    else:
        m = CMP_NN.match(core)
        if not m:
            return None, f"unparsed predicate `{core}`"
        ln, op, rn = m.group(1), m.group(2), m.group(3)
        val, _ = lookup(ln, claim)
        rhs, _ = lookup(rn, claim)
        if val is None:
            return None, f"payload does not bind `{ln}`"
        if rhs is None:
            return None, f"payload does not bind `{rn}`"
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
                got, why = evaluate(pred, c)
                if got is None:
                    skipped += 1
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
