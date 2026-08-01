#!/usr/bin/env python3
"""Ask every resumable journal whether it records the binary that produced it.

WHY IT ASKS THE ARTEFACTS AND NOT THE SCRIPTS. A script can contain the field and
never write it -- this repository has shipped a function with no call site more
than once -- and a resume reads the journal on disk, not the source. So the
question is answered by opening every `*.jsonl` under notes/coverage and counting
records that carry `binary`, which is the same thing a resume would look at.

WHY IT EXISTS AT ALL. "Which journals are safe to resume" was carried in a human
memory, and that memory went stale: it named `option_matrix.py` as the one with
no identity check and no `--redo`, both of which had since been implemented, and
it did not mention `forge_roundtrip.py` at all -- which turned out to be the only
script with NO identity on any of its 74 records. A list that has to be
remembered is a list that is wrong the moment someone fixes something.

READING THE OUTPUT:
  OK        every record names its binary; a resume can refuse a stale one
  PARTIAL   some records predate the field. They are FOREIGN, not assumed
            current -- an absent field must never be read as "same build"
  NO FIELD  either the script has no identity check (a real gap), or the records
            predate one that now exists (harmless, because the check refuses
            them). The two look identical HERE, so confirm against the script
            before calling it a defect.
"""
import json
from pathlib import Path

ROOT = Path("/home/samson/workspace/esbmc/notes/coverage")

for p in sorted(ROOT.rglob("*.jsonl")):
    s = str(p)
    if "prefix-buggy-frontend" in s or "/work/" in s:
        continue
    rel = str(p.relative_to(ROOT))
    lines = [l for l in p.read_text().splitlines() if l.strip()]
    if not lines:
        print(f"{rel:<52} EMPTY")
        continue
    has_bin = 0
    bad = 0
    for l in lines:
        try:
            r = json.loads(l)
        except ValueError:
            bad += 1
            continue
        if "binary" in r:
            has_bin += 1
    if has_bin == len(lines) - bad:
        mark = "OK"
    elif has_bin:
        mark = "PARTIAL"
    else:
        mark = "** NO `binary` FIELD **"
    print(f"{rel:<52} {len(lines):>4} rec  binary={has_bin:>4}  {mark}")
