#!/usr/bin/env python3
"""Revalidate canonical RQ1 PUT proof obligations with clean k-induction.

This runner is deliberately read-only with respect to RQ1.  It reconstructs
queries from retained PUT workdirs and writes resumable evidence to a sibling
audit directory.  One inventory item is one physical PUT (exact put.json
SHA-256), not a result-row retry.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import signal
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from rq1_concrete_replay_migrate import DEFAULT_RESULT_ROOT, _case_dirs, _strict_valid_tests
from rq1_concrete_replay_store import _physical_test_kind

DEFAULT_OUTPUT = Path("/home/samson/workspace/VeriPUT/Results/RQ1_KInduction_Audit")
DEFAULT_ESBMC = Path("/home/samson/workspace/esbmc/build/src/esbmc/esbmc")
AST_LOCK = threading.Lock()
PROCESS_LOCK = threading.Lock()
ACTIVE_PROCESSES: set[subprocess.Popen] = set()
SOURCE_ROOT = Path("/home/samson/workspace/VeriPUT/scripts/Results/workdirs")

VALUE_OPTIONS = {
    "--base-k-step", "--k-step", "--max-inductive-step", "--max-k-step",
    "--memlimit", "--unwind", "--unwindset", "--path-cov-assert",
    "--path-cov-certify",
}
FLAG_OPTIONS = {
    "--incremental-bmc", "--k-induction", "--unlimited-k-steps",
    "--disable-forward-condition", "--enable-forward-condition",
    "--partial-loops", "--overflow-check",
    "--unsigned-overflow-check", "--signed-overflow-check",
    "--div-by-zero-check", "--no-div-by-zero-check",
    "--result-only", "--cov-report-json", "--path-cov-arith-resolve",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def retained_command(workdir: Path) -> list[str] | None:
    for log in sorted((workdir / "assert").glob("run*.log")):
        first = log.read_text(errors="replace").splitlines()[:1]
        if not first:
            continue
        words = shlex.split(first[0])
        for i, word in enumerate(words):
            if word.endswith("/esbmc") or word == "esbmc":
                command = words[i:]
                try:
                    sol_index = command.index("--sol")
                except ValueError:
                    return command
                option_index = sol_index + 1
                while (option_index < len(command) and
                       not command[option_index].startswith("--")):
                    option_index += 1
                # Some retained logs did not shell-quote source paths with
                # spaces. Reassemble the single --sol value before recovery.
                command[sol_index + 1:option_index] = [
                    " ".join(command[sol_index + 1:option_index])
                ]
                return command
    return None


def clean_command(command: list[str], esbmc: Path, memlimit: str) -> list[str]:
    cleaned = [str(esbmc)]
    i = 1
    while i < len(command):
        word = command[i]
        if word in VALUE_OPTIONS or word.startswith("--unwind"):
            i += 2
            continue
        if word in FLAG_OPTIONS or word == "--solidity-max-tx":
            i += 2 if word == "--solidity-max-tx" else 1
            continue
        cleaned.append(word)
        i += 1
    cleaned.extend([
        "--k-induction", "--enable-forward-condition", "--max-k-step", "30",
        "--solidity-max-tx", "1",
        "--memlimit", memlimit, "--result-only", "--cov-report-json",
    ])
    return cleaned


def materialize_ast(command: list[str], output: Path, subject: str,
                    put_json: Path) -> list[str]:
    """Replace an expired AST-cache input with an isolated source-hash AST."""
    if len(command) < 2 or Path(command[1]).is_file():
        return command
    try:
        sol_index = command.index("--sol")
        source = Path(command[sol_index + 1])
    except (ValueError, IndexError):
        return command
    if not source.is_file():
        put_unit_dir = put_json.parents[2]
        matches = list(put_unit_dir.glob("*certify-results/src/flat.sol"))
        if len(matches) != 1:
            matches = list(SOURCE_ROOT.glob(f"*/subjects/{subject}/flat.sol"))
        if len(matches) != 1:
            return command
        source = matches[0]
        command[sol_index + 1] = str(source)
    ast = output / "asts" / f"{sha256(source)}.solast"
    with AST_LOCK:
        if not ast.is_file():
            ast.parent.mkdir(parents=True, exist_ok=True)
            compiler = Path("solc")
            pragma = re.search(
                r"\bpragma\s+solidity\s*=\s*(\d+\.\d+\.\d+)\s*;",
                source.read_text(errors="replace"))
            if pragma:
                cached = Path.home() / ".solcx" / f"solc-v{pragma.group(1)}"
                if cached.is_file():
                    compiler = cached
            completed = subprocess.run(
                [str(compiler), "--ast-compact-json", str(source)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            if completed.returncode != 0:
                return command
            with tempfile.NamedTemporaryFile("wb", dir=ast.parent, delete=False) as stream:
                stream.write(completed.stdout)
                temporary = Path(stream.name)
            os.replace(temporary, ast)
    return [command[0], str(ast), *command[2:]]


def inventory(root: Path) -> list[dict]:
    unique: dict[tuple, dict] = {}
    for case, subject_dir in _case_dirs(root):
        for row in _strict_valid_tests(subject_dir):
            if _physical_test_kind(row) != "put":
                continue
            put_json = Path(row.get("put_json") or "")
            if not put_json.is_file():
                continue
            data = json.loads(put_json.read_text())
            stats = data.get("stats") or {}
            final_oracles = [
                {
                    "var": oracle.get("var"),
                    "text": oracle.get("text"),
                    "layer": oracle.get("layer"),
                    "classes": oracle.get("classes") or [],
                }
                for oracle in stats.get("assertion_oracles") or []
                if oracle.get("verdict") == "HOLDS"
                and oracle.get("emitted_in_test") is True
            ]
            stage4_kind = data.get("stage4_kind")
            if stage4_kind == "abi-value-gate":
                for oracle in final_oracles:
                    if oracle.get("layer") == "exit":
                        oracle["text"] = "path exits through revert"
            digest = sha256(put_json)
            key = (case, str(data.get("path_function") or ""),
                   str(data.get("unit") or row.get("unit") or ""),
                   str(data.get("enc") if data.get("enc") is not None else ""),
                   str(data.get("piece") if data.get("piece") is not None else ""))
            unique[key] = {
                "case": case,
                "subject_dir": str(subject_dir),
                "put_json": str(put_json),
                "put_json_sha256": digest,
                "path_function": key[1], "unit": key[2], "enc": key[3], "piece": key[4],
                "test": data.get("test") or row.get("test"),
                "oracle_classes": sorted(set(stats.get("oracle_classes") or [])),
                "exit_kind": ("revert" if stage4_kind == "abi-value-gate"
                              else stats.get("exit_kind")),
                "stage4_kind": stage4_kind,
                # Only emitted final-test assertions are proof obligations.  The
                # raw ladder also contains superseded and deliberately dropped
                # candidates (notably vacuous return candidates on revert paths).
                "expected_ladder": final_oracles,
            }
    return [unique[key] for key in sorted(unique)]


def stratified_sample(items: list[dict], size: int) -> list[dict]:
    """Select a deterministic round-robin sample across dataset and oracle shape."""
    groups: dict[tuple, list[dict]] = {}
    for item in items:
        dataset = item["case"].split("/", 1)[0]
        classes = "+".join(item.get("oracle_classes") or ["none"])
        key = (dataset, classes, item.get("exit_kind") or "unknown")
        groups.setdefault(key, []).append(item)
    for values in groups.values():
        values.sort(key=lambda item: hashlib.sha256(
            (item["case"] + item["path_function"] + item["enc"] + item["piece"]).encode()
        ).hexdigest())
    selected = []
    keys = sorted(groups)
    while len(selected) < min(size, len(items)):
        progressed = False
        for key in keys:
            if groups[key] and len(selected) < size:
                selected.append(groups[key].pop(0))
                progressed = True
        if not progressed:
            break
    return selected


def certify_spec(assert_spec: dict, put_data: dict) -> dict:
    box = []
    holes = put_data.get("holes") or {}
    state_stored = (put_data.get("stats") or {}).get("state_stored")
    established_state = None
    if isinstance(state_stored, list):
        established_state = {
            entry.split(":=", 1)[0].strip()
            for entry in state_stored
            if isinstance(entry, str) and ":=" in entry
        }
    for name, bounds in (put_data.get("region") or {}).items():
        item = {"name": name, "lo": str(bounds[0]), "hi": str(bounds[1])}
        if holes.get(name):
            item["holes"] = [str(value) for value in holes[name]]
        box.append(item)
    for name, value in (put_data.get("pins") or {}).items():
        if name in (put_data.get("region") or {}):
            continue
        # A retained counterexample can contain state coordinates that the
        # final Foundry PUT explicitly could not establish.  Revalidation must
        # prove the materialized PUT's domain, not those dropped coordinates.
        if (name.startswith("state.") and established_state is not None and
                name not in established_state):
            continue
        box.append({"name": name, "lo": str(value), "hi": str(value)})
    result = {
        "unit": assert_spec["unit"], "enc": assert_spec["enc"],
        "depth": assert_spec.get("depth", 1), "box": box,
    }
    # Stage 4 uses enc=1/depth=0 as a materialization marker for the ABI
    # nonpayable value gate.  In the instrumented CFG the actual rejecting
    # edge is the first false decision: seed 1 becomes enc=2 at depth 1.
    if (put_data.get("stage4_kind") == "abi-value-gate" and
            str(assert_spec.get("enc")) == "1" and
            int(assert_spec.get("depth", 0)) == 0):
        result["enc"] = 2
        result["depth"] = 1
    if put_data.get("establish"):
        result["establish"] = put_data["establish"]
    return result


def final_assert_spec(assert_spec: dict, expected_oracles: list[dict]) -> dict | None:
    """Keep only variables used by assertions emitted in the final PUT.

    A variable spec contains structured candidates whose rendered text is owned
    by ESBMC.  Until that renderer is shared with this runner, retain every
    candidate for an expected variable rather than risk dropping the real proof
    obligation.  Variables absent from the final test are historical search
    candidates and must not consume revalidation time.
    """
    expected_vars = {
        str(oracle.get("var")) for oracle in expected_oracles
        if oracle.get("layer") != "exit" and oracle.get("var") is not None
    }
    variables = [
        variable for variable in assert_spec.get("vars") or []
        if str(variable.get("name")) in expected_vars
    ]
    if not variables:
        return None
    return {**assert_spec, "vars": variables}


def ordered_assert_specs(paths: list[Path]) -> list[Path]:
    """Try the authoritative base ladder before optional R2 search batches."""
    return sorted(paths, key=lambda path: (path.name != "spec.json", path.name))


def verdict(output: str, returncode: int, timed_out: bool) -> str:
    if timed_out:
        return "timeout"
    if re.search(
            r"REFUSING THE QUERY: path enc=\d+ is not among this unit's "
            r"\d+ enumerated path\(s\)", output):
        return "stale-path"
    certify = re.findall(r"--path-cov-certify: RESULT: ([A-Z][A-Z-]*)", output)
    if certify:
        return {
            "CERTIFIED": "proved", "REFUTED": "refuted",
            "VACUOUS": "inconclusive", "UNDECIDED": "inconclusive",
            "UNDECIDED-TRUNCATED": "inconclusive",
        }.get(certify[-1], "error")
    ladder = re.findall(
        r"ladder summary -- \d+ candidate\(s\): (\d+) HOLDS, (\d+) REFUTED, "
        r"(\d+) no verdict \(solver unknown\), (\d+) no verdict", output)
    if ladder:
        holds, refuted, unknown, unreached = map(int, ladder[-1])
        if refuted:
            return "refuted"
        if unknown or unreached:
            return "inconclusive"
        return "proved" if holds else "inconclusive"
    # Query refusals describe the possible RESULT values in their diagnostic.
    # They are not solver verdicts and must remain fail-closed.
    if "REFUSING THE QUERY" in output or "ERROR:" in output:
        return "error"
    if re.search(r"(?m)^VERIFICATION FAILED$", output):
        return "refuted"
    if re.search(r"(?m)^VERIFICATION SUCCESSFUL$", output):
        return "proved"
    if re.search(r"(?m)^VERIFICATION UNKNOWN$", output):
        return "inconclusive"
    if returncode < 0 or "Out of memory" in output or "bad_alloc" in output:
        return "oom-or-killed"
    return "error"


def run_query(command: list[str], cwd: Path, log: Path, timeout: int) -> dict:
    started = time.time()
    timed_out = False
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, errors="replace",
                               start_new_session=True)
    with PROCESS_LOCK:
        ACTIVE_PROCESSES.add(process)
    try:
        output, _ = process.communicate(timeout=timeout)
        returncode = process.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        os.killpg(process.pid, signal.SIGTERM)
        try:
            tail, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            tail, _ = process.communicate()
        def decoded(value: str | bytes | None) -> str:
            if isinstance(value, bytes):
                return value.decode(errors="replace")
            return value or ""

        output = decoded(exc.stdout) + decoded(tail) + "\nHARD TIMEOUT\n"
        returncode = 124
    finally:
        with PROCESS_LOCK:
            ACTIVE_PROCESSES.discard(process)
    log.write_text(output)
    return {
        "command": command, "returncode": returncode,
        "elapsed_seconds": round(time.time() - started, 3),
        "status": verdict(output, returncode, timed_out),
        "log": str(log),
    }


def report_path_exit_kind(report: Path, enc: str) -> str | None:
    """Read the certified path's own exit kind from its non-vacuity row."""
    if not report.is_file():
        return None
    try:
        claims = json.loads(report.read_text()).get("claims") or []
    except (OSError, json.JSONDecodeError):
        return None
    expected_id = f"{enc}#nonvacuous"
    kinds = {
        str(claim.get("exit_kind"))
        for claim in claims
        if str(claim.get("path_id")) == expected_id
        and claim.get("exit_kind") in {"normal", "revert", "undetermined"}
    }
    return next(iter(kinds)) if len(kinds) == 1 else None


