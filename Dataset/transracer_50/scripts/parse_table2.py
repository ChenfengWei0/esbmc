#!/usr/bin/env python3
"""Parse Table 2 from TransRacer.pdf → paper_table2.json + selected.json (TRBD>=1)."""
from __future__ import annotations
import json, re, sys
from pathlib import Path

import pypdf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PDF = Path("/home/samson/workspace/esbmc/TransRacer.pdf")


def extract_table2_lines() -> list[str]:
    r = pypdf.PdfReader(str(PDF))
    # Table 2 spans pages 8 (starts at "Contract Name #Func...") and continues to "Total" row.
    # From the earlier probe: page 8 (0-indexed 7) is the one containing Table 2.
    text = r.pages[7].extract_text() or ""
    lines = text.splitlines()
    # Rows look like:
    # "XCTCrowdSale 5 472 0 0 0 1 0 0 0 1 0 0.6 6/6=100.0%"
    # 11 ints + float time + coverage rate
    return lines


NUMERIC = re.compile(r"^(?P<name>[A-Za-z][\w\-]*) (?P<nums>(?:\d[\d,]* ){11})(?P<time>\d+(?:\.\d+)?) (?P<cov>.+)$")


def parse_row(line: str) -> dict | None:
    m = NUMERIC.match(line)
    if not m:
        return None
    name = m.group("name")
    nums = [int(x.replace(",", "")) for x in m.group("nums").split()]
    if len(nums) != 11:
        return None
    time_min = float(m.group("time"))
    cov = m.group("cov").split()[0]  # drop trailing columns we don't need here
    (n_func, n_inst, n_dep,
     trI_is, trI_us, trD_is, trD_us,
     trbI_is, trbI_us, trbD_is, trbD_us) = nums
    return dict(
        name=name,
        n_func=n_func, n_inst=n_inst, n_dep=n_dep,
        trI_is=trI_is, trI_us=trI_us,
        trD_is=trD_is, trD_us=trD_us,
        trbI_is=trbI_is, trbI_us=trbI_us,
        trbD_is=trbD_is, trbD_us=trbD_us,
        time_min=time_min, coverage=cov,
    )


def main():
    lines = extract_table2_lines()
    rows = []
    for line in lines:
        row = parse_row(line.strip())
        if row:
            rows.append(row)
    # Strip Total / Average rows (they also parse by regex but have 'Total' / 'Average' as name)
    rows = [r for r in rows if r["name"] not in ("Total", "Average")]
    out = ROOT / "paper_table2.json"
    out.write_text(json.dumps(rows, indent=2))
    print(f"Wrote {len(rows)} rows to {out}")

    selected = [r for r in rows if r["trbD_is"] + r["trbD_us"] >= 1]
    sel_path = ROOT / "selected.json"
    sel_path.write_text(json.dumps([r["name"] for r in selected], indent=2))
    print(f"Wrote {len(selected)} selected names to {sel_path}")
    print("names:", ", ".join(r["name"] for r in selected))


if __name__ == "__main__":
    main()
