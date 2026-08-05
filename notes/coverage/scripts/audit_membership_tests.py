#!/usr/bin/env python3
"""Report every `<string literal> in <name>` where <name> holds a LIST.

---- WHY THIS EXISTS ------------------------------------------------------

A substring test against a list of lines is FALSE for every substring, because
`in` on a list compares ELEMENTS. Written positively that is harmless -- the
check fails loudly and gets fixed. Written NEGATIVELY it is an always-true
reader:

    bad += check("qty" not in put, "the bad name is never emitted")

passes whether or not `qty` was emitted, and passes just as cheerfully if the
whole feature is deleted. This project has an entry for exactly that failure
shape ("恒真判读器"): the code is wired, it runs every time, and its verdict is
a constant.

Found in `test_solidity_path_put.py` by a positive check in the same test
failing while the two negative ones beside it "passed". Two of the three
checks guarding a REFUSAL branch were vacuous. This tool exists so the next
one is found by running something rather than by getting lucky.

⛔ THIS IS A CLASSIFIER, NOT A SEARCH. Every membership comparison in the file
is put in one of two buckets and the two counts are printed against the total,
so a caller can see the tool looked at all of them.

usage:
    audit_membership_tests.py <file.py> [more files ...] [--names put,body]

exit 1 if any suspect comparison exists, 0 otherwise.
"""
import ast
import sys

# Names known to be bound to a list of source lines. Extend with --names.
DEFAULT_LISTISH = ("put",)


def audit(path, listish_names):
    src = open(path, errors="replace").read()
    tree = ast.parse(src)
    lines = src.splitlines()

    total = 0
    suspect = []
    other = 0

    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        # Which of the watched names this function actually binds. A function
        # that never assigns `put` cannot be testing against the list one.
        bound = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign):
                targets = []
                for t in n.targets:
                    targets += t.elts if isinstance(t, ast.Tuple) else [t]
                for t in targets:
                    if isinstance(t, ast.Name) and t.id in listish_names:
                        bound.add(t.id)
        for n in ast.walk(fn):
            if not isinstance(n, ast.Compare):
                continue
            for op, cmp in zip(n.ops, n.comparators):
                if not isinstance(op, (ast.In, ast.NotIn)):
                    continue
                total += 1
                if (isinstance(n.left, ast.Constant)
                        and isinstance(n.left.value, str)
                        and isinstance(cmp, ast.Name)
                        and cmp.id in bound):
                    suspect.append(
                        (n.lineno, fn.name, cmp.id,
                         "not in" if isinstance(op, ast.NotIn) else "in"))
                else:
                    other += 1

    print("%s: %d membership comparison(s)" % (path, total))
    print("    %d substring against a LIST  <-- vacuous when negated"
          % len(suspect))
    print("    %d other" % other)
    for ln, fname, name, kind in sorted(suspect):
        sev = "⛔ ALWAYS TRUE" if kind == "not in" else "   always false"
        print("    %5d  %s  `%s`  in %s" % (ln, sev, name, fname))
        print("           %s" % lines[ln - 1].strip())
    print("    checksum: %d + %d = %d" % (len(suspect), other, total))
    return len(suspect)


# ---- THE DISCRIMINATOR HAS TO BE SEEN FIRING ------------------------------
#
# A clean report from a detector that has never fired is indistinguishable
# from a clean report from a detector that CANNOT fire, and this project has
# been bitten by that three times. So the tool carries the shape it is looking
# for and classifies it on every run: `--selftest` must find exactly two
# suspects here, and the third case -- the same literal against a name the
# function never bound to a list -- must NOT be flagged, or the tool would
# report every ordinary string test in the file.
SELFTEST_SRC = '''
def t_bad():
    put, stats = build_put()
    assert "qty" not in put          # 1: vacuous, the reason this tool exists
    assert "amount" in put           # 2: always false, same root cause

def t_good():
    put, stats = build_put()
    body = "\\n".join(put)
    assert "qty" not in body         # correct: compares against text
    assert "x" in some_dict          # not a watched name
    assert 3 in [1, 2, 3]            # not a string literal
'''


def selftest():
    import tempfile
    import os
    fd, p = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(SELFTEST_SRC)
    try:
        n = audit(p, ("put",))
    finally:
        os.unlink(p)
    ok = (n == 2)
    print("\nSELFTEST: %s -- the detector flagged %d of the 2 planted shapes "
          "and left the 3 innocent ones alone"
          % ("FIRES" if ok else "⛔ DID NOT FIRE AS SPECIFIED", n))
    return 0 if ok else 1


def main(argv):
    args = argv[1:]
    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        return 0
    if "--selftest" in args:
        return selftest()
    names = list(DEFAULT_LISTISH)
    if "--names" in args:
        i = args.index("--names")
        names = args[i + 1].split(",")
        args = args[:i] + args[i + 2:]
    bad = 0
    for p in args:
        bad += audit(p, tuple(names))
    print("\n%s" % ("⛔ %d suspect comparison(s)" % bad if bad
                    else "no substring-against-a-list comparison found"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
