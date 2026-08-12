#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import unit_schedule  # noqa: E402
import veriput_recipe  # noqa: E402
import veriput_subjects  # noqa: E402

DEFAULT_RECIPE_DIR = veriput_recipe.STRONG_RECIPE_VERSION.replace("/", "_")


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def argv_value(argv, flag):
    try:
        idx = argv.index(flag)
    except ValueError:
        return None
    if idx + 1 >= len(argv):
        return None
    return argv[idx + 1]


def subject_record(unit=""):
    return {
        "schema": "veriput-subject/v1",
        "benchmark": "stress243",
        "subject_id": "repo__C",
        "benchmark_key": "stress243__repo__C",
        "root": "/tmp/repo__C",
        "flat_sol": "/tmp/repo__C/flat.sol",
        "solast": "/tmp/cache/stress243/stress243__repo__C/flat.sol.solast",
        "solast_source": "cache",
        "contract": "C",
        "unit": unit,
        "solc_bin": "/bin/false",
        "solc_bin_source": "explicit",
        "solc": "0.8.29",
        "inferred_solc_bin": None,
        "solc_extra": [],
        "meta_status": "ok",
    }


def manifest():
    return {
        "schema":
        "veriput-unit-manifest/v1",
        "benchmark":
        None,
        "target_manifest":
        "/tmp/targets.json",
        "generate_ast":
        False,
        "ast_cache_root":
        "/tmp/cache",
        "summary": {
            "subjects": 2,
            "ok": 1,
            "missing_ast": 1,
            "error": 0,
            "units": 2,
        },
        "subjects": [
            {
                "subject": subject_record(),
                "status": "ok",
                "target": {
                    "benchmark": "stress243",
                    "subject_id": "repo__C",
                    "contract": "C",
                    "units_hint": ["setX", "changedMissing"],
                },
                "unit_hints": {
                    "hinted_units": ["setX"],
                    "missing_unit_hints": ["changedMissing"],
                    "pending_unit_hints": [],
                },
                "units": {
                    "schema": "veriput-subject-units/v1",
                    "contract": "C",
                    "units": ["getX", "setX"],
                    "skipped": [],
                },
            },
            {
                "subject": subject_record(),
                "status": "missing-ast",
                "reason": "flat.sol.solast does not exist",
            },
        ],
    }


def test_schedule_prioritizes_hinted_units_and_preserves_argv():
    doc = unit_schedule.build_schedule(manifest())
    bad = 0
    bad += check(doc["schema"] == "veriput-unit-schedule/v1",
                 f"schedule schema is stable: {doc['schema']}")
    bad += check(doc["summary"]["jobs"] == 2, f"two unit jobs are emitted: {doc['summary']}")
    bad += check(doc["summary"]["skipped_by_status"] == {"missing-ast": 1},
                 f"non-ok rows are skipped explicitly: {doc['summary']}")
    jobs = doc["jobs"]
    hinted, enumerated = jobs[0], jobs[1]
    bad += check(hinted["unit"] == "setX" and hinted["priority"] == 0,
                 f"target-hinted unit is first: {jobs}")
    bad += check(enumerated["unit"] == "getX" and enumerated["priority_reason"] == "enumerated",
                 f"non-hinted unit remains scheduled: {jobs}")
    bad += check(hinted["subject"]["unit"] == "setX" and enumerated["subject"]["unit"] == "getX",
                 f"each job carries a concrete subject unit: {jobs}")
    argv = hinted["certify_argv"]
    bad += check("--subject-dir" in argv and "/tmp/repo__C" in argv,
                 f"certifier argv resolves the prepared subject: {argv}")
    bad += check("--subject-benchmark" in argv and "stress243" in argv,
                 f"certifier argv labels the benchmark: {argv}")
    bad += check("--unit" in argv and "setX" in argv, f"certifier argv selects the unit: {argv}")
    bad += check("--ast-cache-root" in argv and "/tmp/cache" in argv,
                 f"certifier argv preserves AST cache root: {argv}")
    bad += check("--recipe-version" in argv
                 and veriput_recipe.STRONG_RECIPE_VERSION in argv,
                 f"certifier argv uses the shared strong recipe: {argv}")
    bad += check("--skip-bracket" in argv and "--probe-ladder" in argv
                 and "--pin-agreed-establishable-env" in argv
                 and "--pin-agreed-state" in argv and "--slot-coords" in argv,
                 f"strong region controls are scheduled for benchmarks: {argv}")
    bad += check("--esbmc-arg=--unwindsetname" in argv
                 and "--esbmc-arg=_ESBMC_alloc_nested_2d:0:16,nondet_string:0:33"
                 in argv,
                 f"library-loop unwindsetname is scheduled for benchmarks: {argv}")
    bad += check("--no-auto-pin-value" not in argv,
                 f"main benchmark recipe keeps the nonpayable body slice cheap: {argv}")
    bad += check("--env-coord" not in argv,
                 f"one-parameter unit does not get the zero-interface sender arm: {argv}")
    bad += check(argv_value(argv, "--timeout") == "60"
                 and argv_value(argv, "--run-timeout") == "60"
                 and argv_value(argv, "--memlimit-gib") == "8",
                 f"schedule embeds the first-attempt certify budget: {argv}")
    default_workdir = f"/tmp/certify_all/{DEFAULT_RECIPE_DIR}/t60_r60_m8"
    bad += check(argv_value(argv, "--workdir") == default_workdir,
                 f"schedule embeds a recipe/budget-specific scratch root: {argv}")
    bad += check(hinted["certification_budget"] == {
        "timeout_s": 60,
        "run_timeout_s": 60,
        "memlimit_gib": 8,
        "workdir": default_workdir,
    }, f"job records the embedded certify budget: {hinted['certification_budget']}")
    bad += check("--dry-run" not in argv and "--dry-run" in hinted["dry_run_argv"],
                 f"normal and dry-run argv are separate: {hinted}")
    bad += check(argv_value(hinted["dry_run_argv"], "--timeout") == "60",
                 f"dry-run argv carries the same budget: {hinted['dry_run_argv']}")
    bad += check(doc["recipe_version"] == veriput_recipe.STRONG_RECIPE_VERSION,
                 f"schedule records the recipe version: {doc.get('recipe_version')}")
    bad += check(doc["certification_budget"] == {
        "timeout_s": 60,
        "run_timeout_s": 60,
        "memlimit_gib": 8,
        "workdir": default_workdir,
    }, f"schedule records the default certification budget: {doc['certification_budget']}")
    return bad


