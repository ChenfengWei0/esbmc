#!/usr/bin/env python3
"""For each upgraded+compiling contract, pick ONE likely function pair
(two public/external functions whose bodies touch overlapping state
variables) and run ESBMC with `--tod-race-check=fa,fb` under the hardened
wrapper.  Results -> results/<name>/run.{stdout,stderr,exitcode,pair}.

Per-run budget:
  wall:    300 s   (timeout 300)
  CPU:     270 s   (ulimit -t 270)
  RSS:     4 GB    (ulimit -v 4000000)
Outer concurrency: 1 (sequential, avoids WSL OOM).

Contracts that fail to compile (round2_status.json != "ok") are skipped.
"""
from __future__ import annotations
import json, os, re, subprocess, sys, time
from pathlib import Path

ROOT = Path("/home/samson/workspace/esbmc/Dataset/transracer_50")
SOURCES = ROOT / "sources"
RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)
ESBMC = "/home/samson/workspace/esbmc/build/src/esbmc/esbmc"
WALL = 300
CPU = 270
VMEM_KB = 4000000


# state-variable write regex: look for `<ident> = ...;` or `<ident>++;` etc
# (heuristic — we only care about approximate overlap)
WRITE_RE = re.compile(r"\b([a-zA-Z_]\w*)\s*(?:=|\+=|-=|\*=|/=|\+\+|--|\[[^\]]*\]\s*=)")
FUNC_RE = re.compile(
    r"function\s+(\w+)\s*\(([^)]*)\)\s+([^{]+)\{([^}]*(?:\{[^}]*\}[^}]*)*)\}",
    re.DOTALL,
)


def extract_contract_body(sol_text: str, cname: str) -> str | None:
    """Return body of the named contract (brace-balanced)."""
    # find `contract NAME ... {` — handles `abstract contract`, `is Base`
    m = re.search(rf"\bcontract\s+{re.escape(cname)}\b[^{{]*\{{", sol_text)
    if not m:
        return None
    start = m.end() - 1   # position of `{`
    depth = 0
    i = start
    while i < len(sol_text):
        if sol_text[i] == "{":
            depth += 1
        elif sol_text[i] == "}":
            depth -= 1
            if depth == 0:
                return sol_text[start:i+1]
        i += 1
    return None


def parse_public_funcs(body: str) -> list[tuple[str, str, set[str]]]:
    """List (fname, sig_line, written_state_vars_approx)."""
    funcs = []
    for m in FUNC_RE.finditer(body):
        fname, params, sig_rest, fbody = m.groups()
        # skip constructors and private/internal
        if not re.search(r"\b(public|external)\b", sig_rest):
            continue
        if fname.startswith("_"):
            continue
        writes = set()
        for wm in WRITE_RE.finditer(fbody):
            w = wm.group(1)
            if w not in ("require", "assert", "revert", "emit", "return", "if", "else", "for", "while"):
                writes.add(w)
        funcs.append((fname, sig_rest.strip(), writes))
    return funcs


def pick_pair(funcs: list[tuple[str, str, set[str]]]) -> tuple[str, str] | None:
    """Pick a pair (fa, fb) with maximum write-overlap, tie-broken by first-two."""
    if len(funcs) < 2:
        return None
    best = None
    best_overlap = -1
    for i, fi in enumerate(funcs):
        for fj in funcs[i+1:]:
            overlap = len(fi[2] & fj[2])
            if overlap > best_overlap:
                best_overlap = overlap
                best = (fi[0], fj[0])
    return best


