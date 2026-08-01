#!/usr/bin/env python3
"""Does results.jsonl carry the cause of its 59 aborted certification queries?

certify_summary.py prints, verbatim, the most common non-certification reason:

    "no verdict from the certification query (ESBMC printed neither SUCCESSFUL
     nor FAILED) -- ESBMC exited -6 (ABORTED (SIGABRT)), so the round measured
     nothing (a TOOL outcome, not a property of the path). The last ERROR line
     in its output names the cause"

That last sentence is an INSTRUCTION to go and read something. This script asks
whether the thing it points at was kept: `notes/coverage/certify/` holds only
`results.jsonl`, `results.jsonl.superseded` and `poc_results.jsonl` -- no logs.
If the ESBMC output was not preserved and no record field carries the ERROR line,
then the stated cause of the single largest loss in stage 2 is UNFOLLOWABLE from
the artefacts, and the sweep has to be re-run to learn it.

Prints the union of record keys (so a field holding it cannot be missed by
guessing a name) and every string value anywhere in a record that looks like an
ESBMC error line.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.join(HERE, "..", "certify", "results.jsonl")
MARKERS = ("ERROR", "Aborted", "SIGABRT", "terminate called", "what():",
           "Assertion", "bad_alloc")


def walk_strings(o, path=""):
    if isinstance(o, str):
        yield path, o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield from walk_strings(v, f"{path}.{k}" if path else k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk_strings(v, f"{path}[{i}]")


def main(argv):
    p = argv[1] if len(argv) > 1 else DEFAULT
    recs = []
    with open(p) as fh:
        for line in fh:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    print(f"## {p}\n   {len(recs)} record(s)\n")

    keys = {}
    for r in recs:
        for k in r:
            keys[k] = keys.get(k, 0) + 1
    print("   top-level keys (name: how many records carry it):")
    for k in sorted(keys):
        print(f"     {k:<34} {keys[k]}")

    hits, aborted = {}, 0
    for r in recs:
        blob = json.dumps(r)
        if "SIGABRT" in blob or "exited -6" in blob:
            aborted += 1
        for where, s in walk_strings(r):
            if any(m in s for m in MARKERS):
                # Strip the reason boilerplate itself: it MENTIONS "ERROR line"
                # without BEING one, and counting it would answer the question
                # with the sentence that raised it.
                if "The last ERROR line in its output names the cause" in s:
                    key = (where, "<the reason string itself, not an error line>")
                else:
                    key = (where, s[:160])
                hits[key] = hits.get(key, 0) + 1

    print(f"\n   record(s) whose JSON mentions SIGABRT / 'exited -6': {aborted}")
    print(f"\n   string values that look like a preserved ESBMC error line:")
    if not hits:
        print("     (NONE)")
    for (where, s), n in sorted(hits.items(), key=lambda kv: -kv[1]):
        print(f"     {n:>4}x  [{where}]  {s}")

    print("\n   ⇒ If the only hits are the reason string itself, the cause of "
          "the aborts is NOT\n     in this artefact, and no log was kept beside "
          "it — so it can only be recovered\n     by re-running the sweep with "
          "its output captured.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