def test_schedule_prioritizes_cheap_getters_before_complex_state_units():
    data = manifest()
    row = data["subjects"][0]
    row["unit_hints"] = {
        "hinted_units": [],
        "missing_unit_hints": [],
        "pending_unit_hints": [],
    }
    row["target"]["units_hint"] = []
    row["units"]["units"] = ["name", "setX", "quote", "poke"]
    row["units"]["unit_info"] = [
        {
            "name": "name",
            "state_mutability": "view",
            "parameter_count": 0,
            "return_count": 0,
        },
        {
            "name": "setX",
            "state_mutability": "nonpayable",
            "parameter_count": 1,
            "return_count": 0,
        },
        {
            "name": "quote",
            "state_mutability": "pure",
            "parameter_count": 1,
            "return_count": 1,
        },
        {
            "name": "poke",
            "state_mutability": "nonpayable",
            "parameter_count": 0,
            "return_count": 0,
        },
    ]
    doc = unit_schedule.build_schedule(data)
    got = [(job["unit"], job["priority"], job["priority_reason"])
           for job in doc["jobs"]]
    bad = 0
    bad += check(got == [("name", 1, "cheap-pure/view-getter"),
                         ("quote", 1, "cheap-pure/view-getter"),
                         ("setX", 1, "state-changing"),
                         ("poke", 2, "zero-interface-state-changing")],
                 f"cheap getter-like units are sampled before state-changing rows: {got}")
    bad += check(doc["jobs"][0]["unit_info"]["parameter_count"] == 0,
                 f"job keeps unit metadata for later audit: {doc['jobs'][0]}")
    poke = next(job for job in doc["jobs"] if job["unit"] == "poke")
    poke_argv = poke["certify_argv"]
    bad += check(poke["region_strategy"] == {
        "zero_interface_sender_arm": True,
        "env_coords": ["msg.sender"],
        "reason": "state-changing unit has no ABI parameter coordinate",
    }, f"zero-interface state unit records the sender-coordinate arm: {poke}")
    bad += check(argv_value(poke_argv, "--env-coord") == "msg.sender",
                 f"zero-interface state unit promotes sender to a PUT coordinate: {poke_argv}")
    name = next(job for job in doc["jobs"] if job["unit"] == "name")
    bad += check(not name["region_strategy"]["zero_interface_sender_arm"]
                 and "--env-coord" not in name["certify_argv"],
                 f"zero-arg view unit does not spend a sender-coordinate arm: {name}")
    return bad


def test_schedule_deprioritizes_unhinted_initializers():
    data = manifest()
    row = data["subjects"][0]
    row["target"]["units_hint"] = []
    row["unit_hints"] = {
        "hinted_units": [],
        "missing_unit_hints": [],
        "pending_unit_hints": [],
    }
    row["units"]["units"] = ["init", "transfer", "setUp", "balanceOf"]
    row["units"]["unit_info"] = [
        {
            "name": "init",
            "state_mutability": "nonpayable",
            "parameter_count": 4,
            "return_count": 0,
        },
        {
            "name": "transfer",
            "state_mutability": "nonpayable",
            "parameter_count": 2,
            "return_count": 1,
        },
        {
            "name": "setUp",
            "state_mutability": "nonpayable",
            "parameter_count": 0,
            "return_count": 0,
        },
        {
            "name": "balanceOf",
            "state_mutability": "view",
            "parameter_count": 1,
            "return_count": 1,
        },
    ]
    doc = unit_schedule.build_schedule(data)
    got = [(job["unit"], job["priority"], job["priority_reason"])
           for job in doc["jobs"]]
    bad = 0
    bad += check(got == [("balanceOf", 1, "cheap-pure/view-getter"),
                         ("transfer", 2, "state-changing"),
                         ("setUp", 3, "initializer-like"),
                         ("init", 3, "initializer-like")],
                 f"unhinted initializer-like units do not monopolize first attempts: {got}")

    row["target"]["units_hint"] = ["init"]
    row["unit_hints"] = {
        "hinted_units": ["init"],
        "missing_unit_hints": [],
        "pending_unit_hints": [],
    }
    hinted_doc = unit_schedule.build_schedule(data)
    hinted = [(job["unit"], job["priority"], job["priority_reason"])
              for job in hinted_doc["jobs"][:2]]
    bad += check(hinted == [("init", 0, "target-hint"),
                            ("balanceOf", 1, "cheap-pure/view-getter")],
                 f"explicit target hints still override initializer deprioritization: {hinted}")
    return bad