def run_esbmc(name: str, contract: str, pair: tuple[str, str]) -> dict:
    sol_dir = SOURCES / name
    out_dir = RESULTS / name
    out_dir.mkdir(exist_ok=True)
    fa, fb = pair
    cmd = (
        f"timeout {WALL} bash -c '"
        f"ulimit -v {VMEM_KB}; ulimit -t {CPU}; "
        f"exec \"{ESBMC}\" contract.sol "
        f"--contract \"{contract}\" "
        f"--tod-race-check={fa},{fb} --tod-jobs=1 "
        f"--bound --unwind 3 --no-unwinding-assertions "
        f"--cvc5"
        f"'"
    )
    (out_dir / "cmd").write_text(cmd + "\n")
    (out_dir / "pair").write_text(f"{fa},{fb}\n")
    t0 = time.time()
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=sol_dir,
            capture_output=True, text=True,
            timeout=WALL + 10,
        )
        rc = r.returncode
        (out_dir / "run.stdout").write_text(r.stdout)
        (out_dir / "run.stderr").write_text(r.stderr)
    except subprocess.TimeoutExpired:
        rc = 124
        (out_dir / "run.stdout").write_text("")
        (out_dir / "run.stderr").write_text("timeout\n")
    (out_dir / "run.exitcode").write_text(str(rc))
    elapsed = time.time() - t0

    # parse verdict
    full = (out_dir / "run.stderr").read_text() + (out_dir / "run.stdout").read_text()
    summary_match = re.search(
        r"--tod-race-check summary: (\d+) pair\(s\) — (\d+) clean, (\d+) TOD found, (\d+) error",
        full,
    )
    if summary_match:
        verdict = "summarised"
        pairs, clean, found, error = map(int, summary_match.groups())
    elif "discovered 0 candidate pair(s)" in full:
        verdict = "no_pairs"; pairs = clean = found = error = 0
    elif rc == 124:
        verdict = "timeout"; pairs = clean = found = error = 0
    elif "boost::bad_any_cast" in full or "Aborted" in full or "Segmentation" in full or rc < 0:
        verdict = "crash"; pairs = clean = found = error = 0
    else:
        verdict = "unknown"; pairs = clean = found = error = 0

    return {
        "name": name, "pair": f"{fa},{fb}",
        "elapsed_s": round(elapsed, 1),
        "exit_code": rc,
        "verdict": verdict,
        "pairs": pairs, "clean": clean, "tod_found": found, "error": error,
    }


def main():
    selected = json.loads((ROOT / "selected.json").read_text())
    picked_map = json.loads((ROOT / "picked_map.json").read_text())

    results = []
    for name in selected:
        sol_dir = SOURCES / name
        sol = sol_dir / "contract.sol"
        if not sol.exists():
            results.append({"name": name, "verdict": "no_source"})
            continue

        # check compile
        rc = subprocess.run(
            ["solc", "--stop-after", "parsing", "contract.sol"],
            cwd=sol_dir, capture_output=True, text=True, timeout=60,
        ).returncode
        if rc != 0:
            r = subprocess.run(
                ["solc", "--bin", "contract.sol"],
                cwd=sol_dir, capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0 and "Error:" in r.stderr:
                results.append({"name": name, "verdict": "no_compile"})
                print(f"[{name}] skip (compile fail)")
                continue

        # parse functions + pick pair
        text = sol.read_text()
        contract_name = picked_map.get(name, {}).get("etherscan_name", name)
        body = extract_contract_body(text, contract_name)
        if body is None:
            # try other common names
            for alt in [name, name.capitalize()]:
                body = extract_contract_body(text, alt)
                if body:
                    contract_name = alt
                    break
        if body is None:
            results.append({"name": name, "contract": contract_name, "verdict": "contract_not_found"})
            print(f"[{name}] skip (contract body not found for {contract_name})")
            continue

        funcs = parse_public_funcs(body)
        pair = pick_pair(funcs)
        if pair is None:
            results.append({"name": name, "contract": contract_name, "verdict": "no_pair"})
            print(f"[{name}] skip (no public function pair)")
            continue

        print(f"[{name}] contract={contract_name} pair={pair[0]},{pair[1]}  running...")
        r = run_esbmc(name, contract_name, pair)
        r["contract"] = contract_name
        results.append(r)
        print(f"[{name}] -> {r['verdict']} elapsed={r['elapsed_s']}s pairs={r['pairs']}/clean={r['clean']}/found={r['tod_found']}/err={r['error']}")

    (ROOT / "run_summary.json").write_text(json.dumps(results, indent=2))
    print("\n=== DONE ===")
    ok = sum(1 for r in results if r.get("verdict") in ("summarised", "no_pairs"))
    found = sum(1 for r in results if r.get("tod_found", 0) > 0)
    print(f"Reached verdict: {ok}/{len(results)}")
    print(f"TOD found: {found}/{len(results)}")


if __name__ == "__main__":
    main()
