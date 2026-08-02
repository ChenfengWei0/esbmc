#!/usr/bin/env python3
"""COUNT B -- the deliverable, read off the emitted .t.sol files themselves.

B is defined in WORKORDER §1 as a generated `.t.sol` satisfying all five of:

  1 the parameters are FUZZ parameters, not hardcoded values;
  2 `bound(...)` confines a parameter to an interval of WIDTH > 1;
  3 at least one `assert*` whose subject is the post-call state or the return
    value;
  4 `forge test` is GREEN on the UNMODIFIED contract;
  5 it comes from a REAL CORPUS contract, not a hand-written fixture.

WHY THIS READS THE FILE AND NOT put.json. put.json is what the driver BELIEVED
it emitted; the .t.sol is what exists. They agree today, and a counter built on
the driver's own bookkeeping would keep agreeing after the emitter changed --
which is the proxy-instead-of-deliverable mistake this project has already
made. It also finds files no driver run in this session produced: four
`transfer` PUTs were sitting in the farming project, green, and were absent
from every denominator anyone had counted.

GATE 3 IS SCOPED TO THE PUT'S OWN BODY. The emitted file also holds concrete
replay cases, and one of those routinely carries
`assertFalse(ok, "value sent to a non-payable entry must revert")` -- an
exit-kind expectation on a DIFFERENT test. Counting the file's asserts would
credit the PUT with an assertion that is not in it. The exit-kind comment
`// [asserted] path exits normally` is not an assert* either and is not counted.

Usage: count_b.py <forge-project> [<forge-project> ...]
"""
import json
import re
import subprocess
import sys
import os

PUT_RE = re.compile(r"^\s*function (test_put_\w+)\(([^)]*)\)\s*public\s*\{",
                    re.M)
BOUND_RE = re.compile(r"\bbound\(\s*[^,]+,\s*([0-9]+)\s*,\s*([0-9]+)\s*\)")
ASSERT_RE = re.compile(r"\bassert(?:Eq|True|False|Ge|Le|Gt|Lt|NotEq)\s*\(")


def put_body(text, start):
    """The PUT function's body, brace-matched from its opening brace."""
    i = text.index("{", start)
    depth, j = 0, i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
        j += 1
    return text[i:]


def forge_green(project):
    """{test file name: True/False} from one real `forge test` run.

    ⛔ A RUN THAT PRODUCED NO SUITE AT ALL IS A MEASUREMENT FAILURE, NOT A
    COLUMN OF FALSES. It is raised here rather than returned, because every
    caller of this function reads a missing entry as "not green" and would
    report `B = 0` -- a number, from a run that measured nothing.

    MEASURED, twice, and it is why this guard exists. WORKORDER §7 gives the
    daily acceptance as

        forge test --match-path 'bench/**/test/*.t.sol' -vv

    and from the repository root that prints

        Nothing to compile

    and exits 0. The root is not a Foundry project -- there is no foundry.toml
    above `bench/FeeVault` -- so forge compiles nothing, runs nothing, matches
    nothing, and reports success. A green acceptance that inspected zero tests
    is indistinguishable, from its exit code, from a green acceptance that
    inspected all of them. That is the empty-set-reads-as-no-restriction shape
    this project has already been bitten by from the other direction.

    THE RULING, made here because the acceptance has to be executable: B is
    measured PER FORGE PROJECT -- `count_b.py <project> [...]` -- and each
    project must yield at least one suite. WORKORDER is authoritative and is not
    edited; what is recorded is that its §7 command, run from the root, is not
    an executable form of it.
    """
    p = subprocess.run(["forge", "test", "-vv"], cwd=project,
                       capture_output=True, text=True, timeout=1800)
    out = p.stdout + p.stderr
    res = {}
    for line in out.splitlines():
        m = re.match(r"Ran \d+ tests? for (\S+):", line.strip())
        if m:
            cur = os.path.basename(m.group(1).split(":")[0])
        m = re.match(r"Suite result: (\w+)\.", line.strip())
        if m:
            res[cur] = (m.group(1) == "ok")
    if not res:
        raise SystemExit(
            f"FATAL: `forge test` in {project} produced NO suite result at "
            f"all, so gate 4 was not measured for any file. This is a failure "
            f"of the RUN, and it must not be reported as `B = 0` -- an "
            f"un-run gate and a failed gate are different facts. forge said:\n"
            + out)
    return res, out