def test_schedule_orders_cheaper_units_inside_priority_bucket():
    data = manifest()
    row = data["subjects"][0]
    row["target"]["units_hint"] = [
        "buy",
        "execute",
        "flashFee",
        "setFeeRate",
    ]
    row["unit_hints"] = {
        "hinted_units": ["buy", "execute", "flashFee", "setFeeRate"],
        "missing_unit_hints": [],
        "pending_unit_hints": [],
    }
    row["units"]["units"] = [
        "buy",
        "execute",
        "flashFee",
        "setFeeRate",
        "deposit",
        "transferOwnership",
    ]
    row["units"]["unit_info"] = [
        {
            "name": "buy",
            "state_mutability": "payable",
            "parameter_count": 3,
            "return_count": 3,
        },
        {
            "name": "execute",
            "state_mutability": "payable",
            "parameter_count": 2,
            "return_count": 1,
        },
        {
            "name": "flashFee",
            "state_mutability": "view",
            "parameter_count": 2,
            "return_count": 1,
        },
        {
            "name": "setFeeRate",
            "state_mutability": "nonpayable",
            "parameter_count": 1,
            "return_count": 0,
        },
        {
            "name": "deposit",
            "state_mutability": "payable",
            "parameter_count": 2,
            "return_count": 0,
        },
        {
            "name": "transferOwnership",
            "state_mutability": "nonpayable",
            "parameter_count": 1,
            "return_count": 0,
        },
    ]
    doc = unit_schedule.build_schedule(data)
    got = [(job["unit"], job["priority"], job["priority_reason"],
            job["schedule_rank"]["cheap_first"]) for job in doc["jobs"]]
    bad = 0
    bad += check([unit for unit, _prio, _reason, _rank in got] == [
        "flashFee",
        "setFeeRate",
        "transferOwnership",
        "execute",
        "buy",
        "deposit",
    ], f"cheap units are tried before expensive target hints: {got}")
    bad += check(all(prio == 0 and reason == "target-hint"
                     for _unit, prio, reason, _rank in got[:2]),
                 f"cheap target hints stay in the first bucket: {got}")
    bad += check(all(reason == "expensive-target-hint"
                     for unit, _prio, reason, _rank in got[3:5]
                     if unit in ("execute", "buy")),
                 f"expensive target hints stay marked for audit: {got}")
    bad += check(got[0][3] < got[3][3],
                 f"schedule rank records the cheap-first decision: {got}")
    return bad


def test_schedule_prioritizes_ownership_transfer_before_business_mutators():
    data = manifest()
    row = data["subjects"][0]
    row["target"]["units_hint"] = []
    row["unit_hints"] = {
        "hinted_units": [],
        "missing_unit_hints": [],
        "pending_unit_hints": [],
    }
    row["units"]["units"] = [
        "includeAccount",
        "increaseAllowance",
        "transferOwnership",
    ]
    row["units"]["unit_info"] = [
        {
            "name": "includeAccount",
            "state_mutability": "nonpayable",
            "parameter_count": 1,
            "return_count": 0,
        },
        {
            "name": "increaseAllowance",
            "state_mutability": "nonpayable",
            "parameter_count": 2,
            "return_count": 1,
        },
        {
            "name": "transferOwnership",
            "state_mutability": "nonpayable",
            "parameter_count": 1,
            "return_count": 0,
        },
    ]
    doc = unit_schedule.build_schedule(data)
    got = [(job["unit"], job["schedule_rank"]["cheap_first"][0])
           for job in doc["jobs"]]
    bad = 0
    bad += check(got[:3] == [
        ("transferOwnership", 8),
        ("includeAccount", 30),
        ("increaseAllowance", 30),
    ], f"ownership transfer is tried before broad business mutators: {got}")
    return bad


def test_schedule_deprioritizes_unhinted_admin_units():
    data = manifest()
    row = data["subjects"][0]
    row["target"]["units_hint"] = []
    row["unit_hints"] = {
        "hinted_units": [],
        "missing_unit_hints": [],
        "pending_unit_hints": [],
    }
    row["units"]["units"] = [
        "addAgent",
        "mint",
        "freezePartialTokens",
        "pause",
        "unPause",
        "unpause",
        "setAddressFrozen",
        "setName",
        "approve",
        "setFeeRate",
    ]
    row["units"]["unit_info"] = [
        {
            "name": "addAgent",
            "state_mutability": "nonpayable",
            "parameter_count": 1,
            "return_count": 0,
        },
        {
            "name": "mint",
            "state_mutability": "nonpayable",
            "parameter_count": 2,
            "return_count": 0,
        },
        {
            "name": "freezePartialTokens",
            "state_mutability": "nonpayable",
            "parameter_count": 2,
            "return_count": 0,
        },
        {
            "name": "pause",
            "state_mutability": "nonpayable",
            "parameter_count": 0,
            "return_count": 0,
        },
        {
            "name": "unPause",
            "state_mutability": "nonpayable",
            "parameter_count": 0,
            "return_count": 0,
        },
        {
            "name": "unpause",
            "state_mutability": "nonpayable",
            "parameter_count": 0,
            "return_count": 0,
        },
        {
            "name": "setAddressFrozen",
            "state_mutability": "nonpayable",
            "parameter_count": 2,
            "return_count": 0,
        },
        {
            "name": "setName",
            "state_mutability": "nonpayable",
            "parameter_count": 1,
            "return_count": 0,
        },
        {
            "name": "approve",
            "state_mutability": "nonpayable",
            "parameter_count": 2,
            "return_count": 1,
        },
        {
            "name": "setFeeRate",
            "state_mutability": "nonpayable",
            "parameter_count": 1,
            "return_count": 0,
        },
    ]
    doc = unit_schedule.build_schedule(data)
    got = [(job["unit"], job["schedule_rank"]["cheap_first"])
           for job in doc["jobs"]]
    bad = 0
    bad += check([unit for unit, _rank in got] == [
        "setFeeRate",
        "approve",
        "pause",
        "unPause",
        "unpause",
        "setName",
        "setAddressFrozen",
        "addAgent",
        "mint",
        "freezePartialTokens",
    ], f"unhinted admin units no longer block business methods: {got}")
    pause = next(job for job in doc["jobs"] if job["unit"] == "pause")
    bad += check(pause["region_strategy"]["zero_interface_sender_arm"]
                 and argv_value(pause["certify_argv"], "--env-coord") == "msg.sender",
                 f"admin zero-interface units still record sender arm when reached: {pause}")

    row["target"]["units_hint"] = ["pause"]
    row["unit_hints"]["hinted_units"] = ["pause"]
    hinted_doc = unit_schedule.build_schedule(data)
    hinted = [(job["unit"], job["priority"], job["priority_reason"])
              for job in hinted_doc["jobs"][:2]]
    bad += check(hinted == [("pause", 0, "target-hint"),
                            ("setFeeRate", 1, "state-changing")],
                 f"explicit target hints still override admin deprioritization: {hinted}")
    return bad


