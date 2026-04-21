#!/usr/bin/env python3
"""Re-run the 33 filtered TransRacer contracts with the patched ESBMC
binary using --tod-race-check=auto.  Previously pick_and_run.py picked
a single pair per contract; with auto mode enumerating every candidate
we now get a full verdict distribution.

Per-contract budget:
  wall: 240 s
  CPU:  220 s
  RSS:  4 GB

Writes:
  results/<name>/auto.stdout
  results/<name>/auto.stderr
  results/<name>/auto.json  — parsed summary
  auto_summary.json         — aggregate
"""
from __future__ import annotations
import json, re, subprocess, sys, time
from pathlib import Path

ROOT = Path("/home/samson/workspace/esbmc/Dataset/transracer_50")
SOURCES = ROOT / "sources"
RESULTS = ROOT / "results"
ESBMC = "/home/samson/workspace/esbmc/build/src/esbmc/esbmc"
WALL = 240
CPU = 220
VMEM_KB = 4000000

RE_CONTRACT = re.compile(r'--contract\s+"?([^"\s]+)"?')
RE_DISCOVERED = re.compile(
    r"^--tod-race-check: discovered (\d+) candidate pair\(s\)", re.MULTILINE
)
RE_VERDICT_LINE = re.compile(
    r"^\s+->\s+(\S+)\s+(\S+)(?:\s+(.*))?$", re.MULTILINE
)
RE_SUMMARY = re.compile(
    r"^--tod-race-check summary: (\d+) pair\(s\) — (\d+) clean, "
    r"(\d+) TOD found, (\d+) error",
    re.MULTILINE,
)


def extract_target(name: str) -> str | None:
    # Preferred source: old cmd file from pick_and_run.py
    cmd_file = RESULTS / name / "cmd"
    if cmd_file.exists():
        m = RE_CONTRACT.search(cmd_file.read_text())
        if m:
            return m.group(1)
    # Fallback 1: stderr header e.g. "discovered N candidate pair(s) in 'X'"
    stderr = RESULTS / name / "run.stderr"
    if stderr.exists():
        m = re.search(r"in '([A-Za-z_][\w]*)'", stderr.read_text())
        if m:
            return m.group(1)
    # Fallback 2: stdout
    stdout = RESULTS / name / "run.stdout"
    if stdout.exists():
        m = re.search(r"in '([A-Za-z_][\w]*)'", stdout.read_text())
        if m:
            return m.group(1)
    return None


def run_one(name: str) -> dict:
    target = extract_target(name)
    if not target:
        return {"case": name, "error": "no_target"}
    sol_dir = SOURCES / name
    out_dir = RESULTS / name
    out_dir.mkdir(exist_ok=True)

    cmd = (
        f"timeout {WALL} bash -c '"
        f"ulimit -v {VMEM_KB}; ulimit -t {CPU}; "
        f"exec \"{ESBMC}\" contract.sol "
        f"--contract \"{target}\" "
        f"--tod-race-check=auto --tod-jobs=2 "
        f"--bound --unwind 1 --no-unwinding-assertions "
        f"--no-standard-checks --cvc5"
        f"'"
    )
    (out_dir / "auto.cmd").write_text(cmd + "\n")
    t0 = time.monotonic()
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=sol_dir,
            capture_output=True, text=True, timeout=WALL + 20,
        )
        stdout = r.stdout
        stderr = r.stderr
        rc = r.returncode
    except subprocess.TimeoutExpired as ex:
        stdout = (ex.stdout.decode() if ex.stdout else "")
        stderr = (ex.stderr.decode() if ex.stderr else "")
        rc = -1
    elapsed = time.monotonic() - t0
    (out_dir / "auto.stdout").write_text(stdout)
    (out_dir / "auto.stderr").write_text(stderr)

    combined = stdout + "\n===STDERR===\n" + stderr
    disc = RE_DISCOVERED.search(combined)
    summary = RE_SUMMARY.search(combined)
    verdicts = [
        [m.group(1), m.group(2), (m.group(3) or "").strip()]
        for m in RE_VERDICT_LINE.finditer(combined)
    ]

    no_pairs = (
        disc is not None
        and int(disc.group(1)) == 0
        and "no race TOD pairs detected" in combined
    )
    status = (
        "DONE" if summary else (
            "NO_PAIRS" if no_pairs else
            "CRASH" if "Aborted" in stderr or "core dumped" in stderr else
            "OOM" if "Out of memory" in stderr or "Killed" in stderr else
            "PARSE_ERROR" if "PARSING ERROR" in combined else
            "TIMEOUT" if elapsed >= WALL - 5 else
            "ERROR"
        )
    )
    out = {
        "case": name,
        "target": target,
        "discovered": int(disc.group(1)) if disc else 0,
        "verdicts": verdicts,
        "summary": (
            {
                "total": int(summary.group(1)),
                "clean": int(summary.group(2)),
                "tod_found": int(summary.group(3)),
                "error": int(summary.group(4)),
            }
            if summary
            else None
        ),
        "status": status,
        "wall_sec": round(elapsed, 1),
        "returncode": rc,
    }
    (out_dir / "auto.json").write_text(json.dumps(out, indent=2))
    return out


def main() -> int:
    selected = json.loads((ROOT / "selected.json").read_text())
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    if only:
        selected = [c for c in selected if c in only]

    report = []
    for i, name in enumerate(selected):
        print(f"[{i + 1}/{len(selected)}] {name} ...", flush=True)
        r = run_one(name)
        s = r.get("summary") or {}
        print(
            "  status={st}  disc={d}  tod={t}  clean={c}  err={e}  sec={s}".format(
                st=r.get("status"),
                d=r.get("discovered"),
                t=s.get("tod_found"),
                c=s.get("clean"),
                e=s.get("error"),
                s=r.get("wall_sec"),
            ),
            flush=True,
        )
        report.append(r)
    (ROOT / "auto_summary.json").write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