def certify_path_enc(log: Path, fallback: str) -> str:
    """Return the current path id after any historical-path remapping."""
    spec = log.parent / "certify.json"
    try:
        enc = json.loads(spec.read_text()).get("enc")
    except (OSError, json.JSONDecodeError):
        return fallback
    return str(enc) if enc is not None else fallback


def classify_obligation(item: dict, queries: list[dict]) -> tuple[str, list[dict]]:
    """Classify only the region and final PUT oracles, never rejected candidates."""
    if not queries:
        return "error", []
    region_status = queries[0].get("status")
    observed = []
    assertion_output = "\n".join(
        Path(query["log"]).read_text(errors="replace")
        for query in queries[1:] if Path(query.get("log") or "").is_file())
    for expected in item.get("expected_ladder") or []:
        if expected.get("layer") == "exit":
            expected_exit = {
                "path exits normally": "normal",
                "path exits through revert": "revert",
            }.get(expected.get("text"))
            observed_exit = queries[0].get("path_exit_kind")
            if region_status != "proved" or expected_exit is None:
                status = "error"
            elif observed_exit == expected_exit:
                status = "proved"
            elif observed_exit in {"normal", "revert"}:
                status = "refuted"
            else:
                status = "inconclusive"
            observed.append({**expected, "status": status})
            continue
        pattern = (re.escape(str(expected.get("var") or "")) + r":\s*" +
                   re.escape(str(expected.get("text") or "")) +
                   r"\s+(HOLDS|REFUTED|UNDECIDED)")
        matches = re.findall(pattern, assertion_output)
        status = ({"HOLDS": "proved", "REFUTED": "refuted",
                   "UNDECIDED": "inconclusive"}.get(matches[-1])
                  if matches else "inconclusive")
        proof_basis = None
        if status == "inconclusive" and expected.get("text") == (
                f"{expected.get('var')} != 0"):
            equality_pattern = (
                re.escape(str(expected.get("var") or "")) + r":\s*" +
                re.escape(str(expected.get("var") or "")) +
                r" == (-?[0-9]+)\s+HOLDS")
            proved_constants = re.findall(equality_pattern, assertion_output)
            nonzero = next(
                (value for value in reversed(proved_constants) if int(value) != 0),
                None)
            if nonzero is not None:
                status = "proved"
                proof_basis = (
                    f"implied by {expected.get('var')} == {nonzero} HOLDS")
        result = {**expected, "status": status}
        if proof_basis is not None:
            result["proof_basis"] = proof_basis
        observed.append(result)
    statuses = [region_status, *(entry["status"] for entry in observed)]
    if "stale-path" in statuses:
        return "stale-path", observed
    if "refuted" in statuses:
        return "refuted", observed
    if "timeout" in statuses:
        return "timeout", observed
    if "error" in statuses or "oom-or-killed" in statuses:
        return "error", observed
    if all(status == "proved" for status in statuses):
        return "proved", observed
    return "inconclusive", observed


