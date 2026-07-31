#!/usr/bin/env python3
"""Why is `ours` below the bar? -- attribution for EXECUTION_PLAN step 1.3.

`branch_gate.py` answers WHETHER complete-path enumeration reaches as many
canonical decisions as the locked branch-coverage dataset. It does not answer
WHERE the missing ones went, and the difference matters because the three
candidate causes call for three different fixes:

  (1) the unit was never entered            -> driver / entry-state work
  (2) the unit was entered, the decision's
      arm was simply never witnessed        -> exploration depth, bounds
  (3) the decision lives in an INTERNAL body
      that no unit path ever inlined        -> call-depth bound, degradation

This script buckets every MISSED canonical decision into one of those by
locating the function that syntactically encloses it and cross-referencing the
per-unit claim census from the same reports.

WHAT THIS SCRIPT DOES NOT DO, STATED SO ITS SILENCE IS NOT READ AS EVIDENCE
--------------------------------------------------------------------------
It cannot distinguish (3) "the call site was withdrawn by degradation or the
depth bound" from (3') "no unit calls this function at all". Both look like an
internal function with no witnessed decision. Separating them needs the
producer to publish `degraded_call_sites` / `sc_sites_over_cap` in
cov-report.json's `summary`; today those live only in a `log_warning` and reach
no reader (branch_gate.py's docstring records the same gap). Rows in bucket (3)
are therefore printed as ONE bucket with both readings named, not split.

It also inherits `branch_gate.pathcov_reached_flat_lines`, so a benchmark whose
runs were all killed contributes an honest zero-with-a-reason there and an
all-missed table here. That is not a finding about the method.

Usage:
    python3 notes/coverage/scripts/gap_attribution.py [bench ...]
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOTES = HERE.parent.parent          # notes/
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(NOTES))

import ast_decisions                # noqa: E402
import branch_gate                  # noqa: E402


# --------------------------------------------------------------------------
# The function index: which function encloses a given flat line
# --------------------------------------------------------------------------
def function_index(flat_path):
    """[(start_line, end_line, contract, name, visibility, kind)] from the AST.

    Read from the AST, not from a brace scan of the text: the flat contains
    library bodies, modifiers and free functions, and a brace scan puts a
    modifier's decisions inside whichever function happens to precede it.

    Ranges are kept as a LIST and searched innermost-first rather than being
    flattened into a line->function map, because ranges nest (a function inside
    a contract, a modifier body spliced by the compiler) and the innermost
    enclosing definition is the one that answers the question.
    """
    flat_path = Path(flat_path)
    b2l = ast_decisions.byte_to_line(flat_path.read_bytes())
    solast = flat_path.with_suffix(".sol.solast")
    if not solast.exists():
        solast = Path(str(flat_path) + ".solast")
    ast = ast_decisions.extract_ast_json(solast)

    out = []

    def span(node):
        s = node.get("src")
        if not s:
            return None
        start, length, _ = s.split(":")
        start, length = int(start), int(length)
        return (ast_decisions.line_of(b2l, start),
                ast_decisions.line_of(b2l, start + max(length - 1, 0)))

    def walk(node, contract):
        if isinstance(node, list):
            for c in node:
                walk(c, contract)
            return
        if not isinstance(node, dict):
            return
        nt = node.get("nodeType")
        if nt == "ContractDefinition":
            contract = node.get("name")
        elif nt in ("FunctionDefinition", "ModifierDefinition"):
            sp = span(node)
            if sp:
                if nt == "ModifierDefinition":
                    kind, vis = "modifier", "modifier"
                else:
                    kind = node.get("kind") or "function"
                    vis = node.get("visibility") or "?"
                nm = node.get("name") or ("<" + kind + ">")
                out.append((sp[0], sp[1], contract, nm, vis, kind))
        for v in node.values():
            if isinstance(v, (list, dict)):
                walk(v, contract)

    walk(ast, None)
    return out


def enclosing(index, line):
    """Innermost definition containing `line`, or None."""
    best = None
    for start, end, contract, name, vis, kind in index:
        if start <= line <= end:
            if best is None or (end - start) < (best[1] - best[0]):
                best = (start, end, contract, name, vis, kind)
    return best


UNIT_VIS = ("public", "external")


# --------------------------------------------------------------------------
# The per-unit claim census, read from the same reports the gate reads
# --------------------------------------------------------------------------
def claim_census(report_paths):
    """Per report file: status counts, U-reason counts, and the claim key
    inventory.

    The key inventory is printed rather than assumed. Every field this script
    reads off a claim is a field whose name was guessed once; printing the set
    that is actually present is what turns a silent `.get() -> None` into a
    visible discrepancy.
    """
    per_unit = {}
    keys = set()
    for p in report_paths:
        p = Path(p)
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        st = defaultdict(int)
        ureason = defaultdict(int)
        for c in d.get("claims", []):
            keys.update(c.keys())
            st[c.get("status", "?")] += 1
            if c.get("status") == "U":
                ureason[str(c.get("u_reason", "<none>"))] += 1
        s = d.get("summary", {})
        per_unit[p.stem] = {
            "status": dict(st),
            "u_reason": dict(ureason),
            "paths_total": s.get("paths_total"),
            "covered": s.get("covered"),
            "seq": s.get("decision_sequences", {}),
        }
    return per_unit, sorted(keys)


def main(argv):
    benches = argv[1:] or branch_gate.BENCHES
    print("# Gap attribution -- where the missing canonical decisions went\n")
    print("Buckets, and the fix each one implies:\n")
    print("  A  unit, no F claim at all        -> the unit was never entered")
    print("  B  unit, has F claims             -> entered; that arm unwitnessed")
    print("  C  internal/private/library body  -> either no unit path inlined")
    print("     or a modifier                     it, or the call site was")
    print("                                       withdrawn (NOT separable "
          "today)")
    print("  D  no enclosing definition        -> file-level / constructor\n")

    for bench in benches:
        print(f"\n## `{bench}`\n")
        try:
            base = branch_gate.baseline(bench)
        except (OSError, ValueError, KeyError) as e:
            print(f"baseline unreadable: {e}")
            continue

        meta, reports = branch_gate.pathcov_reports_for(bench)
        if meta is None:
            print("not collected")
            continue

        lines, st = branch_gate.pathcov_reached_flat_lines(reports)
        canon, _blocks = branch_gate.canonical_in_scope(base["flat"],
                                                        base["project"])
        index = function_index(base["flat"])
        census, keys = claim_census(reports)

        # THE UNIT SET COMES FROM THE REPORTS, NOT FROM THE AST. A public
        # function the collector never ran has no report, and calling it
        # "entered" or "not entered" from an AST walk would be inventing a
        # measurement. Missing report -> bucket A with the reason spelled out.
        f_by_fn = defaultdict(int)
        for stem, c in census.items():
            fn = stem.split("__")[-1]
            f_by_fn[fn] += c["status"].get("F", 0)

        buckets = defaultdict(list)
        for fname, c_lines in sorted(canon.items()):
            for ln in sorted(c_lines - lines):
                enc = enclosing(index, ln)
                if enc is None:
                    buckets["D"].append((fname, ln, "-", "-", "-"))
                    continue
                _s, _e, contract, name, vis, kind = enc
                if kind == "function" and vis in UNIT_VIS:
                    b = "B" if f_by_fn.get(name, 0) > 0 else "A"
                else:
                    b = "C"
                buckets[b].append((fname, ln, contract, name,
                                   f"{vis}/{kind}"))

        total_missed = sum(len(v) for v in buckets.values())
        reached_in_canon = sum(len(lines & c) for c in canon.values())
        print(f"canonical in scope: {sum(len(c) for c in canon.values())}   "
              f"reached: {reached_in_canon}   missed: {total_missed}\n")

        print("| bucket | count |")
        print("|---|---|")
        for b in ("A", "B", "C", "D"):
            print(f"| {b} | {len(buckets[b])} |")

        for b in ("A", "B", "C", "D"):
            if not buckets[b]:
                continue
            print(f"\n### bucket {b}\n")
            print("| file | flat line | contract | enclosing | vis/kind |")
            print("|---|---|---|---|---|")
            for row in buckets[b]:
                print("| {} | {} | {} | `{}` | {} |".format(*row))

        print("\n### per-report census "
              f"(claim keys present: {', '.join(keys) or '<no claims>'})\n")
        print("| report | paths | F | U | I | covered | U reasons |")
        print("|---|---|---|---|---|---|---|")
        for stem in sorted(census):
            c = census[stem]
            ur = ", ".join(f"{k}={v}" for k, v in sorted(c["u_reason"].items()))
            print(f"| `{stem}` | {c['paths_total']} | "
                  f"{c['status'].get('F', 0)} | {c['status'].get('U', 0)} | "
                  f"{c['status'].get('I', 0)} | {c['covered']} | {ur} |")

        killed = sum(1 for r in meta["runs"] if r.get("killedByOuterTimeout"))
        noreport = sum(1 for r in meta["runs"] if not r.get("reportPresent"))
        if killed or noreport:
            print(f"\n⚠ {killed} run(s) killed, {noreport} without a report -- "
                  f"every decision they would have witnessed is counted as "
                  f"missed above, so these buckets are an UPPER bound on the "
                  f"gap, not a measurement of it.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
