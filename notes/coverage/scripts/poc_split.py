#!/usr/bin/env python3
"""SPLIT THE CORPUS INTO ONE POC PER TARGET UNIT, AND MAKE THE WHOLE-BENCHMARK
RUN IMPOSSIBLE.

WHY THIS EXISTS. Every sweeper in this tree takes a BENCHMARK key and walks
every unit of it. That shape is what turns a one-unit question into a
five-hour run, and it is the shape the operator has banned. After this script
runs there is no benchmark to run: there is a list of PoCs, each naming
exactly ONE target public/external function, and both drivers REFUSE to start
without being told which one.

⛔ WHAT A POC IS, AND WHAT IT IS NOT.

A PoC is ONE TARGET UNIT, not a reduced source file. The contract keeps every
other entry it declares, and that is deliberate rather than lazy:

    MEASURED (notes/coverage/INVOCATION_DECISIONS.md rows 1-2, poc/Tiny.sol):
      --focus-function withdraw, tx=1   5 paths, F 3, 60%
      whole contract,           tx=1   8 paths, F 6, 75%
      whole contract,           tx=2   8 paths, F 8, 100%

    One transaction is EXACTLY one entry call -- each dispatcher arm ends in a
    `return` (solidity_convert_constructor.cpp:445) -- so the reachable call
    sequences are the words over the focus alphabet. A source reduced to ONE
    public function has an alphabet of size one, every word is f^k, and NO tx
    bound reaches cross-function state. Deleting the siblings would therefore
    bake the one configuration already measured to be unable to witness a
    state-guarded path into every PoC, permanently.

So the two sets are kept apart, exactly as the tool itself keeps them apart:

    TARGET (denominator)  --path-cov-instrument-only <unit>   exactly one name
    ALPHABET (dispatch)   --focus-function <unit>[,writers]   one or more

`--path-cov-instrument-only` is refused by ESBMC unless it is a subset of the
focus set (options.cpp:153-165), so the two cannot drift apart silently.

WHAT THIS SCRIPT WRITES. `notes/coverage/poc_units/index.json` plus one
`<poc-id>/poc.json` per unit. Nothing else, and it runs no esbmc: the unit
enumeration is the AST walk `collect.enumerate_own_callable_functions`, which
is the SAME rule the baseline collector uses (METHODOLOGY 3). One fact, one
place -- a second enumerator here would be the defect this repository keeps
paying for.

Usage:
    python3 poc_split.py            # write the index and print the table
    python3 poc_split.py --list     # print the table from the existing index
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import collect as base  # noqa: E402  -- the LOCKED unit-enumeration rule

REPO = Path("/home/samson/workspace/esbmc")
INPUTS = REPO / "notes/coverage/inputs"
POCS = REPO / "notes/coverage/poc_units"


def visibility_map(flat, project):
    """(contract, fn) -> visibility, read from the same AST, for the same flat.

    WHY IT IS READ AT ALL. `collect.enumerate_own_callable_functions` is the
    LOCKED scope rule and is reused verbatim for WHICH FILES are in scope --
    but its visibility filter is the BRANCH-COVERAGE baseline's, and that
    baseline routes library functions through `--function <name>`:

        allowed = ((cur_kind == "library"
                    and vis in ("public", "external", "internal"))
                   or (cur_kind == "contract" and vis in ("public", "external")))

    `--function` is BANNED here (INVOCATION_DECISIONS row 10): it verifies from
    an ARBITRARY contract state, so its counterexample may rest on a state no
    `constructor() -> tx sequence` reaches, which becomes a RED test on the
    unmodified contract. Under path coverage a UNIT is what the frontend built
    `#_sol_save_this` for -- public / external / receive / fallback -- and
    `solidity_path_coverage` skips everything else outright
    (`goto_coverage.cpp`, the `is_external_entry` test and the non-unit
    `continue`). A library INTERNAL function is therefore not a unit at all;
    its decisions are covered through the callers that inline it.

    So this reads ONE extra field off the AST that the locked enumerator does
    not return. It is not a second scope rule -- the file scope still comes
    entirely from `enumerate_own_callable_functions`.
    """
    from ast_decisions import extract_ast_json
    solast = Path(str(flat) + ".solast")
    ast = extract_ast_json(solast)
    out = {}

    def walk(n, cur=None):
        if isinstance(n, list):
            for v in n:
                walk(v, cur)
            return
        if not isinstance(n, dict):
            return
        if n.get("nodeType") == "ContractDefinition":
            cur = n.get("name")
        if (n.get("nodeType") == "FunctionDefinition" and cur
                and n.get("name")):
            # A name declared twice in one contract is an overload; both are
            # units and they share a visibility in every case this corpus has,
            # so the first wins and the duplicate is not silently widened.
            out.setdefault((cur, n["name"]), n.get("visibility"))
        for v in n.values():
            if isinstance(v, (list, dict)):
                walk(v, cur)

    walk(ast)
    return out


def materialise_inputs(d, flat, solast):
    """Give this PoC its OWN copy of the two files ESBMC reads, plus the scope
    file, and return the private directory holding them.

    WHY. The shared `notes/coverage/inputs/` directory IS the benchmark: every
    driver resolves `INPUTS / <flat basename>` from a benchmark key, so as long
    as that directory exists a whole-corpus sweep is one command away. The
    operator's instruction is to delete it. A PoC that still pointed at it
    would die with the directory, so the input moves INTO the PoC first.

    HARDLINKED, not copied. The corpus is 6.3 MiB and 50 PoCs would be 90 MiB
    of identical bytes; a hardlink is the same inode, so this costs nothing and
    -- more to the point -- makes it IMPOSSIBLE for a PoC's input to drift from
    the bytes the corpus row was measured on. These files are read-only inputs;
    nothing in this tree writes to them.

    THE BASENAME IS KEPT EXACTLY. Every driver builds its path as
    `<inputs dir> / <basename from its own table>`, so keeping the name means
    the only thing that has to change downstream is WHICH directory is used --
    one constant, not a path-building rewrite in twenty-six scripts.
    """
    inp = d / "inputs"
    inp.mkdir(exist_ok=True)
    made = []
    for src in (flat, solast, INPUTS / "own_contracts.json"):
        if not src.exists():
            sys.exit(
                f"missing {src}. The per-PoC input directory is built FROM the "
                f"corpus, so this must run while the corpus is still on disk. "
                f"If it has already been deleted: git checkout -- "
                f"notes/coverage/inputs/")
        dst = inp / src.name
        if dst.exists():
            if dst.stat().st_ino == src.stat().st_ino:
                continue
            dst.unlink()
        os.link(src, dst)
        made.append(dst.name)
    return inp, made


def poc_id(bench, contract, fn):
    """A PoC's name in every later table.

    Carries the BENCHMARK, the DECLARING CONTRACT and the FUNCTION, because a
    unit inherited from a base is legitimately a unit of the contract under
    test (BaseEscrow.rescueFunds is measured under EscrowSrc) and dropping the
    declaring contract would make two different units share one id.
    """
    return f"{bench}__{contract}__{fn}"


def build():
    if not POCS.exists():
        POCS.mkdir(parents=True)
    index = {
        "schema": "poc-units/3",
        "what": ("one PoC = one TARGET public/external function OF A CONTRACT. "
                 "The contract keeps its other entries so the dispatcher "
                 "alphabet can still reach cross-function state at "
                 "--solidity-max-tx >= 2; the target is pinned by "
                 "--path-cov-instrument-only, which is what makes the "
                 "denominator one unit's own"),
        "not_a_unit": (
            "A LIBRARY FUNCTION IS NOT A UNIT AND GETS NO POC. Under path "
            "coverage a unit is a function the frontend built #_sol_save_this "
            "for -- public / external / receive / fallback -- and a library "
            "INTERNAL function is not one: its decisions are covered through "
            "the callers that physically inline it. A library PUBLIC/EXTERNAL "
            "function IS a unit by visibility but has no dispatcher harness, "
            "so it can never be entered; that is a stated applicability limit, "
            "counted below rather than dressed up as a PoC nobody can run."),
        "excluded": {},
        "pocs": [],
    }
    rows = []
    for bench in sorted(base.BENCHES):
        flat_rel, primary, _solc, project = base.BENCHES[bench]
        flat = INPUTS / flat_rel
        solast = INPUTS / (flat_rel + ".solast")
        if not solast.exists():
            sys.exit(
                f"{bench}: missing AST {solast}. Refusing to emit a PoC list "
                f"built from a partial enumeration -- an empty unit list reads "
                f"as 'this contract has no units'.\n"
                f"  THE EXPECTED CAUSE: the shared corpus directory has been "
                f"DELETED on purpose (it was the benchmark). The 50 existing "
                f"PoCs each own their input and still run; only REBUILDING the "
                f"list needs the corpus back.\n"
                f"    git checkout -- notes/coverage/inputs/   # tracked, "
                f"nothing was lost\n"
                f"    python3 notes/coverage/scripts/poc_split.py\n"
                f"    rm -rf notes/coverage/inputs             # put it back "
                f"out of reach\n"
                f"  To only LOOK at the current list, no corpus needed:\n"
                f"    python3 notes/coverage/scripts/poc_split.py --list")
        kind = base.primary_contract_kind(flat, primary)
        units = base.enumerate_own_callable_functions(flat, project)
        vis = visibility_map(flat, project)
        if not units:
            # NOT silently skipped. A benchmark whose enumeration is empty is a
            # finding about the scope rule or the AST, never a property of the
            # contract, and it must be visible in the table rather than absent
            # from it.
            rows.append((bench, primary, kind, 0, 0, 0, "NO UNIT ENUMERATED"))
            continue
        lib_internal, lib_entry, n_unit = 0, 0, 0
        for cname, fname, ckind in units:
            if ckind == "library":
                # ⛔ NOT A POC. Split into the two cases, because they are
                # different facts and only one of them is a limitation:
                #   internal        -> not a unit at all; covered through the
                #                      callers that inline it. Nothing is lost.
                #   public/external -> a unit by visibility with no dispatcher
                #                      to enter it. THAT is the applicability
                #                      limit, and it is the smaller number.
                v = vis.get((cname, fname))
                if v in ("public", "external"):
                    lib_entry += 1
                    index["excluded"].setdefault(
                        "library_entry_points", []).append(
                            f"{bench}::{cname}.{fname} ({v})")
                else:
                    lib_internal += 1
                continue
            v = vis.get((cname, fname))
            if v not in ("public", "external"):
                # The locked enumerator already restricts contracts to
                # public/external, so reaching here means the AST and that
                # filter disagree. Refuse rather than emit a PoC for something
                # the dispatcher will not carry -- an instrumented unit the
                # harness cannot enter reports every path 'unit-not-entered',
                # which reads as 'nothing reaches this code'.
                sys.exit(
                    f"{bench}: {cname}.{fname} came out of the locked "
                    f"callable enumeration but its AST visibility is {v!r}, "
                    f"not public/external. The two disagree; refusing to "
                    f"guess which is right")
            n_unit += 1
            pid = poc_id(bench, cname, fname)
            d = POCS / pid
            d.mkdir(exist_ok=True)
            inp, _made = materialise_inputs(d, flat, solast)
            poc = {
                "schema": "poc-unit/2",
                "id": pid,
                "benchmark": bench,
                "project": project,
                # ⛔ THESE POINT INSIDE THE POC, NOT AT THE CORPUS. The shared
                # corpus directory is deleted; this PoC's private hardlinks are
                # the only copy any run of it reads. `inputs_dir` is what
                # poc_one.py hands the drivers as their inputs directory.
                "inputs_dir": str(inp),
                "flat": str(inp / flat.name),
                "ast": str(inp / solast.name),
                # The --contract ESBMC is given: the harness entry, which is the
                # benchmark's primary, NOT the declaring contract. A unit
                # inherited from a base is dispatched through the primary.
                "harness_contract": primary,
                "declaring_contract": cname,
                "declaring_kind": ckind,
                "unit": fname,
                "visibility": v,
                # THE TWO COMMAND LINES, both recorded, never merged. A run of
                # one may not be quoted as the other
                # (notes/coverage/INVOCATION_DECISIONS.md, "The settled command
                # line").
                "cells": {
                    "gate": {
                        "why": ("compared against the branch-coverage baseline, "
                                "which is MEASURED to run at one transaction; "
                                "running deeper than the thing being compared "
                                "to is not a comparison"),
                        "scope": "single",
                        "max_tx": 1,
                        "focus_function": fname,
                        "instrument_only": fname,
                    },
                    "artefact": {
                        "why": ("no second party to match, so the question is "
                                "what this method can REACH. tx>=2 is the only "
                                "configuration measured to reach cross-function "
                                "state at all"),
                        "scope": "set",
                        "max_tx": 2,
                        # EMPTY UNTIL SOMEONE CHOOSES IT, and left empty rather
                        # than guessed. The rule is `{unit} + the functions that
                        # WRITE what the unit reads`; nothing in this tree
                        # computes that set and wiring a guess in here would
                        # make every artefact cell a measurement of the guess.
                        "focus_with": [],
                        "instrument_only": fname,
                    },
                },
            }
            (d / "poc.json").write_text(json.dumps(poc, indent=2) + "\n")
            index["pocs"].append(pid)
        note = ""
        if n_unit == 0:
            # A BARE 0 IN A TABLE READS AS "small", NOT AS "this benchmark
            # contributes nothing". It has to say so. Reaching here means every
            # function the locked enumerator returned was a library function,
            # which means the branch-coverage baseline for this benchmark was
            # measured entirely through `--function` -- the route banned here.
            # That makes the benchmark uncomparable at the gate, not merely
            # empty, and the two are different findings.
            note = ("NO UNIT: every enumerated function is a library function, "
                    "so this benchmark has no public/external contract entry "
                    "and contributes nothing to the gate")
        rows.append((bench, primary, kind, n_unit, lib_internal, lib_entry,
                     note))
    # ONE FACT, ONE PLACE. The table is stored WITH the index rather than
    # recomputed by `--list` from the per-PoC files: the non-unit counts do not
    # live in any poc.json (there is no poc.json for a non-unit), so a `--list`
    # that rebuilt the table would have to print zeros for them and would
    # silently disagree with the run that wrote it.
    index["table"] = rows
    (POCS / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    reconcile(index)
    return index, rows


def reconcile(index):
    """Delete PoC directories the CURRENT index no longer names.

    An earlier version of this script emitted a PoC per LIBRARY function. Those
    directories do not disappear when the rule is corrected, and a stale
    `poc.json` on disk is worse than no PoC: `poc_one.py` loads it by id and
    would run it, so the corrected rule would be true only in the index.

    A directory holding ANYTHING besides its own poc.json is KEPT and reported.
    That is run output -- a report, a counterexample, a certify jsonl -- and
    this script is not the thing that decides measured output is disposable.
    """
    named = set(index["pocs"])
    removed, kept = [], []
    for d in sorted(POCS.iterdir()):
        if not d.is_dir() or d.name in named:
            continue
        # `inputs/` is this script's own product (hardlinks to the corpus), not
        # run output, so its presence must not make a stale directory look
        # precious -- otherwise every obsolete PoC would be kept forever.
        extra = sorted(p.name for p in d.iterdir()
                       if p.name not in ("poc.json", "inputs"))
        if extra:
            kept.append((d.name, extra))
            continue
        shutil.rmtree(d)
        removed.append(d.name)
    if removed:
        print(f"[reconcile] removed {len(removed)} stale PoC director(ies) no "
              f"longer named by the index:")
        for n in removed:
            print(f"    - {n}")
    if kept:
        print(f"[reconcile] ⚠ KEPT {len(kept)} stale director(ies) because they "
              f"hold run output. They are NOT in the index, and poc_one.py "
              f"REFUSES an id the index does not name -- so the numbers in "
              f"them belong to the OLD unit rule and may not be quoted:")
        for n, extra in kept:
            print(f"    ! {n}  ({', '.join(extra)})")
    if not removed and not kept:
        print("[reconcile] no stale PoC directories")
    return removed, kept


def show(rows, index):
    print("# The corpus is now a list of PoCs. There is no benchmark to run.\n")
    print("# A UNIT is a public/external function OF A CONTRACT -- what the "
          "frontend\n# built #_sol_save_this for. The last two columns are NOT "
          "units and get no\n# PoC; they are printed so the gap is a number "
          "rather than an absence.\n")
    print(f"{'benchmark':30s} {'primary':16s} {'kind':10s} "
          f"{'PoCs':>5s} {'lib-int':>8s} {'lib-ext':>8s}  note")
    tot = int_ = ext = 0
    for bench, primary, kind, n, ni, ne, note in rows:
        print(f"{bench:30s} {primary:16s} {kind:10s} "
              f"{n:5d} {ni:8d} {ne:8d}  {note}")
        tot += n
        int_ += ni
        ext += ne
    print("-" * 88)
    print(f"{'TOTAL':30s} {'':16s} {'':10s} {tot:5d} {int_:8d} {ext:8d}")
    print()
    print(f"{tot} PoC(s) written under {POCS}, every one of them runnable.")
    print(f"{int_} library INTERNAL function(s): not units at all. Their "
          f"decisions are\n     covered through the callers that inline them, "
          f"so nothing is lost here.")
    print(f"{ext} library PUBLIC/EXTERNAL function(s): units by visibility with "
          f"no\n     dispatcher to enter them. THIS is the stated applicability "
          f"limit.")
    for name in sorted(index.get("excluded", {})):
        for line in index["excluded"][name]:
            print(f"     - {line}")
    print()
    print("Run exactly one with:")
    print("    python3 notes/coverage/scripts/poc_one.py <poc-id>")
    print("A whole-benchmark run is refused by both drivers; see the refusal "
          "each of them prints.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true",
                    help="print the table from the existing index instead of "
                         "rebuilding it")
    a = ap.parse_args()
    if a.list:
        p = POCS / "index.json"
        if not p.exists():
            sys.exit(f"no {p}; run this script with no arguments first")
        index = json.loads(p.read_text())
        if index.get("schema") != "poc-units/3" or "table" not in index:
            sys.exit(
                f"{p} was written by an older version of this script "
                f"(schema {index.get('schema')!r}). Re-run poc_split.py with no "
                f"arguments; printing its table would show the OLD unit rule, "
                f"which counted library functions as units")
        show([tuple(r) for r in index["table"]], index)
        return 0
    index, rows = build()
    show(rows, index)
    return 0


if __name__ == "__main__":
    sys.exit(main())