def refresh_result(item: dict, retained: dict) -> dict:
    queries = retained.get("queries") or []
    for query in queries:
        log = Path(query.get("log") or "")
        if not log.is_file():
            query["status"] = "error"
            continue
        query["status"] = verdict(log.read_text(errors="replace"),
                                  int(query.get("returncode") or 0),
                                  query.get("status") == "timeout")
        query["path_exit_kind"] = report_path_exit_kind(
            log.parent / "cov-report.json",
            certify_path_enc(log, item["enc"]))
    status, observed = classify_obligation(item, queries)
    return {**retained, **item, "status": status,
            "final_oracle_results": observed}


def run_item(item: dict, output: Path, esbmc: Path, memlimit: str,
             timeout: int, resume: bool) -> dict:
    artifact_id = item["put_json_sha256"][:16]
    destination = output / "artifacts" / artifact_id
    result_file = destination / "result.json"
    if resume and result_file.is_file():
        retained = json.loads(result_file.read_text())
        retained = refresh_result(item, retained)
        atomic_json(result_file, retained)
        if retained.get("status") not in {"error", "oom-or-killed"}:
            return retained
    destination.mkdir(parents=True, exist_ok=True)
    put_json = Path(item["put_json"])
    workdir = put_json.parent
    old_command = retained_command(workdir)
    specs = ordered_assert_specs(list((workdir / "assert").glob("spec*.json")))
    if old_command is None or not specs:
        result = {**item, "status": "missing-evidence", "queries": []}
        atomic_json(result_file, result)
        return result
    base = materialize_ast(clean_command(old_command, esbmc, memlimit), output,
                           Path(item["subject_dir"]).name, put_json)
    primary = json.loads(specs[0].read_text())
    local_certify = destination / "certify.json"
    put_data = json.loads(put_json.read_text())
    atomic_json(local_certify, certify_spec(primary, put_data))
    queries = []
    command = base + ["--path-cov-certify", str(local_certify)]
    queries.append(run_query(command, destination, destination / "certify.log", timeout))
    queries[0]["path_exit_kind"] = report_path_exit_kind(
        destination / "cov-report.json", str(json.loads(local_certify.read_text())["enc"]))
    region_status = queries[0]["status"]
    assertion_oracles = [
        oracle for oracle in item.get("expected_ladder") or []
        if oracle.get("layer") != "exit"
    ]
    if region_status == "proved" and assertion_oracles:
        for index, source_spec in enumerate(specs):
            filtered_spec = final_assert_spec(
                json.loads(source_spec.read_text()), assertion_oracles)
            if filtered_spec is None:
                continue
            local_spec = destination / f"assert-{index}.json"
            atomic_json(local_spec, filtered_spec)
            command = base + ["--path-cov-assert", str(local_spec)]
            queries.append(
                run_query(command, destination,
                          destination / f"assert-{index}.log", timeout))
            if queries[-1]["status"] in {
                    "timeout", "error", "oom-or-killed", "stale-path"
            }:
                break
            partial_status, _ = classify_obligation(item, queries)
            if partial_status in {"proved", "refuted"}:
                break
    overall, observed = classify_obligation(item, queries)
    result = {**item, "status": overall, "queries": queries,
              "final_oracle_results": observed}
    atomic_json(result_file, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--esbmc", type=Path, default=DEFAULT_ESBMC)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sample-size", type=int)
    parser.add_argument("--artifact-sha", help="Run exactly one PUT JSON SHA-256 prefix")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--memlimit", default="4g")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args()
    items = inventory(args.result_root)
    atomic_json(args.output / "inventory.json", {
        "schema": "veriput-rq1-put-kinduction-inventory/v1",
        "scope": "canonical-current physical valid PUT artifacts",
        "count": len(items), "items": items,
    })
    if args.artifact_sha:
        selected = [item for item in items
                    if item["put_json_sha256"].startswith(args.artifact_sha)]
        if len(selected) != 1:
            parser.error(f"--artifact-sha matched {len(selected)} inventory items")
    elif args.sample_size:
        selected = stratified_sample(items, args.sample_size)
        atomic_json(args.output / f"sample-{args.sample_size}.json", {
            "schema": "veriput-rq1-put-kinduction-sample/v1",
            "population_count": len(items), "sample_count": len(selected),
            "selection": "dataset/oracle-classes/exit-kind round-robin; stable SHA ordering",
            "items": selected,
        })
    else:
        selected = items[:args.limit] if args.limit else items
    if args.inventory_only:
        print(json.dumps({"population_count": len(items), "selected_count": len(selected)},
                         sort_keys=True))
        return 0
    results = []
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs)
    futures = [executor.submit(run_item, item, args.output, args.esbmc,
                               args.memlimit, args.timeout, not args.no_resume)
               for item in selected]
    try:
        for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
            result = future.result()
            results.append(result)
            print(f"[{completed}/{len(selected)}] {result['status']} {result['case']} "
                  f"{result['unit']} enc={result['enc']}", flush=True)
    except KeyboardInterrupt:
        for future in futures:
            future.cancel()
        with PROCESS_LOCK:
            processes = list(ACTIVE_PROCESSES)
        for process in processes:
            os.killpg(process.pid, signal.SIGTERM)
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    executor.shutdown()
    counts = {status: sum(result["status"] == status for result in results)
              for status in sorted({result["status"] for result in results})}
    atomic_json(args.output / "summary.json", {
        "schema": "veriput-rq1-put-kinduction-summary/v1",
        "inventory_count": len(items), "selected_count": len(selected),
        "completed_count": len(results), "status_counts": counts,
        "isolated": True, "rq1_modified": False,
    })
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
