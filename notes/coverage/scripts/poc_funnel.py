#!/usr/bin/env python3
"""THE FUNNEL, measured on the hand-written PoC set:

    instrumented path  ->  F (a counterexample exists)
                       ->  rendered as a Foundry case
                       ->  the suite COMPILES
                       ->  the case is GREEN on the unmodified contract

The order matters and it is not mine. Generalising a counterexample into a
region, and proving assertions over that region, are both built on top of a
counterexample that has already become a test that runs. If the first two stages
lose half each, everything downstream is operating on a quarter of the paths,
however good it is. So this script measures the first two and nothing else.

WHY THE LAST COLUMN IS `forge test` AND NOT ANYTHING THE VERIFIER PRINTS. A path
reported `F` has a counterexample IN THE MODEL. The deliverable is a test that is
GREEN on the real contract, and the two come apart in both directions: the model
gives an external call a nondet return and may choose success where the chain
reverts, and a rendered call may carry a DEFAULTED argument the model never
constrained. Neither is visible in cov-report.json. Only running it is.

WHAT EACH DROP MEANS -- they are different defects and must not be summed:

    paths -> F          the solver found no witness, or was never asked. Split
                        out by the report's own U reason tokens, because
                        `bounded-holds` (asked, no witness at this bound),
                        `unit-not-entered` (the harness never got there) and
                        `claim-budget-exceeded` (abandoned at 120 s) call for
                        three different fixes.
    F -> rendered       the emitter had a counterexample and could not turn it
                        into a call: an unsupported argument type, a body that
                        reconstructs to a constructor only, or a NAMED OBSTACLE.
                        The run's own log says which; it is quoted, not guessed.
    rendered -> builds  the emitted Solidity does not compile. Always a defect.
    builds -> GREEN     it compiles and fails on the contract it came from. This
                        is the one that would silently inflate every downstream
                        number if it were not measured.

ONE forge project for the whole set, not one per contract: `forge build`
dominates the wall clock and running it 30 times would make the measurement cost
more than everything it measures.

Usage:
    poc_funnel.py [--tx 1] [--only D01_StringState,Tiny] [--timeout 120]
                  [--keep]
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path("/home/samson/workspace/esbmc")
ESBMC = REPO / "build/src/esbmc/esbmc"
POC = REPO / "notes/coverage/poc"
FORGE_STD = (REPO / "notes/coverage-comparison/_foundry_roundtrip/aqua_forge"
             / "lib" / "forge-std")
OUT = REPO / "notes/coverage/poc_funnel"

FOUNDRY_TOML = """[profile.default]
src = "src"
test = "test"
libs = ["lib"]
# No solc pin: each PoC carries its own pragma and forge should satisfy it.
"""


def sh(cmd, cwd=None, timeout=300):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, cwd=cwd, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            out, _ = p.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out = ""
        return -1, out


def provenance():
    _, head = sh(["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO))
    _, dirty = sh(["git", "status", "--porcelain", "--", "src/"], cwd=str(REPO))
    bstat = ESBMC.stat().st_mtime if ESBMC.exists() else 0
    print("## Provenance\n")
    print(f"  HEAD          {head.strip()}")
    print(f"  binary mtime  "
          f"{time.strftime('%H:%M:%S', time.localtime(bstat))}")
    changed = [ln for ln in dirty.splitlines() if ln.strip()]
    if changed:
        print(f"  ** src/ HAS {len(changed)} UNCOMMITTED CHANGE(S) -- these "
              f"results correspond to no commit **")
        for ln in changed:
            print(f"      {ln}")
    else:
        print("  src/ clean")
    print()


def contract_name(path):
    """The contract under test: the LAST top-level `contract X` in the file,
    because the derived / using-for PoCs put it there."""
    name = None
    for ln in path.read_text().splitlines():
        s = ln.strip()
        if s.startswith("contract "):
            name = s.split()[1].split("{")[0].split("(")[0].strip()
    return name


def emit_reasons(out):
    """The EMITTER's own words for why a counterexample produced no case.
    Quoted from the log rather than inferred, because "no case" has at least
    four causes and they need four different fixes."""
    r = {}
    # findall, not search. The emitter prints this line ONCE PER GENERATED TEST
    # CONTRACT, and it generates more than one whenever two paths need
    # different CONSTRUCTOR-time `msg.sender` (measured: P07_Sender,
    # D02_StructWithMapping). Reading only the first made P21_ExternalCall
    # report `cases=7` against `F=5` -- more rendered cases than
    # counterexamples, which is impossible and was the signal that this parse
    # was wrong.
    ns = [int(x) for x in
          re.findall(r"Generated Foundry coverage test with (\d+) case", out)]
    if ns:
        r["cases"] = sum(ns)
        r["test_contracts"] = len(ns)
    m = re.search(r"Foundry: (\d+) counterexample\(s\) REFUSED -- every "
                  r"call they reconstructed is a CONSTRUCTOR", out)
    if m:
        r["refused_ctor_only"] = int(m.group(1))
    m = re.search(r"Foundry: (\d+) counterexample\(s\) REFUSED -- their "
                  r"path is a NAMED OBSTACLE", out)
    if m:
        r["refused_obstacle"] = int(m.group(1))
    m = re.search(r"Foundry: (\d+) call\(s\) carry (\d+) DEFAULTED "
                  r"argument\(s\) \(([^)]*)\)", out)
    if m:
        r["defaulted_calls"] = int(m.group(1))
        r["defaulted_args"] = int(m.group(2))
        r["defaulted_types"] = m.group(3)
    if "UNSUPPORTED" in out:
        r["mentions_unsupported"] = True
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tx", default="1")
    ap.add_argument("--only", default="")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--memlimit", default="4g")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    only = {s.strip() for s in a.only.split(",") if s.strip()}

    if shutil.which("forge") is None:
        sys.exit("forge is not on PATH -- the last two columns ARE the point "
                 "of this script, and reporting the first three alone would "
                 "look like a measurement of the funnel")
    if not FORGE_STD.exists():
        sys.exit(f"missing forge-std at {FORGE_STD}")

    provenance()

    proj = OUT
    if proj.exists() and not a.keep:
        shutil.rmtree(proj)
    (proj / "src").mkdir(parents=True, exist_ok=True)
    (proj / "test").mkdir(parents=True, exist_ok=True)
    (proj / "lib").mkdir(parents=True, exist_ok=True)
    (proj / "foundry.toml").write_text(FOUNDRY_TOML)
    if not (proj / "lib" / "forge-std").exists():
        os.symlink(FORGE_STD, proj / "lib" / "forge-std")

    rows = []
    # test-contract name -> PoC stem, so `forge test` output maps back
    owner = {}
    for sol in sorted(POC.glob("*.sol")):
        if only and sol.stem not in only:
            continue
        cname = contract_name(sol)
        row = {"stem": sol.stem, "contract": cname}
        if cname is None:
            row["err"] = "no contract declared"
            rows.append(row)
            continue

        rc, out = sh(["solc", "--ast-compact-json", str(sol)], timeout=120)
        if rc != 0:
            row["err"] = f"solc rc={rc}"
            rows.append(row)
            continue
        wd = proj / "_gen" / sol.stem
        wd.mkdir(parents=True, exist_ok=True)
        ast = wd / f"{sol.stem}.solast"
        ast.write_text(out)

        cmd = [str(ESBMC), str(ast), "--sol", str(sol),
               "--solidity-path-coverage", "--cov-report-json",
               "--generate-foundry-testcase", "--memlimit", a.memlimit,
               "--contract", cname, "--solidity-max-tx", a.tx]
        t0 = time.time()
        rc, out = sh(cmd, cwd=str(wd), timeout=a.timeout)
        row["wall"] = round(time.time() - t0, 1)
        row["rc"] = rc
        (wd / "run.log").write_text(out)
        row.update(emit_reasons(out))

        rep = wd / "cov-report.json"
        if rep.exists():
            d = json.loads(rep.read_text())
            s = d.get("summary", {})
            row["paths"] = s.get("paths_total")
            row["F"] = s.get("F_feasible_with_ce")
            row["partial"] = bool(d.get("partial", s.get("partial")))
            row["U_reasons"] = {k: v for k, v in
                                (s.get("U_reasons") or {}).items() if v}
        else:
            row["err"] = f"no cov-report.json (rc={rc})"

        # Copy the PoC source and every emitted test into the shared project,
        # renaming the test contract so several PoCs can coexist.
        produced = sorted(wd.glob("*.cov.t.sol"))
        row["emitted_files"] = len(produced)
        if produced:
            shutil.copy(sol, proj / "src" / sol.name)
        for p in produced:
            txt = p.read_text()
            # ---- RENAME EVERY TEST CONTRACT IN THE FILE, NOT JUST ONE ----
            #
            # The emitter splits into SEVERAL test contracts in one file when
            # two paths need different CONSTRUCTOR-time state -- `vm.startPrank`
            # around `new C()` -- because a Foundry `setUp()` can only deploy
            # once. Measured on P07_Sender and D02_StructWithMapping:
            # `contract P07_SenderCovTest_0` and `..._1`.
            #
            # Matching only `<stem>CovTest` left those contracts under their
            # original names, so `forge test` ran them and this script could not
            # attribute the results: six PoCs reported `cases=2, GREEN=0, RED=0`
            # -- which reads exactly like "the tests did not run" and is instead
            # "the tests ran and I could not find them". A measurement bug that
            # presents as a product failure is the worst kind, so every contract
            # is now registered.
            for m in re.finditer(r"contract\s+(\w*CovTest\w*)\s+is\s+Test",
                                 txt):
                owner[m.group(1)] = sol.stem
            txt = re.sub(r'from "\./[^"]*"', f'from "../src/{sol.name}"', txt)
            (proj / "test" / f"{sol.stem}_CovTest.t.sol").write_text(txt)
            # CLAIMS RENDERED, which is the funnel's real unit. One emitted
            # `test_cov_N` can be labelled with SEVERAL claim ids, so counting
            # test functions understates what the emitter claims to cover --
            # and counting claim ids reveals the merges, which is where the
            # ABI-value-gate payload defect shows up.
            ids = set()
            merged = 0
            for line in txt.splitlines():
                s = line.strip()
                if s.startswith("// claim:"):
                    parts = [x.strip() for x in
                             s[len("// claim:"):].split(",") if x.strip()]
                    ids.update(parts)
                    if len(parts) > 1:
                        merged += 1
            row["claims_rendered"] = len(ids)
            row["merged_cases"] = merged
        rows.append(row)

    # ---- build ONCE for the whole set --------------------------------------
    print("## forge build\n", flush=True)
    rc, out = sh(["forge", "build"], cwd=str(proj), timeout=900)
    (proj / "build.log").write_text(out)
    build_ok = rc == 0
    print(f"  rc={rc}  ({'ok' if build_ok else 'FAILED -- see build.log'})\n")

    green = defaultdict(int)
    red = defaultdict(list)
    if build_ok:
        print("## forge test\n", flush=True)
        rc, out = sh(["forge", "test", "-vv"], cwd=str(proj), timeout=900)
        (proj / "test.log").write_text(out)
        cur = None
        seen = set()
        for line in out.splitlines():
            m = re.match(r"Ran \d+ tests? for (\S+):(\S+)", line)
            if m:
                cur = m.group(2)
                continue
            m = re.match(r"\[(PASS|FAIL)[^\]]*\]\s+(\w+)\(", line)
            if m and cur:
                key = (cur, m.group(2))
                if key in seen:
                    continue          # forge prints failures twice
                seen.add(key)
                stem = owner.get(cur, cur)
                if m.group(1) == "PASS":
                    green[stem] += 1
                else:
                    red[stem].append(m.group(2))
        print(f"  rc={rc}\n")

    # ---- the table ---------------------------------------------------------
    print("## The funnel, per contract "
          f"(--solidity-max-tx {a.tx}, whole contract, no --focus-function)\n")
    print("| contract | paths | F (has CE) | claims rendered | cases | merged "
          "cases | GREEN | RED | s |")
    print("|---|---|---|---|---|---|---|---|---|")
    tot = defaultdict(int)
    for r in rows:
        stem = r["stem"]
        if "err" in r:
            print(f"| `{stem}` | {r['err']} | - | - | - | - | - | - | "
                  f"{r.get('wall', '-')} |")
            continue
        g = green.get(stem, 0)
        rd = len(red.get(stem, []))
        mark = " **PARTIAL**" if r.get("partial") else ""
        print(f"| `{stem}` | {r.get('paths')}{mark} | {r.get('F')} | "
              f"{r.get('claims_rendered', 0)} | {r.get('cases', 0)} | "
              f"{r.get('merged_cases', 0)} | {g} | {rd} | {r['wall']} |")
        for k, v in (("paths", r.get("paths")), ("F", r.get("F")),
                     ("claims_rendered", r.get("claims_rendered", 0)),
                     ("cases", r.get("cases", 0)),
                     ("merged", r.get("merged_cases", 0))):
            if isinstance(v, int):
                tot[k] += v
        tot["green"] += g
        tot["red"] += rd

    print(f"\n**TOTAL  paths {tot['paths']}  ->  F {tot['F']}  ->  claims "
          f"rendered {tot['claims_rendered']}  ->  GREEN {tot['green']} of "
          f"{tot['cases']} case(s)  (RED {tot['red']})**\n")
    print(f"`merged cases` = {tot['merged']} emitted case(s) are labelled with "
          f"MORE THAN ONE path id. A single concrete call cannot walk two "
          f"different decision sequences, so each merge is one path counted as "
          f"rendered that the test cannot actually reach. Every merge measured "
          f"so far is the pair that differs only on the synthetic ABI value "
          f"gate, whose payload reports `msg.value = 0` on BOTH arms -- run "
          f"ce_consistency.py on the reports under _gen/ to see it named.\n")

    # ---- where each stage lost, named ---------------------------------------
    print("## Where each stage lost\n")
    print("### paths -> F  (per U reason, summed)\n")
    agg = defaultdict(int)
    for r in rows:
        for k, v in (r.get("U_reasons") or {}).items():
            agg[k] += v
    if not agg:
        print("  nothing lost: every instrumented path has a counterexample\n")
    for k, v in sorted(agg.items(), key=lambda kv: -kv[1]):
        print(f"  {v:>4}  {k}")

    print("\n### F -> rendered  (the emitter's own words)\n")
    any_loss = False
    for r in rows:
        if "err" in r:
            continue
        f, c = r.get("F") or 0, r.get("cases", 0)
        if f == c:
            continue
        any_loss = True
        bits = []
        for k in ("refused_ctor_only", "refused_obstacle", "defaulted_calls",
                  "defaulted_args", "defaulted_types",
                  "mentions_unsupported"):
            if k in r:
                bits.append(f"{k}={r[k]}")
        print(f"  {r['stem']}: F={f} cases={c}  "
              f"{'; '.join(bits) if bits else 'NO REASON IN THE LOG'}")
    if not any_loss:
        print("  nothing lost: every counterexample became a case\n")

    print("\n### rendered -> GREEN  (RED on the contract it came from)\n")
    if not any(red.values()):
        print("  nothing lost: every rendered case passes\n")
    for stem, fns in sorted(red.items()):
        print(f"  {stem}: {len(fns)} RED  ({', '.join(fns)})")

    # ---- REFUSE TO PRESENT THE RATE AS FINAL WHILE PAYLOADS CONTRADICT PATHS -
    #
    # The conversion rate above is built from `F` and from what the emitter
    # rendered. Both are claims about paths, and a payload that contradicts its
    # own decision sequence makes them claims about a path the test does not
    # walk. Measured: 37 of 161 emitted cases carry more than one path id, all of
    # them the ABI-value-gate pair whose payload reports `msg.value = 0` on both
    # arms -- so the numerator is inflated by exactly the paths the emitter could
    # not tell apart.
    #
    # So this is a GATE and not a footnote. It runs the consistency checker over
    # the reports this run produced and exits non-zero when any decision
    # disagrees. A number that is quoted while this fires is a number about a
    # different set of paths than the one it names.
    print("\n## Payload-vs-path gate\n", flush=True)
    reports = sorted((proj / "_gen").glob("*/cov-report.json"))
    if not reports:
        print("  no report to check -- the gate did NOT pass, it had nothing "
              "to look at")
        print(f"\nproject: {proj}")
        return 2
    rc, out = sh([sys.executable,
                  str(Path(__file__).resolve().parent / "ce_consistency.py")]
                 + [str(p) for p in reports], timeout=600)
    for ln in out.splitlines():
        print("  " + ln)
    print(f"\nproject: {proj}")
    if rc != 0:
        print("\n**GATE FAILED.** At least one counterexample payload does not "
              "walk the path it is filed under, so `F`, `claims rendered` and "
              "the GREEN count above describe a path set that differs from the "
              "one they are labelled with. Fix the payload before quoting the "
              "rate.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