def test_schedule_deprioritizes_recursive_helper_obstacles():
    recursive_ast = {
        "nodes": [
            {
                "nodeType": "ContractDefinition",
                "id": 1,
                "name": "SafeMath",
                "nodes": [
                    {
                        "nodeType": "FunctionDefinition",
                        "id": 10,
                        "name": "sub",
                        "parameters": {
                            "parameters": [{}, {}],
                        },
                        "body": {
                            "nodeType": "Block",
                            "statements": [{
                                "nodeType": "Return",
                                "expression": {
                                    "nodeType": "FunctionCall",
                                    "expression": {
                                        "nodeType": "Identifier",
                                        "name": "sub",
                                        "referencedDeclaration": 10,
                                    },
                                    "arguments": [{}, {}],
                                },
                            }],
                        },
                    },
                ],
            },
            {
                "nodeType": "ContractDefinition",
                "id": 2,
                "name": "C",
                "linearizedBaseContracts": [2],
                "nodes": [
                    {
                        "nodeType": "FunctionDefinition",
                        "id": 20,
                        "name": "transfer",
                        "parameters": {
                            "parameters": [{}, {}],
                        },
                        "body": {
                            "nodeType": "Block",
                            "statements": [{
                                "nodeType": "ExpressionStatement",
                                "expression": {
                                    "nodeType": "FunctionCall",
                                    "expression": {
                                        "nodeType": "Identifier",
                                        "name": "sub",
                                        "referencedDeclaration": 10,
                                    },
                                    "arguments": [{}, {}],
                                },
                            }],
                        },
                    },
                    {
                        "nodeType": "FunctionDefinition",
                        "id": 30,
                        "name": "approve",
                        "parameters": {
                            "parameters": [{}, {}],
                        },
                        "body": {
                            "nodeType": "Block",
                            "statements": [],
                        },
                    },
                ],
            },
        ],
    }
    with tempfile.TemporaryDirectory() as td:
        ast = Path(td) / "flat.sol.solast"
        ast.write_text("JSON AST (compact format):\n" + json.dumps(recursive_ast))
        data = manifest()
        row = data["subjects"][0]
        row["ast"] = {"path": str(ast), "status": "generated"}
        row["subject"]["solast"] = str(ast)
        row["units"]["units"] = ["transfer", "approve"]
        row["units"]["unit_info"] = [
            {
                "name": "transfer",
                "state_mutability": "nonpayable",
                "parameter_count": 2,
                "return_count": 1,
            },
            {
                "name": "approve",
                "state_mutability": "nonpayable",
                "parameter_count": 2,
                "return_count": 1,
            },
        ]
        doc = unit_schedule.build_schedule(data)
    got = [(job["unit"], job["priority"], job["priority_reason"])
           for job in doc["jobs"]]
    transfer = next(job for job in doc["jobs"] if job["unit"] == "transfer")
    bad = 0
    bad += check(got == [("approve", 2, "state-changing"),
                         ("transfer", 4, "static-obstacle")],
                 f"recursive-helper units stay scheduled but are deprioritized: {got}")
    bad += check(transfer["static_obstacles"][0]["tag"] == "recursive-helper-preflight",
                 f"job records the static obstacle for audit: {transfer}")
    bad += check(transfer["static_obstacles"][0]["helpers"] == ["SafeMath.sub/2"],
                 f"recursive helper labels are carried into the schedule: {transfer}")
    bad += check(doc["summary"]["static_obstacle_jobs"] == 1
                 and doc["summary"]["static_obstacles_by_tag"] == {
                     "recursive-helper-preflight": 1,
                 }, f"summary counts static obstacles: {doc['summary']}")
    return bad


def test_schedule_cli_reads_stdin_and_applies_limit():
    with tempfile.TemporaryDirectory() as td:
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts" / "unit_schedule.py"),
            "-",
            "--limit",
            "1",
            "--cert-out",
            str(Path(td) / "results.jsonl"),
            "--timeout",
            "120",
            "--run-timeout",
            "30",
            "--memlimit-gib",
            "10",
        ],
                            input=json.dumps(manifest()),
                            capture_output=True,
                            text=True)
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    doc = json.loads(cp.stdout)
    job = doc["jobs"][0]
    bad = 0
    bad += check(doc["summary"]["jobs"] == 1, f"limit keeps one scheduled job: {doc['summary']}")
    bad += check(doc["summary"]["jobs_before_shard"] == 2,
                 f"pre-limit denominator is retained: {doc['summary']}")
    bad += check(job["unit"] == "setX" and job["priority"] == 0,
                 f"limit is applied after priority sorting: {job}")
    out_idx = job["certify_argv"].index("--out") if "--out" in job["certify_argv"] else -1
    bad += check(
        out_idx >= 0 and job["certify_argv"][out_idx + 1].endswith("results.jsonl"),
        f"cert output path is threaded into argv: {job['certify_argv']}")
    bad += check(argv_value(job["certify_argv"], "--timeout") == "120"
                 and argv_value(job["certify_argv"], "--run-timeout") == "30"
                 and argv_value(job["certify_argv"], "--memlimit-gib") == "10",
                 f"CLI budget flags are threaded into certify argv: {job['certify_argv']}")
    bad += check(argv_value(job["certify_argv"], "--workdir")
                 == str(Path(td) / "certify-work-t120_r30_m10"),
                 f"CLI cert-out derives an isolated workdir: {job['certify_argv']}")
    return bad


def test_schedule_deduplicates_prepared_subject_units():
    data = manifest()
    data["subjects"].append(json.loads(json.dumps(data["subjects"][0])))
    doc = unit_schedule.build_schedule(data)
    ids = [job["job_id"] for job in doc["jobs"]]
    bad = 0
    bad += check(ids == ["stress243__repo__C__setX", "stress243__repo__C__getX"],
                 f"duplicate subject units are scheduled once: {ids}")
    bad += check(doc["summary"]["duplicate_jobs"] == 2,
                 f"duplicate unit jobs are reported: {doc['summary']}")
    bad += check(doc["duplicate_jobs"][0]["unit"] in ("getX", "setX"),
                 f"duplicate sample names the unit: {doc['duplicate_jobs']}")
    return bad


