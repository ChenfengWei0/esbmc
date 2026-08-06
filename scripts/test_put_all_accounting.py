#!/usr/bin/env python3
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from argparse import Namespace


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PUT_ALL = os.path.join(REPO, "notes", "coverage", "scripts", "put_all.py")
COLLECT = os.path.join(REPO, "notes", "coverage", "scripts", "collect.py")

spec = importlib.util.spec_from_file_location("put_all", PUT_ALL)
put_all = importlib.util.module_from_spec(spec)
spec.loader.exec_module(put_all)

collect_spec = importlib.util.spec_from_file_location("collect", COLLECT)
collect = importlib.util.module_from_spec(collect_spec)
collect_spec.loader.exec_module(collect)


def check(name, got, want):
    if got == want:
        print(f"ok - {name}")
        return 0
    print(f"not ok - {name}: got {got!r}, want {want!r}")
    return 1


def main():
    records = [
        {
            "benchmark": "bench",
            "unit": "target",
            "witnessed": 4,
            "certified": {"1": "x in [0, 9]"},
            "not_certified": {
                "2": "refuted with concrete witness",
                "3": "STATICALLY INSEPARABLE: differs only on external-call behavior",
            },
            "not_certified_details": {
                "2": {"enc": 2, "concrete_fallback": True},
                "3": {"enc": 3, "concrete_fallback": False},
            },
        },
        {
            "benchmark": "bench",
            "unit": "other",
            "witnessed": 9,
            "certified": {},
            "not_certified": {"9": "not selected"},
        },
        {
            "benchmark": "bench",
            "unit": "legacy",
            "witnessed": 2,
            "certified": {},
            "not_certified": {
                "5": "STATICALLY INSEPARABLE: differs only on external-call behavior",
            },
            "static_extcall_inseparable": True,
        },
    ]
    with tempfile.NamedTemporaryFile("w", delete=False) as fh:
        path = fh.name
        for record in records:
            fh.write(json.dumps(record) + "\n")
    try:
        bad = 0
        target = put_all.stage2_path_accounting(path, "bench.target")
        bad += check("selected-record-count", target["records"], 1)
        bad += check("selected-witnessed-count", target["witnessed"], 4)
        bad += check("selected-certified-count", target["certified"], 1)
        bad += check("selected-not-certified-count",
                     target["not_certified"], 2)
        bad += check("structured-concrete-fallback",
                     target["concrete_fallback"], 1)
        bad += check("structured-method-unsupported",
                     target["method_unsupported"], 1)
        bad += check("selected-no-verdict", target["no_verdict"], 1)

        legacy = put_all.stage2_path_accounting(path, "bench.legacy")
        bad += check("legacy-extcall-attribution",
                     legacy["method_unsupported"], 1)
        bad += check("legacy-detail-not-unknown",
                     legacy["detail_unknown"], 0)
        bad += check("stage4-bench-table-covers-collector",
                     sorted(put_all.BENCHES),
                     sorted(collect.BENCHES))
        args = Namespace(strong_recipe=True,
                         auto_unwind=0,
                         propose_r2=False,
                         r2_depth=0,
                         r2_term_budget=1,
                         r2_candidate_budget=1,
                         fuzz_r2_prefilter=False,
                         fuzz_runs=1,
                         fuzz_r2_candidate_budget=1)
        bad += check("stage4-strong-recipe-version",
                     put_all.apply_strong_put_recipe(args),
                     put_all.STRONG_RECIPE_VERSION)
        bad += check("stage4-strong-recipe-auto-unwind",
                     args.auto_unwind, 1)
        bad += check("stage4-strong-recipe-r2",
                     (args.propose_r2, args.r2_depth, args.r2_term_budget,
                      args.r2_candidate_budget),
                     (True, 1, 96, 128))
        bad += check("stage4-strong-recipe-fuzz-refute",
                     (args.fuzz_r2_prefilter, args.fuzz_runs,
                      args.fuzz_r2_candidate_budget),
                     (True, 256, 128))
        plain = Namespace(strong_recipe=False, auto_unwind=0)
        bad += check("stage4-plain-recipe-unchanged",
                     (put_all.apply_strong_put_recipe(plain), plain.auto_unwind),
                     (None, 0))
        cp = subprocess.run([
            sys.executable,
            PUT_ALL,
            "--out-root",
            "/home/samson/workspace/VeriPUT/Results/put-stage4",
            "--cert",
            path,
        ],
                            capture_output=True,
                            text=True)
        bad += check("stage4-refuses-protected-out-root",
                     (cp.returncode != 0
                      and "--out-root must not be under" in cp.stderr),
                     True)
        return bad
    finally:
        os.unlink(path)


if __name__ == "__main__":
    raise SystemExit(main())
