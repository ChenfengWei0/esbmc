#!/usr/bin/env python3
"""Match remaining RQ3 identities by exact source claim, then optionally copy.

This is a final physical audit for artifacts whose subject directory was
renamed or moved between RQ3 shards.  A candidate is accepted only when its
source contains the exact frozen path/function claim and the exact unit/path
tokens in its ancestry.  Similar contract/function names are never accepted.
No ESBMC, Forge, or PUT credit is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def target_subject(root: Path, identity: list[str]) -> Path:
    benchmark, subject = identity[0].split("/", 1)
    return root / benchmark / "subjects" / subject


def find_exact(root: Path, identity: list[str]) -> list[Path]:
    case, claim, unit, enc, _piece = identity
    path_no = claim.rsplit("#", 1)[-1] if "#" in claim else ""
    claim_re = re.compile(r"//\s*claim:\s*" + re.escape(claim) + r":path:")
    unit_token = f"__{unit}__pf{path_no}"
    enc_re = re.compile(rf"__{re.escape(enc)}(?:p[0-9]+)?__certify-results")
    # ripgrep keeps this final audit bounded even though RQ3 contains tens of
    # thousands of emitted files; path filtering below remains deterministic.
    claim = f"// claim: sol:{claim.split('sol:', 1)[-1]}:path:"
    result = subprocess.run(
        ["rg", "-l", "--glob", "*.t.sol", "--fixed-strings", claim, str(root)],
        capture_output=True, text=True, check=False,
    )
    out: list[Path] = []
    for name in result.stdout.splitlines():
        source = Path(name)
        text_path = str(source)
        if unit_token in text_path and enc_re.search(text_path):
            out.append(source)
    return sorted(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binding", type=Path)
    parser.add_argument("--rq3-root", type=Path, required=True)
    parser.add_argument("--rq1-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    binding = json.loads(args.binding.read_text(encoding="utf-8"))
    rows = []
    for record in binding["rows"]:
        identity = list(record["frozen_identity"])
        candidates = find_exact(args.rq3_root, identity)
        hashes = {sha256(path): path for path in candidates}
        row = {
            "identity": identity,
            "candidate_count": len(candidates),
            "distinct_source_hashes": len(hashes),
            "candidates": [str(path) for path in candidates],
            "status": "missing" if not candidates else "ambiguous" if len(hashes) > 1 else "source-only",
        }
        if len(hashes) == 1 and args.apply:
            source = next(iter(hashes.values()))
            target = target_subject(args.rq1_root, identity)
            key = hashlib.sha256("\t".join(identity).encode()).hexdigest()[:20]
            destination = target / "put" / "rq3-mechanical" / "content-claim-variant" / key
            copied = destination / "test" / source.name
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, copied)
            result = target / "result.json"
            document = json.loads(result.read_text(encoding="utf-8"))
            closures = document.setdefault("rq3_mechanical_closure", [])
            closures[:] = [entry for entry in closures
                           if entry.get("frozen_identity") != identity]
            closures.append({
                "schema": "veriput-rq3-mechanical-source-only/v1",
                "frozen_identity": identity,
                "rq3_identity": identity,
                "match_tier": "exact-claim-content-variant",
                "source": str(copied),
                "source_sha256": sha256(copied),
                "test": None,
                "put_json": None,
                "forge_run": False,
                "put_credit": False,
                "source_only": True,
                "identity_rewrite": False,
                "flat_source": None,
            })
            result.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")
            row.update({"status": "applied", "destination": str(copied),
                        "source_sha256": sha256(copied)})
        rows.append(row)
    output = {
        "schema": "rq3-final-content-claim-match/v1",
        "rows": len(rows),
        "source_only": sum(row["status"] in {"source-only", "applied"} for row in rows),
        "applied": sum(row["status"] == "applied" for row in rows),
        "missing": sum(row["status"] == "missing" for row in rows),
        "ambiguous": sum(row["status"] == "ambiguous" for row in rows),
        "entries": rows,
        "policy": {"esbmc_run": False, "forge_run": False,
                   "put_credit": False, "cross_identity_fallback": False},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps({k: output[k] for k in ("rows", "source_only", "applied", "missing", "ambiguous")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