def test_schedule_passes_unique_path_function_for_overloads():
    data = manifest()
    row = data["subjects"][0]
    row["subject"]["contract"] = "Co"
    row["subject"]["unit"] = ""
    row["units"]["contract"] = "Co"
    row["units"]["units"] = ["f"]
    row["units"]["unit_info"] = [{
        "name": "f",
        "state_mutability": "nonpayable",
        "parameter_count": 1,
        "return_count": 0,
        "visibility": "public",
        "path_function": "sol:@C@Co@F@f#31",
    }, {
        "name": "f",
        "state_mutability": "nonpayable",
        "parameter_count": 1,
        "return_count": 0,
        "visibility": "public",
        "path_function": "sol:@C@Co@F@f#59",
    }]
    row["target"]["contract"] = "Co"
    row["target"]["units_hint"] = ["f"]
    row["unit_hints"] = {
        "hinted_units": ["f"],
        "missing_unit_hints": [],
        "pending_unit_hints": [],
    }
    ast = {
        "nodes": [{
            "nodeType": "ContractDefinition",
            "id": 60,
            "name": "Co",
            "linearizedBaseContracts": [60],
            "nodes": [
                {
                    "nodeType": "FunctionDefinition",
                    "id": 31,
                    "name": "f",
                    "implemented": True,
                    "visibility": "public",
                    "parameters": {
                        "parameters": [{}],
                    },
                },
                {
                    "nodeType": "FunctionDefinition",
                    "id": 59,
                    "name": "f",
                    "implemented": True,
                    "visibility": "public",
                    "parameters": {
                        "parameters": [{}, {}],
                    },
                },
            ],
        }],
    }
    with tempfile.TemporaryDirectory() as td:
        solast = Path(td) / "flat.sol.solast"
        solast.write_text("JSON AST\n" + json.dumps(ast))
        row["subject"]["solast"] = str(solast)
        doc = unit_schedule.build_schedule(data)

    job = doc["jobs"][0]
    path_function = "sol:@C@Co@F@f#31"
    bad = 0
    bad += check(job["path_function"] == path_function,
                 f"overload arity resolves a unique path function: {job}")
    bad += check(argv_value(job["certify_argv"], "--path-function") == path_function,
                 f"certifier argv pins the selected overload: {job['certify_argv']}")
    bad += check(argv_value(job["dry_run_argv"], "--path-function") == path_function,
                 f"dry-run argv pins the selected overload: {job['dry_run_argv']}")
    return bad


def test_schedule_rehomes_inherited_unit_info_path_function_to_target_owner():
    data = manifest()
    row = data["subjects"][0]
    row["subject"]["contract"] = "Derived"
    row["subject"]["unit"] = ""
    row["units"]["contract"] = "Derived"
    row["units"]["units"] = ["balanceOf"]
    row["units"]["unit_info"] = [{
        "name": "balanceOf",
        "contract": "BaseToken",
        "state_mutability": "view",
        "parameter_count": 1,
        "parameter_types": ["address"],
        "return_count": 1,
        "visibility": "public",
        "path_function": "sol:@C@BaseToken@F@balanceOf#77",
    }]
    row["target"]["contract"] = "Derived"
    row["target"]["units_hint"] = []
    row["unit_hints"] = {
        "hinted_units": [],
        "missing_unit_hints": [],
        "pending_unit_hints": [],
    }
    doc = unit_schedule.build_schedule(data)
    job = doc["jobs"][0]
    path_function = "sol:@C@Derived@F@balanceOf#77"
    bad = 0
    bad += check(job["path_function"] == path_function,
                 f"inherited unit info path_function uses target owner: {job}")
    bad += check(argv_value(job["certify_argv"], "--path-function") == path_function,
                 f"certifier argv pins target-owned inherited path: {job['certify_argv']}")
    bad += check(job["priority_reason"] == "cheap-pure/view-getter",
                 f"inherited getter remains cheap-first: {job}")
    return bad


def test_inherited_only_contract_flows_from_ast_manifest_to_schedule():
    ast = {
        "nodeType":
        "SourceUnit",
        "nodes": [
            {
                "nodeType":
                "ContractDefinition",
                "id":
                1,
                "name":
                "Base",
                "contractKind":
                "contract",
                "linearizedBaseContracts": [1],
                "nodes": [{
                    "nodeType": "FunctionDefinition",
                    "id": 11,
                    "kind": "function",
                    "name": "f",
                    "implemented": True,
                    "visibility": "public",
                    "stateMutability": "pure",
                    "parameters": {
                        "parameters": []
                    },
                    "returnParameters": {
                        "parameters": [{
                            "nodeType": "VariableDeclaration",
                            "typeDescriptions": {
                                "typeString": "uint256"
                            },
                        }]
                    },
                }],
            },
            {
                "nodeType": "ContractDefinition",
                "id": 2,
                "name": "Derived",
                "contractKind": "contract",
                "linearizedBaseContracts": [2, 1],
                "nodes": [],
            },
        ],
    }
    with tempfile.TemporaryDirectory() as td:
        subject_dir = Path(td) / "repo__Derived"
        subject_dir.mkdir()
        (subject_dir / "flat.sol").write_text(
            "contract Base { function f() public pure returns (uint256) { return 1; } }\n"
            "contract Derived is Base {}\n")
        (subject_dir / "flat.sol.solast").write_text(json.dumps(ast) + "\n")
        (subject_dir / "meta.json").write_text(
            json.dumps({
                "subject_id": "repo__Derived",
                "benchmark": "stress243",
                "contract": "Derived",
                "status": "ok",
                "solc_bin": "/bin/false",
            }) + "\n")
        subject = veriput_subjects.resolve_subject("repo__Derived",
                                                   root=td,
                                                   benchmark="stress243",
                                                   require_unit=False)
        unit_doc = veriput_subjects.unit_manifest("stress243", [subject])
        schedule = unit_schedule.build_schedule(unit_doc)

    manifest_row = unit_doc["subjects"][0]
    manifest_info = manifest_row["units"]["unit_info"][0]
    job = schedule["jobs"][0]
    target_path = "sol:@C@Derived@F@f#11"
    bad = 0
    bad += check(manifest_row["status"] == "ok" and manifest_row["units"]["units"] == ["f"],
                 f"inherited-only AST produces a manifest unit: {manifest_row}")
    bad += check(
        manifest_info["contract"] == "Base"
        and manifest_info["path_function"] == "sol:@C@Base@F@f#11",
        f"manifest preserves the inherited declaration owner: {manifest_info}")
    bad += check(
        schedule["summary"]["jobs"] == 1 and schedule["summary"]["no_unit_rows"] == 0,
        f"inherited-only target is scheduled rather than marked no-units: "
        f"{schedule['summary']}")
    bad += check(
        job["contract"] == "Derived" and job["unit"] == "f"
        and job["unit_info"]["inherited_from_contract"] == "Base"
        and job["path_function"] == target_path,
        f"schedule rehomes the inherited unit onto the target: {job}")
    bad += check(
        argv_value(job["certify_argv"], "--path-function") == target_path,
        f"certifier receives the target-owned inherited path: {job['certify_argv']}")
    return bad


