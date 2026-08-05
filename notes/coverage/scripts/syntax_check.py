#!/usr/bin/env python3
"""Syntax-check ONE translation unit, using the build's own compile command.

---- WHY THIS IS NOT A BUILD -----------------------------------------------

It compiles with `-fsyntax-only`: no object file, no archive, no link, and
above all NO NEW BINARY. That matters beyond speed -- relinking would change
the executable's mtime, and every `put.json` on disk records the mtime of the
executable that produced it, so a rebuild silently marks the whole existing
deliverable set STALE. A syntax check leaves all of that untouched.

It is also the only verification available while running the verifier is
forbidden. It catches exactly the class of mistake a large hand edit to a
6000-line function introduces -- a name that no longer exists, a type that does
not convert, an argument count that no longer matches the format string -- and
it catches NONE of the class that matters more.

⛔ A CLEAN SYNTAX CHECK IS NOT EVIDENCE THE CHANGE WORKS. It says the compiler
accepted the text. Whether the new branch is ever ENTERED, and whether it
computes the right thing when it is, are facts only a run establishes. Say
"compiles" and never "works".

usage:
    syntax_check.py <source-file> [--db build/compile_commands.json]
"""
import json
import os
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ESBMC_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEFAULT_DB = os.path.join(ESBMC_ROOT, "build", "compile_commands.json")

# Flags that would produce output or change what the compiler is asked to do.
DROP_WITH_ARG = ("-o", "-MF", "-MT", "-MQ")
DROP = ("-c", "-MD", "-MMD", "-MP")


def main(argv):
    args = argv[1:]
    db = DEFAULT_DB
    if "--db" in args:
        i = args.index("--db")
        db = args[i + 1]
        args = args[:i] + args[i + 2:]
    if not args:
        sys.exit(__doc__)
    target = os.path.abspath(args[0])

    with open(db) as f:
        entries = json.load(f)

    hits = [e for e in entries
            if os.path.abspath(os.path.join(e.get("directory", ""),
                                            e["file"])) == target]
    print("%d compile-database entry(ies) for %s" % (len(hits), target))
    if not hits:
        print("⛔ the build does not know this file. Either it is not compiled "
              "into any target, or the database predates it -- re-run cmake "
              "before reading a clean result as meaningful.")
        return 2
    if len(hits) > 1:
        print("more than one entry; checking each, because they may differ in "
              "defines and a change can compile under one and not the other")

    worst = 0
    for e in hits:
        cmd = shlex.split(e["command"]) if "command" in e else list(e["arguments"])
        out, skip = [], False
        for tok in cmd:
            if skip:
                skip = False
                continue
            if tok in DROP_WITH_ARG:
                skip = True
                continue
            if tok in DROP:
                continue
            out.append(tok)
        out.append("-fsyntax-only")
        print("=" * 92)
        print("cwd: %s" % e.get("directory"))
        print("cmd: %s" % " ".join(shlex.quote(t) for t in out))
        r = subprocess.run(out, cwd=e.get("directory") or ".",
                           capture_output=True, text=True)
        if r.stdout:
            print(r.stdout)
        if r.stderr:
            print(r.stderr)
        print("exit=%d" % r.returncode)
        worst = max(worst, r.returncode)
    print("=" * 92)
    print("SYNTAX CHECK %s. This says the compiler ACCEPTED the text; it says "
          "nothing about whether the new code is ever reached or is correct."
          % ("PASSED" if worst == 0 else "FAILED"))
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv))
