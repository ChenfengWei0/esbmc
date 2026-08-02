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

        # ---- THE THREE FLAGS AN EMITTING RUN MUST CARRY ----
        #
        # INVOCATION_DECISIONS.md row 6: without them a witnessed path whose
        # counterexample wraps or divides by zero is rendered as a bare call
        # asserting a NORMAL exit, and is RED on the unmodified contract.
        #
        # THERE ARE TWO EMITTERS AND THE FIX ONLY REACHED ONE. Commit 4e1cbee1b7
        # added these to `scripts/solidity_path_put.py`, measured the D10/D20
        # `test_cov_*` cases going from panic 0x11 to green, and said so. This
        # script builds its OWN esbmc command and did not get them, so the funnel
        # re-run on that same HEAD came back BYTE-IDENTICAL -- 334 -> 301 -> 300
        # -> 280 of 294, with the same 11 RED, including exactly the D10, Tiny2,
        # P18 and D20 cases the other project had already turned green. A fix
        # applied to one of two readers of the same fact reads as "measured, no
        # effect" and is actually "not wired here".
        cmd = [str(ESBMC), str(ast), "--sol", str(sol),
               "--solidity-path-coverage", "--cov-report-json",
               "--generate-foundry-testcase", "--memlimit", a.memlimit,
               "--overflow-check", "--div-by-zero-check",
               "--path-cov-arith-resolve",
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
            #
            # THE EMITTER'S REASON IS IN THE ARTIFACT, NOT ONLY IN THE LOG.
            # `emit_reasons` reads the run log, and for an unrenderable
            # argument the log says nothing while the generated file says
            # exactly what happened, in the emitter's own words:
            #
            #   // UNSUPPORTED: P26_TypeMatrix.takeEnum has an argument type
            #   //              ESBMC cannot yet render as a literal
            #
            # Before this, that loss printed as "NO REASON IN THE LOG", which
            # is a claim about the emitter (it did not say) when the truth was
            # about this script (it did not look). So the case body is parsed
            # alongside the claim ids and the note is quoted verbatim.
            ids = set()
            merged = 0
            merge_groups = []
            unsupported = []
            cur_parts, cur_body = None, []

            def close_case():
                nonlocal merged
                if cur_parts is None:
                    return
                note = ""
                for bl in cur_body:
                    if "UNSUPPORTED" in bl:
                        note = bl.strip().lstrip("/").strip()
                        break
                if note:
                    unsupported.append(note)
                if len(cur_parts) > 1:
                    merged += 1
                    merge_groups.append({"parts": cur_parts,
                                         "unsupported": bool(note),
                                         "note": note})

            for line in txt.splitlines():
                s = line.strip()
                if s.startswith("// claim:"):
                    close_case()
                    cur_parts = [x.strip() for x in
                                 s[len("// claim:"):].split(",") if x.strip()]
                    cur_body = []
                    ids.update(cur_parts)
                elif cur_parts is not None:
                    cur_body.append(line)
            close_case()

            row["claims_rendered"] = len(ids)
            row["merged_cases"] = merged
            row["merge_groups"] = merge_groups
            row["unsupported_notes"] = unsupported
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
        # BOTH HEADERS. forge prints every failure twice -- under its suite
        # (`Ran N tests for <file>:<contract>`) and again in a closing block
        # (`Encountered N failing test(s) in <file>:<contract>`). Matching only
        # the first left `cur` pointing at the LAST suite that ran, so the
        # closing block's duplicates were re-keyed onto that contract. Measured:
        # one spurious RED filed against P13_Exits, for a `test_cov_5` its file
        # does not contain, while its own suite result was `0 failed`. Every
        # generated suite names its tests test_cov_0, test_cov_1, ..., so the
        # function name identifies nothing without its contract.
        for line in out.splitlines():
            m = re.match(r"Ran \d+ tests? for (\S+):(\S+)", line)
            if m:
                cur = m.group(2)
                continue
            m = re.match(r"Encountered \d+ failing tests? in (\S+):(\S+)",
                         line)
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

        # ---- CROSS-CHECK AGAINST forge's OWN TOTAL ----
        #
        # This parser reconstructs a number the tool already prints. Checking
        # one against the other is what turns "an unrecognised header shape"
        # from a silent miscount into a loud one -- which is exactly how the
        # P13_Exits phantom would have been caught the first time instead of by
        # noticing that a contract with no arithmetic had an arithmetic panic.
        m = re.search(r"(\d+) tests? passed, (\d+) failed", out)
        if m:
            fg, fr = int(m.group(1)), int(m.group(2))
            pg = sum(green.values())
            pr = sum(len(v) for v in red.values())
            if (fg, fr) != (pg, pr):
                print(f"  ** PARSE DISAGREES WITH forge's OWN SUMMARY: this "
                      f"script counted {pg} passed / {pr} failed, forge "
                      f"reported {fg} passed / {fr} failed. The per-contract "
                      f"GREEN/RED columns below are NOT trustworthy; a header "
                      f"shape this parser does not know re-keys failures onto "
                      f"the wrong suite. **\n")
            else:
                print(f"  parse agrees with forge's own summary: "
                      f"{fg} passed, {fr} failed\n")
        else:
            print("  ** forge printed no summary line this parser recognises, "
                  "so the GREEN/RED columns are unchecked **\n")

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
          f"rendered that the test cannot actually reach.\n")
    print("This footnote used to assert that EVERY merge is the synthetic ABI "
          "value gate. The section below, added to check that claim rather "
          "than repeat it, refuted it on its first run: on P26_TypeMatrix two "
          "paths differing on a bytesN equality "
          "(`(_Bool)return_value$_bytes_static_equal$2`) are also rendered as "
          "one call, and one case carries THREE path ids. So the coordinate "
          "the renderer cannot distinguish is not always `msg.value`, and each "
          "merge is attributed individually below instead of being assumed.\n")

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
        notes = r.get("unsupported_notes") or []
        if not bits and not notes:
            print(f"  {r['stem']}: F={f} cases={c}  NO REASON IN THE LOG OR "
                  f"THE EMITTED FILE")
        else:
            print(f"  {r['stem']}: F={f} cases={c}  "
                  f"{'; '.join(bits) if bits else ''}")
        # Quoted from the generated test, not paraphrased: the emitter names
        # the contract, the method and what it could not render, and that is
        # the whole finding. Summarising it here would lose the method name,
        # which is the only part that says what to fix.
        for n in notes:
            print(f"      {n}")
    if not any_loss:
        print("  nothing lost: every counterexample became a case\n")

    # ---- WHICH DECISION THE MERGED PATHS DISAGREE ON ----
    #
    # Not a guess. Every merged case names >1 path id, and each of those
    # ids has its own `decisions` array in the same cov-report.json. Two paths
    # that one concrete call is claimed to walk must differ SOMEWHERE, and the
    # first index at which their decision sequences differ is exactly the
    # coordinate the renderer failed to distinguish. Printing it turns "NO
    # REASON IN THE LOG" into a named, attributable loss -- and it is what
    # showed that every merge measured so far is one coordinate, the synthetic
    # ABI value gate, rather than an assortment.
    print("\n### merged cases -> WHICH decision the renderer could not "
          "distinguish\n")
    any_merge = False
    for r in rows:
        groups = r.get("merge_groups") or []
        if not groups:
            continue
        rep = proj / "_gen" / r["stem"] / "cov-report.json"
        if not rep.exists():
            continue
        by_cond = {}
        for c in json.loads(rep.read_text()).get("claims", []):
            fn = c.get("path_function") or ""
            pid = c.get("path_id") or ""
            by_cond[f"{fn}:path:{pid}"] = c
        for grp in groups:
            parts = grp["parts"]
            any_merge = True
            # A MERGE HAS MORE THAN ONE CAUSE, AND THIS SECTION USED TO REPORT
            # ONLY ONE OF THEM. When the case body is UNSUPPORTED the emitter
            # produced an EMPTY function, so every path it could not render
            # collapses onto that one no-op -- the renderer did not fail to
            # tell two decisions apart, it never got as far as a call. Reading
            # the first differing decision anyway printed
            #   "first disagree at decision #0 [SYNTHETIC ABI VALUE GATE]"
            # for P26's three takeEnum paths, which names a coordinate that had
            # nothing to do with it. `msg.value` was innocent; the argument
            # type was the cause, and it is stated in the file.
            if grp.get("unsupported"):
                print(f"  {r['stem']}: {len(parts)} claim(s) on one case, and "
                      f"the case is EMPTY -- not a coordinate the renderer "
                      f"could not distinguish:")
                print(f"      {grp['note']}")
                for p in parts:
                    print(f"      {p.rsplit(':', 2)[-1]:>4}: (no call emitted)")
                continue
            seqs = []
            for p in parts:
                c = by_cond.get(p)
                seqs.append([] if c is None else (c.get("decisions") or []))
            # First index at which the arms disagree.
            where = None
            for i in range(max(len(s) for s in seqs)):
                arms = {tuple(s[i].get("arm") for s in seqs if i < len(s))}
                present = [s[i] for s in seqs if i < len(s)]
                if len(present) != len(seqs) or len(
                        {d.get("arm") for d in present}) > 1:
                    where = (i, present)
                    break
            if where is None:
                print(f"  {r['stem']}: {len(parts)} claim(s) merged, and their "
                      f"decision sequences are IDENTICAL -- that is a separate "
                      f"defect (two path ids for one sequence)")
                continue
            i, present = where
            d0 = present[0] if present else {}
            gate = " [SYNTHETIC ABI VALUE GATE]" if d0.get(
                "synthetic_abi_gate") else ""
            print(f"  {r['stem']}: {len(parts)} claim(s) on one case, first "
                  f"disagree at decision #{i}{gate}")
            for p, s in zip(parts, seqs):
                if i < len(s):
                    print(f"      {p.rsplit(':', 2)[-1]:>4}: "
                          f"{s[i].get('branch_claim')} [{s[i].get('arm')}]")
                else:
                    print(f"      {p.rsplit(':', 2)[-1]:>4}: (sequence is "
                          f"shorter -- this path exits before decision #{i})")
    if not any_merge:
        print("  no case carries more than one path id\n")

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