def test_schedule_splits_same_arity_overloads_by_path_function():
    data = manifest()
    row = data["subjects"][0]
    row["subject"]["contract"] = "Co"
    row["subject"]["unit"] = ""
    row["units"]["contract"] = "Co"
    row["units"]["units"] = ["f"]
    row["units"]["unit_info"] = [{
        "name": "f",
        "state_mutability": "nonpayable",
        "parameter_count": 1,
        "return_count": 0,
        "visibility": "public",
    }]
    row["target"]["contract"] = "Co"
    row["target"]["units_hint"] = ["f"]
    ast = {
        "nodes": [{
            "nodeType": "ContractDefinition",
            "id": 60,
            "name": "Co",
            "linearizedBaseContracts": [60],
            "nodes": [
                {
                    "nodeType": "FunctionDefinition",
                    "id": 31,
                    "name": "f",
                    "implemented": True,
                    "visibility": "public",
                    "stateMutability": "view",
                    "parameters": {
                        "parameters": [{"typeDescriptions": {
                            "typeString": "uint256"}}],
                    },
                    "returnParameters": {
                        "parameters": [{"typeDescriptions": {
                            "typeString": "bool"}}],
                    },
                    "body": {
                        "statements": [{
                            "nodeType": "Return",
                            "expression": {"nodeType": "FunctionCall"},
                        }],
                    },
                },
                {
                    "nodeType": "FunctionDefinition",
                    "id": 59,
                    "name": "f",
                    "implemented": True,
                    "visibility": "public",
                    "stateMutability": "payable",
                    "parameters": {
                        "parameters": [{"typeDescriptions": {
                            "typeString": "address"}}],
                    },
                    "body": {
                        "statements": [{"nodeType": "ExpressionStatement"}],
                    },
                },
            ],
        }],
    }
    with tempfile.TemporaryDirectory() as td:
        solast = Path(td) / "flat.sol.solast"
        solast.write_text(json.dumps(ast))
        row["subject"]["solast"] = str(solast)
        doc = unit_schedule.build_schedule(data)

    pfs = sorted(job["path_function"] for job in doc["jobs"])
    ids = sorted(job["job_id"] for job in doc["jobs"])
    bad = 0
    bad += check(pfs == ["sol:@C@Co@F@f#31", "sol:@C@Co@F@f#59"],
                 f"same-arity overloads become separate jobs: {pfs}")
    bad += check(ids == ["stress243__repo__C__f__pf31",
                         "stress243__repo__C__f__pf59"],
                 f"job ids include stable path-function suffixes: {ids}")
    bad += check(all(argv_value(job["certify_argv"], "--path-function")
                     == job["path_function"] for job in doc["jobs"]),
                 f"each certifier argv pins its overload: {doc['jobs']}")
    infos = {job["path_function"]: job["unit_info"] for job in doc["jobs"]}
    bad += check(infos["sol:@C@Co@F@f#31"]["state_mutability"] == "view"
                 and infos["sol:@C@Co@F@f#31"]["return_types"] == ["bool"]
                 and infos["sol:@C@Co@F@f#59"]["state_mutability"] == "payable"
                 and infos["sol:@C@Co@F@f#59"]["return_count"] == 0,
                 f"same-arity overload metadata comes from each declaration: {infos}")
    bad += check(doc["jobs"][0]["path_function"] == "sol:@C@Co@F@f#31"
                 and doc["jobs"][0]["unit_info"]["delegating_wrapper"] is True,
                 f"priority buckets remain authoritative over wrapper cost: {doc['jobs']}")
    return bad


def test_schedule_skips_unpinned_overloads_without_path_function():
    data = manifest()
    row = data["subjects"][0]
    row["subject"]["solast"] = ""
    row["subject"]["unit"] = ""
    row["units"]["units"] = ["f", "f"]
    row["units"]["unit_info"] = [
        {
            "name": "f",
            "state_mutability": "nonpayable",
            "parameter_count": 1,
            "return_count": 0,
            "visibility": "public",
        },
        {
            "name": "f",
            "state_mutability": "nonpayable",
            "parameter_count": 2,
            "return_count": 0,
            "visibility": "public",
        },
    ]
    row["target"]["units_hint"] = ["f"]
    row["unit_hints"] = {
        "hinted_units": ["f"],
        "missing_unit_hints": [],
        "pending_unit_hints": [],
    }
    doc = unit_schedule.build_schedule(data)
    bad = 0
    bad += check(doc["jobs"] == [],
                 f"ambiguous overloads without path_function are not scheduled: {doc['jobs']}")
    bad += check(doc["summary"]["skipped_units_by_reason"] == {
        "overloaded unit needs unique --path-function": 2,
    }, f"ambiguous overload skip is audited: {doc['summary']}")
    return bad


