#!/usr/bin/env python3
"""What certified regions are already on disk, and which binary produced them.

---- WHY THE BINARY FIELD COMES FIRST --------------------------------------

Stage 4 (`put_all.py --cert <file>`) turns a certified region into a `.t.sol`
and never invokes the verifier. So every CERTIFIED row already on disk is a
deliverable waiting to be emitted -- provided it was produced by the binary in
the tree right now.

It is exactly that proviso that has gone wrong three times in this project: a
rebuild lands, an old run's numbers get read under the new name, and nothing
reports an error because a JSON row does not know when it was written. So this
script reports the `binary` grouping BEFORE the unit grouping, and refuses to
print a single aggregate over rows from different binaries.

⛔ A ROW WHOSE BINARY IS UNKNOWN IS NOT COUNTED AS CURRENT. Missing provenance
reads as "no restriction" if you let it, which is the most dangerous possible
default -- it is really "maximum restriction: nothing is known".

usage:
    cert_inventory.py                        # every .jsonl under certify/
    cert_inventory.py <file.jsonl> [...]     # named files only
    cert_inventory.py --emittable            # only rows stage 4 could use now
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ESBMC_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CERTIFY = os.path.join(ESBMC_ROOT, "notes", "coverage", "certify")


def rows_of(path):
    """Every parsable row, plus the count of ones that would not parse."""
    out, bad = [], 0
    with open(path, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                bad += 1
    return out, bad


def binary_of(r):
    """The row's provenance, as a single hashable label."""
    b = r.get("binary")
    if isinstance(b, dict):
        return "%s/%s/%s" % (b.get("head"), b.get("srcDirty"),
                             b.get("binaryMtime"))
    if b:
        return str(b)
    return "UNRECORDED"


def probe(files):
    """Dump the first CERTIFIED row's shape.

    ⛔ EXISTS BECAUSE GUESSING A FIELD NAME HAS ALREADY PRODUCED A WRONG
    REPORT IN THIS PROJECT. A count read out of a key that is not there comes
    back as 0 or None and looks exactly like a real measurement of nothing.
    """
    for path in files:
        rows, _bad = rows_of(path)
        for r in rows:
            if (r.get("bucket") or r.get("status")) != "CERTIFIED":
                continue
            print("first CERTIFIED row is in %s" % path)
            for k in sorted(r):
                v = r[k]
                kind = type(v).__name__
                size = len(v) if hasattr(v, "__len__") else ""
                shown = repr(v)
                if len(shown) > 300:
                    shown = shown[:300] + " ...(truncated for display only)"
                print("    %-26s %-6s %-5s %s" % (k, kind, size, shown))
            return 0
    print("no CERTIFIED row found in any of the given files")
    return 2


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    emittable_only = "--emittable" in argv
    if "--probe" in argv:
        fs = args or [os.path.join(CERTIFY, f)
                      for f in sorted(os.listdir(CERTIFY)) if f.endswith(".jsonl")]
        return probe(fs)
    if args:
        files = args
    else:
        files = [os.path.join(CERTIFY, f) for f in sorted(os.listdir(CERTIFY))
                 if f.endswith(".jsonl")]

    per_binary = {}       # binary -> bucket -> count
    per_unit = {}         # (binary, bucket) -> [(file, bench, unit, npaths)]
    keys_seen = set()
    total, badtotal = 0, 0

    for path in files:
        rows, bad = rows_of(path)
        total += len(rows)
        badtotal += bad
        print("%-58s %5d row(s)%s"
              % (os.path.basename(path), len(rows),
                 "  ⛔ %d unparsable" % bad if bad else ""))
        for r in rows:
            keys_seen.update(r.keys())
            b = binary_of(r)
            bucket = r.get("bucket") or r.get("status") or "NO-BUCKET"
            per_binary.setdefault(b, {})
            per_binary[b][bucket] = per_binary[b].get(bucket, 0) + 1
            # ⛔ FIELD NAMES TAKEN FROM --probe, NOT GUESSED. `benchmark` (not
            # `bench`), and the regions live in `certified`, a dict keyed by
            # path encoding. Reading a key that is absent returns 0/None and is
            # indistinguishable from a real measurement of nothing.
            cert = r.get("certified") or {}
            if bucket == "CERTIFIED" or cert:
                per_unit.setdefault((b, "CERTIFIED"), []).append(
                    (os.path.basename(path), r.get("benchmark"), r.get("unit"),
                     len(cert), len(r.get("not_certified") or {}),
                     r.get("witnessed")))

    print("=" * 92)
    print("%d row(s) across %d file(s); %d unparsable"
          % (total, len(files), badtotal))
    print("row keys ever seen: %s" % ", ".join(sorted(keys_seen)))
    print("-" * 92)
    print("BY BINARY -- rows from different binaries are NEVER summed")
    for b in sorted(per_binary):
        buckets = per_binary[b]
        print("  binary %s" % b)
        for k in sorted(buckets, key=lambda k: -buckets[k]):
            print("      %-28s %5d" % (k, buckets[k]))

    print("-" * 92)
    print("CERTIFIED rows, which are what stage 4 can emit without the verifier")
    any_cert = False
    for (b, _bucket), rows in sorted(per_unit.items()):
        any_cert = True
        emit = sum(n for _f, _b, _u, n, _nc, _w in rows)
        print("  binary %s -- %d row(s), %d EMITTABLE region(s)"
              % (b, len(rows), emit))
        for fn, bench, unit, n, nc, w in sorted(
                rows, key=lambda t: (-t[3], t[0], str(t[2]))):
            print("      %-40s %-18s %-24s cert=%-3d uncert=%-3d witnessed=%s"
                  % (fn, bench, unit, n, nc, w))
    if not any_cert:
        print("  none. Every row on disk is in some other bucket -- stage 4 has "
              "nothing to emit from, and that is the finding.")
    if emittable_only:
        print("-" * 92)
        print("⛔ --emittable cannot decide on its own which binary is current. "
              "Compare the labels above against the tree's binary before "
              "feeding any of these to put_all.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
