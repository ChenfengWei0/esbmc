#!/usr/bin/env python3
"""Deliverable B over EVERY corpus PUT project on disk, per ARM, never summed.

WHY THIS EXISTS BESIDE put_all.py's own gate. That one reports B for the
regions of ONE cert file, so it can only ever see the arm it was pointed at. A
question like "did the change-rung fix turn any existing PUT from red to green"
is about every arm at once, and until now answering it meant editing a
hardcoded list -- `b_from_disk.py` names exactly ["aqua_Aqua", "farming"] and
was written before a third project existed. That is the "sweep scripts hardcode
their input" failure: the answer silently becomes "B over whichever subset an
old list happens to name".

So this takes NO list. It walks put_roundtrip/ and treats every directory with
a foundry.toml and a test/ as a project, then reports each ARM separately.

WHAT AN ARM IS. put_all.py gives a non-default cert file its own project,
suffixed with the cert file's name (`farming__results_envsender_shrink8`). Two
arms' PUT counts must never be summed -- they are different measurements of
different regions -- so they are printed as separate blocks with separate
totals and no grand total is emitted.

GATE 5 (corpus, not a hand-written fixture) is decided by the project's SOURCE,
not by its name: a project whose src/ holds a corpus flat is a corpus project.
bench/FeeVault and the shared `poc` project are hand-written and are reported
in their own section, explicitly NOT counted.

Runs forge, reads the emitted text. No esbmc.
"""
import json
import os
import re
import subprocess
import sys

RT = "/home/samson/workspace/esbmc/notes/coverage/put_roundtrip"
PUT_BANNER = "===================== PUT (stage 4) ====================="
SIG = re.compile(r"function\s+(test_put_\w+)\s*\(([^)]*)\)")
BOUND = re.compile(r"\bbound\s*\(")
# The corpus flats, by the name put_all.py copies into src/. A project whose
# src/ holds one of these is a corpus project; anything else is not.
CORPUS_FLATS = {
    "aqua__Aqua.flat.sol",
    "cross-chain-swap__EscrowSrc.flat.sol",
    "cross-chain-swap__EscrowDst.flat.sol",
    "farming__FarmingPool.flat.sol",
    "st1inch__St1inch.flat.sol",
    "limit-order-protocol__MakerTraitsLib.flat.sol",
}


def balanced(text, start):
    d = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            d += 1
        elif text[i] == ")":
            d -= 1
            if d == 0:
                return text[start:i + 1]
    return text[start:]


def top_level_commas(inner):
    out, d, cur = [], 0, ""
    for ch in inner:
        if ch in "([":
            d += 1
        elif ch in ")]":
            d -= 1
        if ch == "," and d == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [a.strip() for a in out]


def as_int(tok):
    m = re.fullmatch(r"(?:uint\d*|int\d*|address)?\(?\s*(\d+)\s*\)?", tok.strip())
    return int(m.group(1)) if m else None


def arm_of(project):
    """`<bench>` or `<bench>__<cert file stem>`. The part after the first `__`
    that names a cert file is the arm; everything else is the default arm."""
    for marker in ("__results_", "__poc_results_"):
        if marker in project:
            return project.split(marker, 1)[1]
    return "default"


def projects():
    out = []
    for name in sorted(os.listdir(RT)):
        p = os.path.join(RT, name)
        if not os.path.isdir(p):
            continue
        if not os.path.exists(os.path.join(p, "foundry.toml")):
            continue
        tdir = os.path.join(p, "test")
        if not os.path.isdir(tdir):
            continue
        srcs = set(os.listdir(os.path.join(p, "src"))) \
            if os.path.isdir(os.path.join(p, "src")) else set()
        out.append((name, p, bool(srcs & CORPUS_FLATS)))
    return out


def forge_verdicts(proj):
    """{test function name: status}. A project that will not compile makes
    every one of its rows UNKNOWN, which is NOT a pass."""
    p = subprocess.run(["forge", "test", "--json"], cwd=proj,
                       capture_output=True, text=True)
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return None
    v = {}
    for suite in data.values():
        for name, res in (suite.get("test_results") or {}).items():
            v[name.split("(")[0]] = res.get("status")
    return v


def main():
    by_arm = {}
    noncorpus = []
    for name, proj, is_corpus in projects():
        verdicts = forge_verdicts(proj)
        tdir = os.path.join(proj, "test")
        for fn in sorted(os.listdir(tdir)):
            if not fn.endswith(".t.sol"):
                continue
            with open(os.path.join(tdir, fn), errors="replace") as f:
                text = f.read()
            if PUT_BANNER not in text:
                row = (name, fn, None, None, None, None, "no PUT section")
            else:
                put = text.split(PUT_BANNER, 1)[1]
                m = SIG.search(put)
                if m is None:
                    row = (name, fn, None, None, None, None,
                           "PUT section with no test_put_ signature")
                else:
                    tname, argtxt = m.group(1), m.group(2).strip()
                    args = [a.strip() for a in argtxt.split(",") if a.strip()]
                    body = put[m.end():]
                    widths = []
                    for bm in BOUND.finditer(body):
                        call = balanced(body, bm.end() - 1)
                        a = top_level_commas(call[1:-1])
                        if len(a) == 3:
                            lo, hi = as_int(a[1]), as_int(a[2])
                            if lo is not None and hi is not None:
                                widths.append(hi - lo)
                    asserts = [ln for ln in body.splitlines()
                               if re.match(r"\s*assert\w*\(", ln)]
                    g1 = len(args) >= 1
                    g2 = any(w > 1 for w in widths)
                    g3 = len(asserts) >= 1
                    st = None if verdicts is None else verdicts.get(tname)
                    g4 = st == "Success"
                    row = (name, fn, g1, g2, g3, len(asserts),
                           "?" if st is None else st)
            if is_corpus:
                by_arm.setdefault(arm_of(name), []).append(row)
            else:
                noncorpus.append(row)

    print("# Deliverable B, every corpus PUT on disk, PER ARM")
    print()
    print("Two arms are two measurements of two different region sets. They are")
    print("NOT summed, and no grand total is printed.")
    print()
    for arm in sorted(by_arm):
        rows = by_arm[arm]
        print(f"## arm: {arm}")
        print(f"{'file':<58}{'p':>3}{'w':>3}{'a':>4}{'forge':>10}  B")
        n = 0
        for _proj, fn, g1, g2, g3, na, st in rows:
            isb = bool(g1 and g2 and g3 and st == "Success")
            n += 1 if isb else 0
            print(f"{fn:<58}{str(g1)[:3]:>3}{str(g2)[:3]:>3}{str(na):>4}"
                  f"{str(st):>10}  {'YES' if isb else 'no'}")
        print(f"  B = {n} of {len(rows)} corpus PUT file(s) in this arm")
        print()
    if noncorpus:
        print("## NOT COUNTED -- hand-written fixtures, gate 5 fails by "
              "construction")
        for _proj, fn, _g1, _g2, _g3, _na, st in noncorpus:
            print(f"  {fn:<58}{str(st):>10}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