def test_schedule_includes_fallback_and_receive_units():
    data = manifest()
    row = data["subjects"][0]
    row["units"]["units"] = ["fallback", "receive", "setX"]
    row["units"]["unit_info"] = [
        {
            "name": "fallback",
            "state_mutability": "nonpayable",
            "parameter_count": 0,
            "return_count": 0,
            "visibility": "external",
            "implemented": True,
        },
        {
            "name": "receive",
            "state_mutability": "payable",
            "parameter_count": 0,
            "return_count": 0,
            "visibility": "external",
            "implemented": True,
        },
        {
            "name": "setX",
            "state_mutability": "nonpayable",
            "parameter_count": 1,
            "return_count": 0,
            "visibility": "public",
            "implemented": True,
        },
    ]
    row["target"]["units_hint"] = ["fallback", "receive", "setX"]
    row["unit_hints"] = {
        "hinted_units": ["fallback", "receive", "setX"],
        "missing_unit_hints": [],
        "pending_unit_hints": [],
    }
    doc = unit_schedule.build_schedule(data)
    units = [job["unit"] for job in doc["jobs"]]
    skipped = doc["skipped_units"]
    bad = 0
    bad += check(units == ["fallback", "receive", "setX"],
                 f"fallback/receive are scheduled as external entries: {units}")
    bad += check(skipped == [], f"external entries are not skipped: {skipped}")
    bad += check("--unit" in doc["jobs"][0]["certify_argv"]
                 and "setX" in doc["jobs"][0]["certify_argv"],
                 f"ordinary unit still gets a certify job: {doc['jobs'][0]}")
    return bad


def test_schedule_handles_fallback_receive_only_rows():
    data = manifest()
    row = data["subjects"][0]
    row["units"]["units"] = ["fallback", "receive"]
    row["units"]["unit_info"] = [
        {
            "name": "fallback",
            "state_mutability": "nonpayable",
            "parameter_count": 0,
            "return_count": 0,
            "visibility": "external",
            "implemented": True,
        },
        {
            "name": "receive",
            "state_mutability": "payable",
            "parameter_count": 0,
            "return_count": 0,
            "visibility": "external",
            "implemented": True,
        },
    ]
    row["target"]["units_hint"] = ["fallback", "receive"]
    row["unit_hints"] = {
        "hinted_units": ["fallback", "receive"],
        "missing_unit_hints": [],
        "pending_unit_hints": [],
    }
    doc = unit_schedule.build_schedule(data)
    bad = 0
    bad += check([job["unit"] for job in doc["jobs"]] == ["fallback", "receive"],
                 f"fallback/receive-only row gets real jobs: {doc['jobs']}")
    bad += check(doc["summary"]["no_unit_rows"] == 0,
                 f"external-entry row is not classified no-unit: {doc['summary']}")
    return bad


def test_schedule_records_ok_rows_without_schedulable_units():
    data = manifest()
    row = data["subjects"][0]
    row["units"] = {
        "schema": "veriput-subject-units/v1",
        "contract": "C",
        "units": [],
        "unit_info": [],
        "schedulable": False,
        "no_unit_reason": (
            "target contract has no public/external FunctionDefinition units"),
        "skipped": [{
            "contract": "C",
            "kind": "constructor",
            "reason": "constructor is not a focus-function",
        }],
    }
    row["unit_hints"] = {
        "hinted_units": [],
        "missing_unit_hints": [],
        "pending_unit_hints": [],
    }
    doc = unit_schedule.build_schedule(data)
    no_unit = doc["no_unit_rows"][0]
    bad = 0
    bad += check(doc["jobs"] == [], f"no empty-unit job is emitted: {doc['jobs']}")
    bad += check(doc["summary"]["no_unit_rows"] == 1,
                 f"no-unit subject is counted: {doc['summary']}")
    bad += check(no_unit["reason"] == row["units"]["no_unit_reason"],
                 f"no-unit reason is retained: {no_unit}")
    bad += check(no_unit["skipped"][0]["kind"] == "constructor",
                 f"skip diagnostics are retained: {no_unit}")
    return bad


def test_schedule_can_round_robin_across_benchmarks():
    data = manifest()
    rows = []
    for bench, subject_id, units in [
            ("bugfix124", "bug__A", ["b0", "b1", "b2"]),
            ("peer182", "peer__B", ["p0", "p1", "p2"]),
            ("stress243", "stress__C", ["s0", "s1", "s2"]),
    ]:
        subject = subject_record()
        subject["benchmark"] = bench
        subject["subject_id"] = subject_id
        subject["benchmark_key"] = f"{bench}__{subject_id}"
        subject["root"] = f"/tmp/{subject_id}"
        subject["contract"] = subject_id.rsplit("__", 1)[-1]
        rows.append({
            "subject": subject,
            "status": "ok",
            "target": {
                "benchmark": bench,
                "subject_id": subject_id,
                "contract": subject["contract"],
                "units_hint": units if bench == "bugfix124" else [],
            },
            "unit_hints": {
                "hinted_units": units if bench == "bugfix124" else [],
                "missing_unit_hints": [],
                "pending_unit_hints": [],
            },
            "units": {
                "schema": "veriput-subject-units/v1",
                "contract": subject["contract"],
                "units": units,
                "skipped": [],
            },
        })
    data["subjects"] = rows
    default_doc = unit_schedule.build_schedule(data, limit=3)
    rr_doc = unit_schedule.build_schedule(
        data, limit=6, selection_strategy="round-robin-benchmark")
    got_default = [job["benchmark"] for job in default_doc["jobs"]]
    got_rr = [(job["benchmark"], job["unit"]) for job in rr_doc["jobs"]]
    bad = 0
    bad += check(got_default == ["bugfix124", "bugfix124", "bugfix124"],
                 f"default priority sampling stays unchanged: {got_default}")
    bad += check(got_rr == [
        ("bugfix124", "b0"),
        ("peer182", "p0"),
        ("stress243", "s0"),
        ("bugfix124", "b1"),
        ("peer182", "p1"),
        ("stress243", "s1"),
    ], f"round-robin sampling balances benchmarks after priority sorting: {got_rr}")
    bad += check(rr_doc["selection_strategy"] == "round-robin-benchmark",
                 f"schedule records the selection strategy: {rr_doc.get('selection_strategy')}")
    bad += check(rr_doc["summary"]["by_benchmark"] == {
        "bugfix124": 2,
        "peer182": 2,
        "stress243": 2,
    }, f"round-robin summary is balanced: {rr_doc['summary']}")
    return bad


