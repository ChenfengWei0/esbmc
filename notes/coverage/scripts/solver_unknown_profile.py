#!/usr/bin/env python3
"""Characterise a benchmark's `solver-unknown` path claims against its
`bounded-holds` ones, from the reports AND the per-run logs on disk.

WHY THIS EXISTS. `u_reason_census.py` answers "how many U, and why" for a
benchmark. It stops exactly where task #16 starts: st1inch is 128 U, 0 F, and
the U split is 81 bounded-holds / 47 solver-unknown. Both are U -- the run
decided neither -- but they are opposite outcomes. `bounded-holds` means the
claim was PROVEN (UNSAT) inside the bound; `solver-unknown` means the solver
came back with no verdict at all. A cause hypothesis has to explain the SECOND
group only, so the second group has to be separated from the first on every
attribute the data actually carries.

WHAT IT REFUSES TO DO.

  * It never estimates a solve time. A claim whose solve it cannot find in a
    run.log is printed as `no-log-match`, never as 0 or as the unit mean.
  * It never assumes one solve per claim. This corpus's own reports print
    "Verdicts Preserved: N -- the same claim key was solved more than once,
    which is a separate defect", so every solve attempt is kept separately and
    the per-claim aggregate says how many attempts there were.
  * It classifies EVERY line in each log's solving region. Lines it does not
    recognise are printed verbatim under UNCLASSIFIED, because a marker that
    exists but is not parsed is exactly how a "no timeouts" conclusion gets
    made from a log that recorded them under a different word.
  * A run with no report is listed by name; it is not a zero.

Usage: python3 solver_unknown_profile.py <bench-dir> [...]
       python3 solver_unknown_profile.py ../pathcov/st1inch_St1inch
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# --- log line grammar -------------------------------------------------------
RE_SOLVING = re.compile(r"^Solving claim '(?P<claim>.*?)' with solver (?P<solver>.*)$")
RE_ENCODE = re.compile(r"^Encoding to solver time: (?P<s>[0-9.]+)s$")
RE_DP = re.compile(r"^Runtime decision procedure: (?P<s>[0-9.]+)s$")
RE_PASSED = re.compile(r"^✓ PASSED: '(?P<claim>.*?)'$")
RE_FAILED = re.compile(r"^(?:✗ )?(?:FAILED|Violated property):?\s*'?(?P<claim>.*?)'?$")
RE_SLICING = re.compile(r"^Slicing time: [0-9.]+s \(removed \d+ assignments\)$")
RE_ENCODING_HDR = re.compile(r"^Encoding remaining VCC\(s\) using ")
RE_SOLVING_WITH = re.compile(r"^Solving with solver ")
RE_PROPS = re.compile(r"^Properties: (?P<n>\d+) verified")
RE_SOLVER_TOT = re.compile(r"^Solver: .*Decision procedure total time: (?P<s>[0-9.]+)s")
RE_VCC = re.compile(r"^Generated (?P<gen>\d+) VCC\(s\), (?P<rem>\d+) remaining after simplification \((?P<asg>\d+) assignments\)$")
RE_SYMEX = re.compile(r"^Symex completed in: (?P<s>[0-9.]+)s \((?P<asg>\d+) assignments\)$")
RE_GOTO = re.compile(r"^GOTO program creation time: (?P<s>[0-9.]+)s$")
RE_BUDGET = re.compile(r"^Claim Budget: (?P<s>\d+)s per claim — (?P<n>\d+) claim\(s\) abandoned over budget")
RE_PRESERVED = re.compile(r"^Verdicts Preserved: (?P<n>\d+)")
RE_REACHED_SOLVER = re.compile(r"^--solidity-path-coverage: (?P<a>\d+) of (?P<b>\d+) instrumented path claim\(s\) reached the solver")

# words that would mean "budget", if any log used them
BUDGET_WORDS = ("timeout", "timed out", "time out", "abandoned", "over budget",
                "resource limit", "rlimit", "memlimit", "out of memory",
                "memory limit", "unknown", "UNKNOWN", "incomplete")


def parse_log(path):
    """Return (solves, meta, unclassified). One record per SOLVE ATTEMPT."""
    solves = []
    meta = {}
    unclassified = []
    cur = None
    in_solving_region = False
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        m = RE_SOLVING.match(line)
        if m:
            in_solving_region = True
            if cur is not None:
                solves.append(cur)
            cur = {"claim": m.group("claim"), "solver": m.group("solver"),
                   "encode_s": None, "dp_s": None, "verdict": None}
            continue
        if cur is not None:
            m = RE_ENCODE.match(line)
            if m:
                cur["encode_s"] = float(m.group("s"))
                continue
            m = RE_DP.match(line)
            if m:
                cur["dp_s"] = float(m.group("s"))
                continue
            m = RE_PASSED.match(line)
            if m:
                cur["verdict"] = "PASSED"
                cur["verdict_claim"] = m.group("claim")
                continue
            if line.startswith("✗") or line.startswith("FAILED")   \
               or line.startswith("Violated property"):
                cur["verdict"] = "FAILED"
                continue
        for rx, key in ((RE_PROPS, "properties_verified"),
                        (RE_SOLVER_TOT, "dp_total_s"),
                        (RE_SYMEX, "symex_s"),
                        (RE_GOTO, "goto_s"),
                        (RE_PRESERVED, "verdicts_preserved")):
            m = rx.match(line)
            if m:
                meta[key] = m.groupdict()
        m = RE_VCC.match(line)
        if m:
            meta.setdefault("vcc", []).append(m.groupdict())
        m = RE_BUDGET.match(line)
        if m:
            meta["claim_budget_s"] = int(m.group("s"))
            meta["claims_abandoned"] = int(m.group("n"))
        m = RE_REACHED_SOLVER.match(line)
        if m:
            meta["reached_solver"] = (int(m.group("a")), int(m.group("b")))
        if in_solving_region and cur is not None:
            if not (RE_SLICING.match(line) or RE_ENCODING_HDR.match(line)
                    or RE_SOLVING_WITH.match(line) or RE_ENCODE.match(line)
                    or RE_DP.match(line) or RE_PASSED.match(line)
                    or RE_SOLVING.match(line) or line == ""):
                unclassified.append(line)
    if cur is not None:
        solves.append(cur)
    # any line in the WHOLE log mentioning a budget/limit word, verbatim
    budget_lines = [l.strip() for l in path.read_text(errors="replace").splitlines()
                    if any(w in l for w in BUDGET_WORDS)]
    meta["budget_word_lines"] = budget_lines
    return solves, meta, unclassified


def short_unit(pf, cond):
    """Unit name from path_function; fall back to the claim condition prefix."""
    if pf:
        m = re.search(r"@F@([A-Za-z0-9_]+)#", pf)
        if m:
            return m.group(1)
        return pf
    if cond and ":path:" in cond:
        return cond.split(":path:")[0]
    return "?"


def pct(a, b):
    return "n/a" if not b else f"{100.0 * a / b:5.1f}%"


def main(argv):
    if len(argv) < 2:
        sys.exit(f"usage: {argv[0]} <bench-dir> [...]")
    for d in argv[1:]:
        profile(Path(d))
    return 0


def profile(bench):
    print("=" * 78)
    print(f"BENCHMARK {bench.name}   ({bench.resolve()})")
    print("=" * 78)

    reports = sorted((bench / "reports").glob("*.json"))
    runs = []
    rj = bench / "runs.jsonl"
    if rj.is_file():
        for line in rj.read_text().splitlines():
            line = line.strip()
            if line:
                runs.append(json.loads(line))

    # ---- 0. runs vs reports -------------------------------------------------
    tags_with_report = {p.stem for p in reports}
    print("\n[0] RUNS vs REPORTS")
    print(f"  runs in runs.jsonl        {len(runs)}")
    print(f"  reports on disk           {len(reports)}")
    for r in runs:
        if r["tag"] not in tags_with_report:
            print(f"  NO REPORT: {r['tag']}  wall={r.get('wallSeconds')}s "
                  f"exit={r.get('exitCode')} "
                  f"killedByOuterTimeout={r.get('killedByOuterTimeout')} "
                  f"pathsInstrumented={r.get('pathsInstrumented')}")
    heads = Counter(r.get("binary", {}).get("head") for r in runs)
    print(f"  binary heads across runs  {dict(heads)}")
    dirty = {r["tag"] for r in runs if r.get("binary", {}).get("srcDirty")}
    print(f"  runs with srcDirty        {sorted(dirty) if dirty else 'none'}")

    # ---- 1. claims ----------------------------------------------------------
    claims = []          # one dict per claim entry, with its report tag
    completeness = Counter()
    for rp in reports:
        d = json.loads(rp.read_text())
        s = d.get("summary", {})
        p = d.get("partial", s.get("partial"))
        completeness["PARTIAL" if p is True else
                     ("unstated" if p is None else "complete")] += 1
        for c in d.get("claims", []):
            c = dict(c)
            c["_tag"] = rp.stem
            c["_unit"] = short_unit(c.get("path_function"), c.get("condition"))
            claims.append(c)
    print(f"  report completeness       {dict(completeness)}")

    keys = Counter()
    for c in claims:
        for k in c:
            keys[k] += 1
    print(f"\n  fields present on claim entries (field: how many of "
          f"{len(claims)} claims carry it)")
    for k, v in sorted(keys.items()):
        print(f"    {k:<28} {v}")

    reasons = Counter(c.get("u_reason") for c in claims)
    statuses = Counter(c.get("status") for c in claims)
    print(f"\n  status   {dict(sorted(statuses.items()))}")
    print(f"  u_reason {dict(sorted(reasons.items(), key=lambda kv: -kv[1]))}")

    # ---- 2. per unit: count AND rate ---------------------------------------
    per_unit = defaultdict(Counter)
    for c in claims:
        per_unit[c["_unit"]][c.get("u_reason")] += 1
        per_unit[c["_unit"]]["_total"] += 1
    print("\n[1] PER UNIT  solver-unknown COUNT and RATE")
    print(f"  {'unit':<26} {'paths':>6} {'unk':>5} {'rate':>7} "
          f"{'bhold':>6} {'other':>6}")
    rows = sorted(per_unit.items(),
                  key=lambda kv: (-kv[1]["solver-unknown"] / kv[1]["_total"],
                                  -kv[1]["_total"], kv[0]))
    for unit, e in rows:
        tot = e["_total"]
        unk = e["solver-unknown"]
        bh = e["bounded-holds"]
        other = tot - unk - bh
        print(f"  {unit:<26} {tot:>6} {unk:>5} {pct(unk, tot):>7} "
              f"{bh:>6} {other:>6}")
    tot = sum(e["_total"] for e in per_unit.values())
    unk = sum(e["solver-unknown"] for e in per_unit.values())
    print(f"  {'TOTAL':<26} {tot:>6} {unk:>5} {pct(unk, tot):>7}")
    zero = [u for u, e in per_unit.items() if e["solver-unknown"] == 0]
    full = [u for u, e in per_unit.items()
            if e["solver-unknown"] == e["_total"]]
    print(f"\n  units at 0%% solver-unknown ({len(zero)}): {sorted(zero)}")
    print(f"  units at 100%% solver-unknown ({len(full)}): {sorted(full)}")

    # ---- 3. what separates unknown from bounded-holds -----------------------
    print("\n[2] SEPARATORS  (all claims, unknown vs bounded-holds)")
    for attr in ("path_depth", "exit_kind", "revert_kind", "bounded_holds",
                 "witnessed_in_earlier_round", "status", "line", "column",
                 "file", "function"):
        tab = defaultdict(Counter)
        for c in claims:
            tab[c.get(attr)][c.get("u_reason")] += 1
        print(f"\n  by {attr}")
        print(f"    {'value':<16} {'unk':>5} {'bhold':>6} {'rate':>7}")
        for v in sorted(tab, key=lambda x: (x is None, x)):
            e = tab[v]
            u, b = e["solver-unknown"], e["bounded-holds"]
            print(f"    {str(v):<16} {u:>5} {b:>6} {pct(u, u + b):>7}")

    # within-unit depth split, only for units that carry BOTH kinds
    print("\n  WITHIN-UNIT path_depth split (units carrying both kinds)")
    per_unit_depth = defaultdict(lambda: defaultdict(list))
    for c in claims:
        per_unit_depth[c["_unit"]][c.get("u_reason")].append(
            (c.get("path_depth"), c.get("path_id"), c.get("exit_kind")))
    for unit in sorted(per_unit_depth):
        e = per_unit_depth[unit]
        if e.get("solver-unknown") and e.get("bounded-holds"):
            us = sorted(e["solver-unknown"])
            bs = sorted(e["bounded-holds"])
            print(f"    {unit}")
            print(f"      unknown   depths {sorted(d for d, _, _ in us)}  "
                  f"ids {[i for _, i, _ in us]}")
            print(f"      bnd-holds depths {sorted(d for d, _, _ in bs)}  "
                  f"ids {[i for _, i, _ in bs]}")

    # ---- 4. run logs --------------------------------------------------------
    print("\n[3] RUN LOGS  per-solve timings")
    all_solves = {}      # tag -> [solve records]
    all_meta = {}
    unclassified_all = Counter()
    for tag in sorted({r["tag"] for r in runs} | tags_with_report):
        lp = bench / "work" / tag / "run.log"
        if not lp.is_file():
            print(f"  NO run.log for {tag}")
            continue
        solves, meta, unclas = parse_log(lp)
        all_solves[tag] = solves
        all_meta[tag] = meta
        for u in unclas:
            unclassified_all[u] += 1

    print(f"  logs parsed               {len(all_solves)}")
    print(f"  total solve attempts      {sum(len(v) for v in all_solves.values())}")
    if unclassified_all:
        print("  UNCLASSIFIED lines inside the solving region "
              "(shape -> count) -- read these before trusting any timing claim")
        for l, n in unclassified_all.most_common():
            print(f"    [{n:>4}] {l}")
    else:
        print("  UNCLASSIFIED lines inside the solving region: none")

    print("\n  per run: budget, abandoned, preserved verdicts, reached-solver")
    print(f"  {'tag':<32} {'budget':>7} {'aband':>6} {'presv':>6} "
          f"{'reached':>9} {'solves':>7} {'dp_total':>9}")
    for tag in sorted(all_meta):
        m = all_meta[tag]
        rs = m.get("reached_solver")
        print(f"  {tag:<32} {m.get('claim_budget_s', 'n/r'):>7} "
              f"{m.get('claims_abandoned', 'n/r'):>6} "
              f"{(m.get('verdicts_preserved') or {}).get('n', 'n/r'):>6} "
              f"{(f'{rs[0]}/{rs[1]}' if rs else 'n/r'):>9} "
              f"{len(all_solves.get(tag, [])):>7} "
              f"{(m.get('dp_total_s') or {}).get('s', 'n/r'):>9}")

    # join solves to claims by (tag, claim-name prefix)
    print("\n[4] SOLVE ATTEMPTS joined to claims by condition string")
    joined = []
    unmatched_solves = []
    claim_by_key = {}
    for c in claims:
        claim_by_key[(c["_tag"], c.get("condition"))] = c
    for tag, solves in all_solves.items():
        for s in solves:
            name = s["claim"]
            # the log prints "<condition> at" for a claim with no location and
            # "<condition> at file F line L function G" for one that has a
            # location. Both forms end the condition at the first " at ".
            cond = name[:-3] if name.endswith(" at") else name.split(" at ")[0]
            c = claim_by_key.get((tag, cond))
            if c is None:
                unmatched_solves.append((tag, name))
            joined.append({**s, "_tag": tag, "_cond": cond,
                           "_reason": (c or {}).get("u_reason"),
                           "_unit": (c or {}).get("_unit"),
                           "_depth": (c or {}).get("path_depth")})
    print(f"  solve attempts joined     {len(joined)}")
    print(f"  attempts with no matching claim entry {len(unmatched_solves)}")
    for t, n in unmatched_solves:
        print(f"    {t}  {n}")

    # per-attempt verdict vs reason
    tab = defaultdict(Counter)
    for j in joined:
        tab[j["verdict"]][j["_reason"]] += 1
    print("\n  solve-attempt VERDICT x claim u_reason")
    for v in sorted(tab, key=lambda x: (x is None, str(x))):
        print(f"    verdict={str(v):<8} {dict(tab[v])}")

    def stats(vals):
        vals = sorted(v for v in vals if v is not None)
        if not vals:
            return "no timings recorded"
        n = len(vals)
        return (f"n={n:<4} min={vals[0]:>8.3f} "
                f"med={vals[n // 2]:>8.3f} max={vals[-1]:>8.3f} "
                f"sum={sum(vals):>9.3f}")

    print("\n  NOTE: verdict=PASSED is UNSAT (claim holds), verdict=FAILED is "
          "SAT (a counterexample was built), verdict=None is NO VERDICT.")
    print("\n  decision-procedure seconds, by solve-attempt verdict")
    for v in sorted(tab, key=lambda x: (x is None, str(x))):
        print(f"    verdict={str(v):<8} {stats([j['dp_s'] for j in joined if j['verdict'] == v])}")
    print("\n  decision-procedure seconds, by claim u_reason")
    for r in sorted({j["_reason"] for j in joined}, key=lambda x: (x is None, str(x))):
        print(f"    reason={str(r):<16} {stats([j['dp_s'] for j in joined if j['_reason'] == r])}")
    print("\n  encoding seconds, by claim u_reason")
    for r in sorted({j["_reason"] for j in joined}, key=lambda x: (x is None, str(x))):
        print(f"    reason={str(r):<16} {stats([j['encode_s'] for j in joined if j['_reason'] == r])}")

    budget = {m.get("claim_budget_s") for m in all_meta.values()}
    print(f"\n  claim budget(s) seen      {budget}")
    over = [j for j in joined if j["dp_s"] is not None
            and any(b for b in budget if b and j["dp_s"] >= b)]
    print(f"  attempts at/over budget   {len(over)}")
    longest = sorted((j for j in joined if j["dp_s"] is not None),
                     key=lambda j: -j["dp_s"])[:10]
    print("  10 longest solve attempts")
    for j in longest:
        print(f"    {j['dp_s']:>9.3f}s  verdict={str(j['verdict']):<7} "
              f"reason={str(j['_reason']):<16} {j['_tag']}  {j['_cond']}")

    # per claim: how many attempts, and did ANY return a verdict
    per_claim = defaultdict(list)
    for j in joined:
        per_claim[(j["_tag"], j["_cond"])].append(j)
    print("\n[5] PER CLAIM: attempts, verdicts, times "
          "(every claim, sorted by unit then id)")
    print(f"  {'unit':<24} {'path':>6} {'reason':<16} {'dep':>3} "
          f"{'att':>3} {'verdicts':<14} {'dp seconds'}")
    for c in sorted(claims, key=lambda c: (c["_unit"],
                                           int(c.get("path_id") or -1))):
        js = per_claim.get((c["_tag"], c.get("condition")), [])
        if not js:
            vs, ts = "no-log-match", ""
        else:
            vs = ",".join(str(j["verdict"]) for j in js)
            ts = " ".join(f"{j['dp_s']:.3f}" if j["dp_s"] is not None
                          else "n/r" for j in js)
        print(f"  {c['_unit']:<24} {str(c.get('path_id')):>6} "
              f"{str(c.get('u_reason')):<16} {str(c.get('path_depth')):>3} "
              f"{len(js):>3} {vs:<14} {ts}")

    # ---- 4b. ORDER of solve attempts inside each run ------------------------
    # An "undecided" population that is always at the TAIL of a run means
    # something ACCUMULATES across solves (memory, an incremental solver
    # context). One that is interleaved with decided solves means the cause is
    # per-formula. These are different bugs and the order is the only thing on
    # disk that separates them.
    print("\n[4b] SOLVE ORDER inside each run  "
          "(D=decided/PASSED, ?=no verdict; seconds under each)")
    for tag in sorted(all_solves):
        solves = all_solves[tag]
        if not solves:
            continue
        marks = "".join("D" if s["verdict"] == "PASSED" else "?"
                        for s in solves)
        print(f"  {tag}")
        print(f"    order   {marks}")
        print(f"    seconds {' '.join(f'{s['dp_s']:.2f}' if s['dp_s'] is not None else 'n/r' for s in solves)}")
        print(f"    claims  {' '.join((s['claim'][:-3] if s['claim'].endswith(' at') else s['claim'].split(' at ')[0]).split(':path:')[-1] for s in solves)}")
        first_undecided = marks.find("?")
        last_decided = marks.rfind("D")
        print(f"    first '?' at index {first_undecided}, "
              f"last 'D' at index {last_decided}, "
              f"tail-only={'yes' if first_undecided > last_decided else 'no'}")

    # ---- 4c. how tight is the undecided time band? --------------------------
    print("\n[4c] DECISION-PROCEDURE TIME HISTOGRAM (0.5 s buckets, all runs)")
    hist = defaultdict(Counter)
    for j in joined:
        if j["dp_s"] is None:
            continue
        b = int(j["dp_s"] * 2) / 2.0
        hist[b]["D" if j["verdict"] == "PASSED" else "?"] += 1
    print(f"    {'bucket':>8} {'decided':>8} {'undecided':>10}")
    for b in sorted(hist):
        e = hist[b]
        print(f"    {b:>8.1f} {e['D']:>8} {e['?']:>10}")

    # ---- 4d. the decision vector behind each path id ------------------------
    # path_id is the decision prefix read as a binary number with a leading
    # sentinel bit: id 2 = '10' = one decision taken FALSE, id 7 = '111' = two
    # decisions taken TRUE. path_depth is exactly bitlength-1 for every claim in
    # this corpus, and that identity is CHECKED here rather than assumed -- if
    # it fails the decode is wrong and every row below it is meaningless.
    print("\n[4d] DECISION VECTOR per claim (id decoded, checked against "
          "path_depth)")
    bad = []
    for c in claims:
        pid = c.get("path_id")
        try:
            n = int(pid)
        except (TypeError, ValueError):
            bad.append((c["_unit"], pid, "not an integer"))
            continue
        bits = bin(n)[2:]
        if len(bits) - 1 != c.get("path_depth"):
            bad.append((c["_unit"], pid, f"bits={bits} depth={c.get('path_depth')}"))
    print(f"  claims whose decoded bitlength-1 != path_depth: {len(bad)}")
    for b in bad:
        print(f"    MISMATCH {b}")
    if not bad:
        for unit in sorted(per_unit_depth):
            print(f"  {unit}")
            print(f"    {'id':>6} {'decisions':<14} {'dep':>3} "
                  f"{'exit':<8} {'revert_kind':<10} {'reason'}")
            us = [c for c in claims if c["_unit"] == unit]
            for c in sorted(us, key=lambda c: bin(int(c["path_id"]))[3:]):
                print(f"    {c['path_id']:>6} {bin(int(c['path_id']))[3:]:<14} "
                      f"{c['path_depth']:>3} {str(c.get('exit_kind')):<8} "
                      f"{str(c.get('revert_kind')):<10} {c.get('u_reason')}")

    # is the undecided set exactly the minimum-depth claims of its unit?
    print("\n  is 'solver-unknown' the same set as 'shallowest in its unit'?")
    print(f"    {'unit':<24} {'min depth of unk':>17} "
          f"{'max depth of unk':>17} {'min depth of bhold':>19}")
    for unit in sorted(per_unit_depth):
        e = per_unit_depth[unit]
        u = [d for d, _, _ in e.get("solver-unknown", [])]
        b = [d for d, _, _ in e.get("bounded-holds", [])]
        print(f"    {unit:<24} {(min(u) if u else '-'):>17} "
              f"{(max(u) if u else '-'):>17} {(min(b) if b else '-'):>19}")

    # ---- 4e. does the undecided time scale with formula size? --------------
    # NOTE ON THE THIRD STATE. A solve has exactly three outcomes in these logs:
    # '✓ PASSED' (UNSAT, the claim holds), '✗ FAILED' (SAT, a counterexample was
    # built) and NEITHER (no verdict). An earlier version of this table counted
    # "not PASSED" as undecided, which silently merged the SAT results with the
    # no-verdict ones -- on every benchmark except st1inch that column was
    # reporting witnesses as failures to decide. The three are kept apart here.
    print("\n[4e] FORMULA SIZE vs SOLVE OUTCOME "
          "(a limit does not scale with size; a search does)")
    print(f"  {'tag':<32} {'VCCgen':>7} {'VCCrem':>7} {'assigns':>9} "
          f"{'symex_s':>8} {'nPASS':>6} {'nSAT':>5} {'nNONE':>6} "
          f"{'minNONE':>8} {'maxNONE':>8} {'maxSAT':>7}")
    for tag in sorted(all_meta):
        m = all_meta[tag]
        vcc = m.get("vcc") or []
        gen = vcc[0]["gen"] if vcc else "n/r"
        rem = vcc[0]["rem"] if vcc else "n/r"
        asg = vcc[0]["asg"] if vcc else "n/r"
        ss = all_solves.get(tag, [])
        npass = sum(1 for s in ss if s["verdict"] == "PASSED")
        sat = [s["dp_s"] for s in ss
               if s["verdict"] == "FAILED" and s["dp_s"] is not None]
        non = [s["dp_s"] for s in ss
               if s["verdict"] is None and s["dp_s"] is not None]
        print(f"  {tag:<32} {gen:>7} {rem:>7} {asg:>9} "
              f"{(m.get('symex_s') or {}).get('s', 'n/r'):>8} "
              f"{npass:>6} {len(sat):>5} {len(non):>6} "
              f"{(f'{min(non):.3f}' if non else '-'):>8} "
              f"{(f'{max(non):.3f}' if non else '-'):>8} "
              f"{(f'{max(sat):.3f}' if sat else '-'):>7}")

    # ---- 4f. which loops were truncated in each run -------------------------
    # Every run compiles the SAME contract with the same constructor, so a
    # whole-contract property cannot explain why one unit's depth-1 claim is
    # decided in 15 ms and another's is not. The truncated-loop list is the one
    # per-run record of WHICH parts of the contract the focused unit actually
    # dragged into its encoding, so it is the available proxy for "what is in
    # this unit's formula that is not in that one's".
    print("\n[4f] TRUNCATED LOOPS per run, against the unit's unknown rate")
    RE_LOOP = re.compile(r"^WARNING:\s+loop (?P<n>\d+) at file (?P<f>\S+) "
                         r"line (?P<l>\d+)(?: column \d+)? function (?P<g>\S+)$")
    loops_by_tag = {}
    for tag in sorted(all_solves):
        lp = bench / "work" / tag / "run.log"
        got = []
        for raw in lp.read_text(errors="replace").splitlines():
            m = RE_LOOP.match(raw.strip())
            if m:
                key = f"{m.group('g')}@{Path(m.group('f')).name}:{m.group('l')}"
                if key not in got:
                    got.append(key)
        loops_by_tag[tag] = got
    groups = defaultdict(list)
    for tag, got in loops_by_tag.items():
        groups[tuple(sorted(got))].append(tag)
    for sig in sorted(groups, key=lambda s: (len(s), s)):
        tags = sorted(groups[sig])
        print(f"\n  loop set ({len(sig)} loop(s)) shared by {len(tags)} run(s)")
        for l in sig:
            print(f"      {l}")
        print(f"    {'run':<32} {'paths':>6} {'unk':>5} {'rate':>7}")
        for t in tags:
            unit = t.split("__", 1)[1] if "__" in t else t
            e = per_unit.get(unit)
            if e is None:
                print(f"    {t:<32} {'no report':>6}")
            else:
                print(f"    {t:<32} {e['_total']:>6} "
                      f"{e['solver-unknown']:>5} "
                      f"{pct(e['solver-unknown'], e['_total']):>7}")

    # ---- 4g. the CE-preservation slicing exemption --------------------------
    # `--cov-report-json` exempts symbols from slicing so a counterexample's
    # values survive into the report. That set is per CONTRACT, not per unit, so
    # it is identical across a benchmark's runs and differs BETWEEN benchmarks.
    # It is printed here because `solver-unknown` is a st1inch-only outcome and
    # any candidate cause has to be a quantity that separates st1inch from the
    # benchmarks that produce witnesses.
    RE_EXEMPT = re.compile(
        r"exempting (?P<n>\d+) symbol\(s\) from slicing.*?"
        r"\((?P<obj>\d+) contract object\(s\), (?P<sto>\d+) contract-scope "
        r"store\(s\), (?P<env>\d+) environment\)")
    print("\n[4g] CE-PRESERVATION SLICING EXEMPTION per run")
    print(f"  {'run':<32} {'symbols':>8} {'objects':>8} {'stores':>7} "
          f"{'env':>5} {'F':>4} {'unk':>5}")
    for tag in sorted(all_solves):
        lp = bench / "work" / tag / "run.log"
        hit = None
        for raw in lp.read_text(errors="replace").splitlines():
            m = RE_EXEMPT.search(raw)
            if m:
                hit = m.groupdict()
                break
        unit = tag.split("__", 1)[1] if "__" in tag else tag
        e = per_unit.get(unit)
        f_ct = sum(1 for c in claims
                   if c["_unit"] == unit and c.get("status") == "F")
        if hit is None:
            print(f"  {tag:<32} {'not recorded':>8}")
        else:
            print(f"  {tag:<32} {hit['n']:>8} {hit['obj']:>8} "
                  f"{hit['sto']:>7} {hit['env']:>5} {f_ct:>4} "
                  f"{(e['solver-unknown'] if e else 'n/r'):>5}")

    # ---- 5. budget / limit words verbatim -----------------------------------
    print("\n[6] EVERY LOG LINE MENTIONING A BUDGET/LIMIT/UNKNOWN WORD "
          "(verbatim, deduplicated by shape)")
    shapes = Counter()
    for tag, m in all_meta.items():
        for l in m.get("budget_word_lines", []):
            shapes[re.sub(r"[0-9]+\.[0-9]+|[0-9]+", "#", l)] += 1
    for shape, n in shapes.most_common():
        print(f"  [{n:>4}] {shape}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
