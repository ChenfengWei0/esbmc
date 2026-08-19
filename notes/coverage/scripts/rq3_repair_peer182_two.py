#!/usr/bin/env python3
"""Insert the two peer182 replay wrappers whose RQ3 claim rows lack a test.

This is source recovery only.  It deliberately does not create certification,
Forge, or PUT evidence.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

RQ1 = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
OUT = Path(
    "/home/samson/workspace/VeriPUT/Results/RQ1_KInduction_NoPUT600/"
    "adoption-bundles/rq3-mechanical-match-20260815"
)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def key(identity: list[str]) -> str:
    return hashlib.sha256("\t".join(identity).encode()).hexdigest()[:20]


CASES = [
    {
        "identity": [
            "peer182/peer_ccsolbmc__ShibaKiyo",
            "sol:@C@ShibaKiyo@F@approve#1659",
            "approve",
            "15",
            "",
        ],
        "source": RQ1 / "peer182/subjects/peer_ccsolbmc__ShibaKiyo/"
        "put/approve/peer182__peer_ccsolbmc__ShibaKiyo__certify-results/test/"
        "ShibaKiyoCovTest_ShibaKiyo_approve_put15.t.sol",
        "flat": Path(
            "/home/samson/workspace/VeriPUT/Results/RQ3/VeriExploit/"
            "No_Cer_Reg/peer182/subjects/peer_ccsolbmc__ShibaKiyo/put/"
            "peer182__peer_ccsolbmc__ShibaKiyo__setTaxFeePercent__pf2147/"
            "peer182__peer_ccsolbmc__ShibaKiyo__certify-results/src/flat.sol"
        ),
        "origin": "rq1-existing-concrete-plus-rq3-flat",
    },
    {
        "identity": [
            "peer182/peer_solar__LotteryFor10",
            "sol:@C@LotteryFor10@F@fallback#143",
            "fallback",
            "13",
            "",
        ],
        "source": RQ1 / "peer182/subjects/peer_solar__LotteryFor10/"
        "concrete-replays/projects/fallback-f0d03ae9f3361231/test/"
        "LotteryFor10CovTest_0_LotteryFor10_fallback_concrete13_fb.t.sol",
        "flat": RQ1 / "peer182/subjects/peer_solar__LotteryFor10/"
        "concrete-replays/projects/fallback-f0d03ae9f3361231/src/flat.sol",
        "origin": "rq1-existing-concrete-replay-plus-rq1-flat",
    },
]


def main() -> None:
    report = []
    for case in CASES:
        identity = case["identity"]
        target = RQ1 / identity[0].split("/", 1)[0] / "subjects" / identity[0].split("/", 1)[1]
        dest = target / "put" / identity[2] / "rq3-mechanical" / key(identity)
        source = case["source"]
        flat = case["flat"]
        if not source.is_file() or not flat.is_file():
            raise SystemExit(f"missing source material: {source} / {flat}")
        copied = dest / "test" / source.name
        copied_flat = dest / "src" / "flat.sol"
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied_flat.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, copied)
        shutil.copy2(flat, copied_flat)
        result = target / "result.json"
        document = json.loads(result.read_text(encoding="utf-8"))
        closures = document.setdefault("rq3_mechanical_closure", [])
        closures[:] = [e for e in closures if e.get("frozen_identity") != identity]
        closures.append({
            "schema": "veriput-rq3-mechanical-physical-source/v1",
            "frozen_identity": identity,
            "rq3_identity": identity,
            "status": "source-backed-reconstructed",
            "match_tier": "claim-wrapper-reconstruction",
            "source": str(copied),
            "source_sha256": sha(copied),
            "flat_source": str(copied_flat),
            "flat_source_sha256": sha(copied_flat),
            "source_origin": case["origin"],
            "forge_run": False,
            "put_credit": False,
            "identity_rewrite": False,
            "certification_created": False,
        })
        result.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        report.append({
            "identity": identity,
            "source": str(copied),
            "source_sha256": sha(copied),
            "flat_source": str(copied_flat),
            "flat_source_sha256": sha(copied_flat),
            "source_origin": case["origin"],
        })
    out = OUT / "peer182-two-repair-v1.json"
    out.write_text(json.dumps({
        "schema": "veriput-rq3-peer182-repair/v1",
        "esbmc_run": False,
        "forge_run": False,
        "put_credit": False,
        "rows": report,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"inserted": len(report), "report": str(out)}))


if __name__ == "__main__":
    main()