def test_schedule_can_round_robin_across_subjects():
    data = manifest()
    rows = []
    for subject_id, units in [
            ("repo__A", ["a0", "a1", "a2"]),
            ("repo__B", ["b0", "b1", "b2"]),
            ("repo__C", ["c0", "c1", "c2"]),
    ]:
        subject = subject_record()
        subject["subject_id"] = subject_id
        subject["benchmark_key"] = f"stress243__{subject_id}"
        subject["root"] = f"/tmp/{subject_id}"
        subject["contract"] = subject_id.rsplit("__", 1)[-1]
        rows.append({
            "subject": subject,
            "status": "ok",
            "target": {
                "benchmark": "stress243",
                "subject_id": subject_id,
                "contract": subject["contract"],
                "units_hint": [],
            },
            "unit_hints": {
                "hinted_units": [],
                "missing_unit_hints": [],
                "pending_unit_hints": [],
            },
            "units": {
                "schema": "veriput-subject-units/v1",
                "contract": subject["contract"],
                "units": units,
                "skipped": [],
            },
        })
    data["subjects"] = rows
    default_doc = unit_schedule.build_schedule(data, limit=4)
    rr_doc = unit_schedule.build_schedule(
        data, limit=7, selection_strategy="round-robin-subject")
    got_default = [(job["subject_id"], job["unit"]) for job in default_doc["jobs"]]
    got_rr = [(job["subject_id"], job["unit"]) for job in rr_doc["jobs"]]
    bad = 0
    bad += check(got_default == [
        ("repo__A", "a0"),
        ("repo__A", "a1"),
        ("repo__A", "a2"),
        ("repo__B", "b0"),
    ], f"default priority sampling keeps source order: {got_default}")
    bad += check(got_rr == [
        ("repo__A", "a0"),
        ("repo__B", "b0"),
        ("repo__C", "c0"),
        ("repo__A", "a1"),
        ("repo__B", "b1"),
        ("repo__C", "c1"),
        ("repo__A", "a2"),
    ], f"round-robin-subject spreads scarce attempts: {got_rr}")
    bad += check(rr_doc["selection_strategy"] == "round-robin-subject",
                 f"schedule records subject-level sampling: {rr_doc.get('selection_strategy')}")
    bad += check(rr_doc["summary"]["subjects"] == 3,
                 f"subject-level sample covers all subjects: {rr_doc['summary']}")
    return bad


def test_schedule_refuses_protected_write_paths():
    protected = "/home/samson/workspace/VeriPUT/Results/certify.jsonl"
    try:
        unit_schedule.build_schedule(manifest(), cert_out=protected)
    except unit_schedule.ScheduleError as exc:
        refused_cert = str(exc)
    else:
        refused_cert = ""
    data = manifest()
    data["ast_cache_root"] = "/home/samson/workspace/VeriPUT/Results/ast-cache"
    try:
        unit_schedule.build_schedule(data)
    except unit_schedule.ScheduleError as exc:
        refused_cache = str(exc)
    else:
        refused_cache = ""
    try:
        unit_schedule.build_schedule(
            manifest(), workdir="/home/samson/workspace/VeriPUT/Results/work")
    except unit_schedule.ScheduleError as exc:
        refused_work = str(exc)
    else:
        refused_work = ""
    cp = subprocess.run([
        sys.executable,
        str(ROOT / "notes" / "coverage" / "scripts" / "unit_schedule.py"),
        "-",
        "--out",
        "/home/samson/workspace/VeriPUT/Results/unit-schedule.json",
    ],
                        input=json.dumps(manifest()),
                        capture_output=True,
                        text=True)
    bad = 0
    bad += check("--cert-out must not be under" in refused_cert,
                 f"protected cert output is refused: {refused_cert}")
    bad += check("--ast-cache-root must not be under" in refused_cache,
                 f"protected AST cache is refused: {refused_cache}")
    bad += check("--workdir must not be under" in refused_work,
                 f"protected workdir is refused: {refused_work}")
    bad += check(cp.returncode != 0 and "--out must not be under" in cp.stderr,
                 f"protected unit schedule output is refused: {cp.stderr.strip()}")
    return bad


TESTS = [
    test_schedule_prioritizes_hinted_units_and_preserves_argv,
    test_schedule_prioritizes_cheap_getters_before_complex_state_units,
    test_schedule_deprioritizes_unhinted_initializers,
    test_schedule_orders_cheaper_units_inside_priority_bucket,
    test_schedule_prioritizes_ownership_transfer_before_business_mutators,
    test_schedule_deprioritizes_unhinted_admin_units,
    test_schedule_deprioritizes_recursive_helper_obstacles,
    test_schedule_cli_reads_stdin_and_applies_limit,
    test_schedule_deduplicates_prepared_subject_units,
    test_schedule_passes_unique_path_function_for_overloads,
        test_schedule_rehomes_inherited_unit_info_path_function_to_target_owner,
    test_inherited_only_contract_flows_from_ast_manifest_to_schedule,
    test_schedule_splits_same_arity_overloads_by_path_function,
    test_schedule_skips_unpinned_overloads_without_path_function,
    test_schedule_includes_fallback_and_receive_units,
    test_schedule_handles_fallback_receive_only_rows,
    test_schedule_records_ok_rows_without_schedulable_units,
    test_schedule_can_round_robin_across_benchmarks,
    test_schedule_can_round_robin_across_subjects,
    test_schedule_refuses_protected_write_paths,
]

if __name__ == "__main__":
    failures = 0
    for test in TESTS:
        try:
            failures += test()
        except Exception as exc:  # pragma: no cover - tiny script harness
            print(f"FAIL: {test.__name__}: {exc}")
            failures += 1
    raise SystemExit(1 if failures else 0)
