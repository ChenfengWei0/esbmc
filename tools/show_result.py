#!/usr/bin/env python3
"""One-screen view of a generalise-result.json.

The raw file carries every path's full counterexample -- twenty environment
quantities each -- so reading it whole to answer "did anything certify, and how
wide is it" costs a screen of noise per path. This prints the two facts a caller
acts on, and prints the NOT-certified reasons in full because that is where the
information is when the answer is no.

WIDTH IS COMPUTED, not eyeballed. A coordinate certified as [v, v] is a concrete
test with extra syntax; the whole point of stage 2 is the coordinates whose width
is greater than 1, so the count of those is the number that decides whether a
PUT is possible at all.
"""
import json
import sys


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    with open(argv[1]) as fh:
        d = json.load(fh)
    print(f"unit      {d.get('contract')}.{d.get('unit')}  max_tx={d.get('max_tx')}")
    print(f"pins      {d.get('pins')}")
    print(f"enumerated {len(d.get('enumerated') or [])} witnessed path(s)")
    print()
    cert = d.get("certified") or []
    print(f"CERTIFIED: {len(cert)}")
    for c in cert:
        wide = 0
        print(f"  enc={c['enc']} piece={c.get('piece')} depth={c.get('depth')}")
        for b in c["box"]:
            lo, hi = int(b["lo"]), int(b["hi"])
            n_holes = len(b.get("holes") or [])
            width = hi - lo + 1 - n_holes
            if width > 1:
                wide += 1
            mark = "  <-- WIDTH > 1" if width > 1 else "  (single point)"
            print(f"      {b['name']:<22} [{b['lo']}, {b['hi']}]"
                  + (f" \\ {b['holes']}" if n_holes else "")
                  + f"   width={width}{mark}")
        print(f"      => {wide} coordinate(s) with width > 1"
              + ("  ** this is a PUT **" if wide else
                 "  ** every coordinate is a point: a concrete test with extra "
                 "syntax, NOT a PUT **"))
    print()
    nc = d.get("not_certified") or []
    print(f"NOT CERTIFIED: {len(nc)}")
    for n in nc:
        print(f"  enc={n['enc']} depth={n.get('depth')}:")
        print(f"      {n['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
