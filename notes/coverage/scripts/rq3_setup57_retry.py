#!/usr/bin/env python3
"""Retry the authenticated RQ3 setup-failure cluster outside Results.

The partition is the allowlist.  Every generator value is delivered through
the hash-sealed ``--concrete-certified-ce-json`` envelope; the retry never
writes a canonical RQ3 result or its original Foundry project.
"""

# This is a linear audit driver; splitting its command and row assembly into
# stateful objects would obscure the compare-before-run flow.
# pylint: disable=missing-function-docstring,too-many-arguments
# pylint: disable=too-many-positional-arguments,too-many-locals

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

EXPECTED = {
    "setup-unmocked-contract": 25,
    "setup-zero-address": 26,
    "setup-caller-mismatch": 2,
    "setup-domain-constraint": 1,
    "setup-other-revert": 3,
}


def canonical_sha(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identity_key(identity):
    return tuple(
        identity.get(name)
        for name in ("dataset", "case", "contract", "unit", "path_function", "enc", "piece"))


def sealed_envelope(partition_path, row):
    certification = row["certification"]
    paths = row["paths"]
    hashes = row["hashes"]
    return {
        "schema": "veriput-certified-ce-evidence/v1",
        "authenticity": "authenticated-ce-exact-identity/v1",
        "classification": "authenticated_ce_repairable",
        "identity": row["identity"],
        "evidence": {
            "partition_json": str(partition_path),
            "partition_json_sha256": file_sha(partition_path),
            "partition_row_sha256": canonical_sha(row),
            "certification_jsonl": paths["certification_jsonl"],
            "certification_jsonl_sha256": hashes["certification_jsonl_sha256"],
            "certification_row_sha256": hashes["certification_row_sha256"],
            "certification_field": certification["field"],
            "certification_key": certification["key"],
            "certification_detail_sha256": hashes["certification_detail_sha256"],
            "counterexample_sha256": hashes["counterexample_sha256"],
            "report_json": paths["report_json"],
            "report_json_sha256": hashes["report_json_sha256"],
            "report_claim_sha256": hashes["report_claim_sha256"],
            "source_sha256": hashes["project_flat_sha256"],
        },
    }


def exact_setup_rows(partition):
    rows = [
        row for row in partition.get("rows", [])
        if row.get("classification") == "authenticated_ce_repairable" and (
            row.get("generator_classification") or {}).get("root_cause_cluster") in EXPECTED
    ]
    counts = {name: 0 for name in EXPECTED}
    for row in rows:
        counts[row["generator_classification"]["root_cause_cluster"]] += 1
    if counts != EXPECTED or len(rows) != sum(EXPECTED.values()):
        raise ValueError(f"authenticated setup partition drift: {counts}")
    keys = [identity_key(row["identity"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("authenticated setup partition contains duplicate identities")
    return rows


def inventory_index(inventory):
    return {identity_key(row["identity"]): row for row in inventory.get("entries", [])}


def copy_project(source, destination):
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("out", "cache"))


def append_json_arg(command, flag, value):
    command.extend((flag, json.dumps(value, sort_keys=True, separators=(",", ":"))))


def generator_command(generator, row, inventory_row, project, workdir, envelope, reuse_dir):
    with open(row["paths"]["put_json"], encoding="utf-8") as stream:
        old_put = json.load(stream)
    command = [
        sys.executable,
        str(generator),
        "--sol",
        str(project / "src" / "flat.sol"),
        "--contract",
        row["identity"]["contract"],
        "--unit",
        row["identity"]["unit"],
        "--path-function",
        row["identity"]["path_function"],
        "--enc",
        str(row["identity"]["enc"]),
        "--forge-project",
        str(project),
        "--workdir",
        str(workdir),
        "--reuse-emitted-dir",
        str(reuse_dir),
        "--timeout",
        "180",
        "--concrete-only",
        "--concrete-stage2-source",
        row["generator_classification"]["stage2_source"],
        "--concrete-stage2-witness-check",
        row["generator_classification"]["stage2_witness_check"],
        "--concrete-certified-ce-json",
        json.dumps(envelope, sort_keys=True, separators=(",", ":")),
        "--test-suffix",
        "_setup57",
    ]
    if old_put.get("depth") is not None:
        command.extend(("--depth", str(old_put["depth"])))
    append_json_arg(command, "--region", old_put.get("region", {}))
    append_json_arg(command, "--holes", old_put.get("holes", {}))
    append_json_arg(command, "--establish", old_put.get("establish", []))
    append_json_arg(command, "--extcall-length-coordinates",
                    old_put.get("extcall_length_coordinates", []))
    append_json_arg(command, "--extcall-pins", old_put.get("extcall_pins", {}))
    for name, value in sorted((old_put.get("pins") or {}).items()):
        command.extend(("--pin", f"{name}={value}"))
    max_tx = ((old_put.get("cell") or {}).get("max_tx"))
    if max_tx is not None:
        command.extend(("--max-tx", str(max_tx)))
    certification_source = str(row.get("certification", {}).get("source") or "")
    stage4_kind = "abi-value-gate" if certification_source.startswith(
        "structural-abi-gate") else "certified-region"
    command.extend(("--stage4-kind", stage4_kind))
    oracles = inventory_row.get("concrete_oracles") or []
    if oracles:
        command.extend(
            ("--concrete-oracles-json", json.dumps(oracles, sort_keys=True, separators=(",", ":"))))
    for argument in old_put.get("esbmc_extra_args") or []:
        command.append("--esbmc-arg=" + str(argument))
    return command


def run(command, timeout):
    return subprocess.run(command,
                          text=True,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT,
                          timeout=timeout,
                          check=False)


def forge_exact(project, generated_put):
    source = Path(generated_put["file"])
    try:
        match_path = source.relative_to(project)
    except ValueError:
        return None, "generated test escaped the temporary Foundry project"
    test_name = generated_put["test"]
    command = [
        "forge", "test", "--root",
        str(project), "--match-path",
        str(match_path), "--match-test", "^" + re.escape(test_name) + r"\(", "-vv"
    ]
    try:
        completed = run(command, 240)
    except subprocess.TimeoutExpired:
        return {"command": command, "timed_out": True}, "forge timeout"
    passed = re.findall(r"\[PASS\]\s+" + re.escape(test_name) + r"\(", completed.stdout)
    tests = re.findall(r"Ran\s+(\d+)\s+test", completed.stdout)
    ok = completed.returncode == 0 and len(passed) == 1 and tests and int(tests[-1]) == 1
    result = {
        "command": command,
        "returncode": completed.returncode,
        "timed_out": False,
        "exact_pass_lines": len(passed),
        "reported_tests": int(tests[-1]) if tests else None,
        "diagnostic": completed.stdout[-4000:],
    }
    return result, None if ok else "exact forge test did not report one PASS"


def retry_row(args, row, inventory_row, index):
    identity = row["identity"]
    label = (f"{index:03d}_{identity['dataset']}__{identity['case']}__"
             f"{identity['unit']}__{identity['enc']}")
    row_root = args.work_root / label
    project = row_root / "project"
    workdir = row_root / "work"
    old_project = Path(row["paths"]["generated_source"]).parents[1]
    copy_project(old_project, project)
    reuse_dir = Path(row["paths"]["put_json"]).parent / "emit"
    if not (reuse_dir / "cov-report.json").is_file():
        reuse_dir = row_root / "sealed-reuse"
        reuse_dir.mkdir()
        shutil.copy2(row["paths"]["generated_source"],
                     reuse_dir / f"{identity['contract']}.cov.t.sol")
        shutil.copy2(row["paths"]["report_json"], reuse_dir / "cov-report.json")
    envelope = sealed_envelope(args.partition, row)
    command = generator_command(args.generator, row, inventory_row, project, workdir, envelope,
                                reuse_dir)
    try:
        generated = run(command, 240)
    except subprocess.TimeoutExpired:
        return {"identity": identity, "status": "refused", "reason": "generator timeout"}
    put_path = workdir / "put.json"
    record = {
        "identity": identity,
        "cluster": row["generator_classification"]["root_cause_cluster"],
        "generator_command": command,
        "generator_returncode": generated.returncode,
        "generator_diagnostic": generated.stdout[-4000:],
    }
    if generated.returncode != 0 or not put_path.exists():
        record.update(status="refused", reason="sealed generator refusal")
        return record
    with open(put_path, encoding="utf-8") as stream:
        generated_put = json.load(stream)
    if generated_put.get("kind") != "concrete" or not generated_put.get("file"):
        record.update(status="refused",
                      reason=(generated_put.get("refusal_reason")
                              or "generator did not emit a concrete test"))
        return record
    binding = generated_put.get("certified_ce_binding") or {}
    if not binding.get("sealed_evidence") or binding.get("setup_evidence") is None:
        record.update(status="refused", reason="generated PUT lacks sealed setup audit")
        return record
    forge, reason = forge_exact(project, generated_put)
    record["forge"] = forge
    if reason:
        record.update(status="refused", reason=reason)
    else:
        record.update(status="publishable",
                      reason=None,
                      generated_file=generated_put["file"],
                      test=generated_put["test"],
                      setup_evidence=binding["setup_evidence"])
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--partition", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--generator", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--contract", action="append", default=[])
    args = parser.parse_args()
    if args.output.exists() or args.work_root.exists():
        raise SystemExit("output and work-root must be new paths")
    with open(args.partition, encoding="utf-8") as stream:
        partition = json.load(stream)
    with open(args.inventory, encoding="utf-8") as stream:
        inventory = json.load(stream)
    rows = exact_setup_rows(partition)
    if args.contract:
        rows = [row for row in rows if row["identity"]["contract"] in args.contract]
    indexed = inventory_index(inventory)
    missing = [row["identity"] for row in rows if identity_key(row["identity"]) not in indexed]
    if missing:
        raise SystemExit(f"repair inventory lacks {len(missing)} authenticated identities")
    args.work_root.mkdir(parents=True)
    records = []
    for index, row in enumerate(rows, 1):
        record = retry_row(args, row, indexed[identity_key(row["identity"])], index)
        records.append(record)
        print(
            f"[{index}/{len(rows)}] {record['status']} "
            f"{row['identity']['contract']}.{row['identity']['unit']}: "
            f"{record.get('reason') or 'exact forge PASS'}",
            flush=True)
    summary = {
        "selected": len(records),
        "publishable": sum(item["status"] == "publishable" for item in records),
        "refused": sum(item["status"] == "refused" for item in records),
    }
    output = {
        "schema": "veriput-rq3-authenticated-setup57-retry/v1",
        "canonical_write": False,
        "partition_sha256": file_sha(args.partition),
        "summary": summary,
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(output, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