def main():
    projects = sys.argv[1:]
    if not projects:
        print("usage: count_b.py <forge-project> [...]")
        return 2
    rows = []
    for proj in projects:
        tdir = os.path.join(proj, "test")
        if not os.path.isdir(tdir):
            print(f"FATAL: {proj} has no test/ directory")
            return 1
        green, _out = forge_green(proj)
        for fn in sorted(os.listdir(tdir)):
            if not fn.endswith(".t.sol"):
                continue
            text = open(os.path.join(tdir, fn)).read()
            m = PUT_RE.search(text)
            if not m:
                continue                      # not a PUT file at all
            params = [p for p in m.group(2).split(",") if p.strip()]
            body = put_body(text, m.start())
            widths = [(int(b) - int(a)) for a, b in BOUND_RE.findall(body)]
            g1 = len(params) >= 1
            g2 = any(w > 0 for w in widths)
            g3 = len(ASSERT_RE.findall(body))
            g4 = green.get(fn)
            rows.append((os.path.basename(proj.rstrip("/")), fn, m.group(1),
                         len(params), widths, g1, g2, g3, g4))

    print(f"{'project':<12} {'put':<44} {'par':>3} {'wide':>5} {'asrt':>5} "
          f"{'green':>6}  B    first failing gate")
    print("-" * 112)
    nB = 0
    for proj, fn, name, npar, widths, g1, g2, g3, g4 in rows:
        isB = bool(g1 and g2 and g3 >= 1 and g4 is True)
        nB += 1 if isB else 0
        # ---- WHICH GATE, AND ONLY WHAT THIS SCRIPT CAN SEE --------------------
        #
        # `no` in a column of `no`s reads as one kind of failure, and they are
        # not one kind. Two of the four gates fail for reasons NO change to this
        # pipeline can lift -- a unit the contract declares with no arguments can
        # never have a fuzz parameter -- while the others are about how far the
        # region search and the ladder got. Collapsing them inflates the
        # denominator of any conversion rate quoted off this table.
        #
        # ⛔ The reason is NOT diagnosed here beyond what the emitted text shows.
        # `par == 0` is read off the PUT's own signature, which is a fact; WHY
        # the unit has no parameter (a parameterless view, versus a region that
        # bounded none of them) is not in this file and is not guessed.
        why = ""
        if not isB:
            if not g1:
                why = ("gate1: the PUT takes no parameter -- a deterministic "
                       "point, not a fuzz test")
            elif not g2:
                why = "gate2: every bound() is a single value"
            elif g3 < 1:
                why = "gate3: no assert* over post-state or the return value"
            elif g4 is not True:
                why = ("gate4: the SUITE is not green" if g4 is False
                       else "gate4: this file produced no suite result")
        print(f"{proj:<12} {name[:44]:<44} {npar:>3} {str(g2):>5} {g3:>5} "
              f"{str(g4):>6}  {'YES' if isB else 'no':<4} {why}")
    print()
    print(f"B = {nB}   (of {len(rows)} emitted PUT file(s) inspected)")
    print()
    print("gate 1 fuzz parameters  : the column `par` -- 0 means the PUT takes "
          "none, i.e. it is a deterministic point of the region")
    print("gate 2 a bound wider    : `wide` -- False means every bound() is a "
          "single value, which is a concrete test with extra syntax")
    print("gate 3 an oracle assert : `asrt` counts assert* calls INSIDE the "
          "PUT body only; the concrete replay cases in the same file are not "
          "counted")
    print("gate 4 forge green      : the whole SUITE, from a real run -- a "
          "green PUT beside a red concrete case is a red file")
    print("gate 5 corpus contract  : judged by the project, listed above")
    return 0


if __name__ == "__main__":
    sys.exit(main())
