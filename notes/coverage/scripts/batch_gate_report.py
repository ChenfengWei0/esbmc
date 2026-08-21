#!/usr/bin/env python3
"""Report TODO-0 gates for ONE batch of subjects, and refuse to average them away.

A batch is run so that a low conversion rate is INVESTIGATED, not discovered at
the end of a 509-subject sweep. So this prints per case first and the aggregate
second, and it names the criterion and the case when one fails.
"""
import json, os, sys, glob, argparse, collections, re


def load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def cases(root):
    """One row per (subject, unit) that Stage 4 was asked about."""
    for summary_path in sorted(glob.glob(os.path.join(root, "**", "put-summary.json"),
                                         recursive=True)):
        d = load(summary_path)
        if not d:
            continue
        q = (d.get("deliverable_b") or {}).get("quality") or d.get("quality") or {}
        s2 = d.get("stage2") or {}
        em = d.get("emission") or {}
        rows = (d.get("deliverable_b") or {}).get("rows") or []
        # A NON-IMPLIED R1/R2 is the S3 gate. `r1r2_rows` counts a row whose
        # oracle carries any R1/R2 class, including a revert-frame `post == pre`
        # -- item 3 -- so it cannot answer S3 on its own.
        # ONLY ROWS THAT PRODUCED A PUT. `deliverable_b.rows` also carries the
        # REFUSED rows, and counting those made the report print
        # "18 solver-certified of 13 candidate rows" -- a number that cannot be
        # read as anything and therefore cannot be checked.
        non_implied = 0
        certify_sourced = 0
        rows = [r for r in rows if r.get("assertion_backed_parameterized")
                or r.get("assertion_backed")]
        for r in rows:
            for o in r.get("assertion_oracles") or []:
                if not o.get("emitted_in_test"):
                    continue
                if o.get("layer") == "revert-frame":
                    continue
                if any(c in ("R1", "R2") for c in (o.get("classes") or [])):
                    non_implied += 1
                    break
            src = r.get("certification_source") or r.get("stage2_source") or ""
            if "structural" not in str(src):
                certify_sourced += 1
        # THE CASE IS A SUBJECT, not a (subject, unit, path-family) summary.
        # TODO 0's S1 says "produced at least one valid test unit" -- per case.
        # Keyed per summary, a subject with three path families and one PUT
        # reads as two S1 failures and one pass, which stops a batch that TODO 0
        # would pass. MEASURED on batch5-20260821-b1: 7 "cases" from 5 subjects,
        # 3 spurious failures.
        parts = summary_path.split(os.sep)
        subject = (parts[parts.index("subjects") + 1]
                   if "subjects" in parts else os.path.basename(os.path.dirname(summary_path)))
        yield {
            "summary": summary_path,
            "subject": subject,
            "valid": int(q.get("valid_reference_rows") or 0),
            "put": int(q.get("put_rows") or 0),
            "concrete": int(q.get("concrete_rows") or 0),
            "put_rate": q.get("put_rate_among_valid"),
            "r1r2_rows": int(q.get("r1r2_rows") or 0),
            "non_implied_r1r2": non_implied,
            "certify_sourced": certify_sourced,
            # The denominator for the two counters above: the rows this report
            # actually inspected. `emission.stage4_candidate_rows` is NOT it --
            # it counts what Stage 4 was asked about, refusals included.
            "oracle_rows": len(rows),
            "b": int((d.get("deliverable_b") or {}).get("b") or 0),
            "refused": int((d.get("deliverable_b") or {}).get("refused") or 0),
            "certified": int(s2.get("certified") or 0),
            "witnessed": int(s2.get("witnessed") or 0),
            "puts_emitted": int(em.get("puts_emitted") or 0),
            "candidate_rows": int(em.get("stage4_candidate_rows") or 0),
        }


def round_gates(root):
    """TODO 0's PER-ROUND gates R1-R4 -- the ones that protect the ablations.

    A batch that passes every per-case gate can still be worthless to RQ3: if
    refinement never fires, `no-region-refinement` removes a stage that did
    nothing, and the arm measures the absence of an absence. Measured on
    Full-509: refinement fired on 88 of ~3700 paths and RESCUED 2 regions in the
    whole corpus. So these are reported next to the per-case table, not after
    it.
    """
    refine_rounds = collections.Counter()
    rescued = []
    seq_rescued = []
    requeried = []
    stopped = 0
    r1r2_present = r0_only = 0
    for f in glob.glob(os.path.join(root, "**", "certify-results.jsonl"), recursive=True):
        for line in open(f):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            # R4: a case stopped by budget is a measurement of the budget.
            blob = json.dumps(d)
            stopped += blob.count("Stage-2 driver stopped")
            # R1/R2: read the certify-query SEQUENCE per path. More than one
            # query on a path means the first box was refuted and refinement
            # proposed another; a SUCCESSFUL last one after a refuted first is
            # a region refinement RESCUED, which is what R2 asks for.
            # R2 is read off the region record's own `refinement_used`: the
            # driver sets it when the FIRST candidate was REFUTED and a later
            # (shrunk / punctured / retreated) candidate certified. That is
            # Algorithm 1's Refine step changing the outcome, which is what the
            # no-region-refinement arm removes.
            subject = os.path.basename(os.path.dirname(os.path.dirname(f)))
            for enc, det in (d.get("certified_details") or {}).items():
                if isinstance(det, dict) and det.get("refinement_used") is True:
                    rescued.append((subject, enc, "refinement_used"))
            # The certify-query verdict SEQUENCE is kept as a cross-check, and
            # it is read strictly: only a FAILED (refuted) query before the
            # final SUCCESSFUL one counts. An UNKNOWN before it is NOT a
            # refutation -- MEASURED on batch b2, every such sequence was the
            # tool REFUSING a coordinate it cannot express, the driver dropping
            # it and re-querying; counting that as "rescued" reported R2 = 3 on
            # a round where refinement changed nothing.
            per_path = collections.defaultdict(list)
            for h in (d.get("generalise_progress") or {}).get("history") or []:
                if h.get("stage") == "certify-query-finished":
                    per_path[h.get("enc")].append(h.get("verdict"))
            for enc, verdicts in per_path.items():
                if len(verdicts) > 1:
                    refine_rounds[str(f)] += len(verdicts) - 1
                    if verdicts[-1] == "SUCCESSFUL" and "FAILED" in verdicts[:-1]:
                        seq_rescued.append((subject, enc, verdicts))
                    elif verdicts[-1] == "SUCCESSFUL":
                        requeried.append((subject, enc, verdicts))
    # Round-level counts of the driver's own round lines.
    rounds = collections.Counter()
    for f in glob.glob(os.path.join(root, "**", "driver.log"), recursive=True):
        try:
            text = open(f, errors="replace").read()
        except OSError:
            continue
        for m in re.finditer(r"\[round\] (level-0|geometric-bracket|linear-refine)", text):
            rounds[m.group(1)] += 1
    return rounds, sum(refine_rounds.values()), rescued, stopped, seq_rescued, requeried


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--min-put-ratio", type=float, default=2.0,
                    help="S2: put_valid >= N x concrete_valid (TODO 0 proposal)")
    args = ap.parse_args()
    per_summary = list(cases(args.root))
    merged = {}
    for r in per_summary:
        m = merged.setdefault(r["subject"], {k: 0 for k in
                                             ("valid", "put", "concrete", "b", "refused",
                                              "certified", "witnessed", "puts_emitted",
                                              "candidate_rows", "non_implied_r1r2",
                                              "certify_sourced", "oracle_rows")})
        m["subject"] = r["subject"]
        m["summary"] = r["summary"]
        for k in list(m):
            if k in ("subject", "summary"):
                continue
            m[k] += r[k]
    rows = [merged[k] for k in sorted(merged)]
    if not rows:
        print(f"NO put-summary.json under {args.root} -- nothing was measured.")
        return 2

    print(f"{'subject':<46}{'valid':>6}{'PUT':>5}{'conc':>5}{'B':>3}"
          f"{'cert':>6}{'!impl':>7}{'solver':>7}  gates")
    failures = []
    for r in rows:
        bad = []
        if r["valid"] < 1:
            bad.append("S1 valid=0")
        if r["put"] < args.min_put_ratio * max(r["concrete"], 0) and r["concrete"] > 0:
            bad.append(f"S2 {r['put']}:{r['concrete']}")
        if r["non_implied_r1r2"] < 1:
            bad.append("S3 no non-implied R1/R2")
        if r["certify_sourced"] < 1:
            bad.append("S4 no solver-certified region")
        print(f"{r['subject'][:45]:<46}{r['valid']:>6}{r['put']:>5}{r['concrete']:>5}"
              f"{r['b']:>3}{r['certified']:>6}{r['non_implied_r1r2']:>7}"
              f"{r['certify_sourced']:>7}  {'OK' if not bad else '; '.join(bad)}")
        if bad:
            failures.append((r["subject"], bad, r["summary"]))

    tot_valid = sum(r["valid"] for r in rows)
    tot_put = sum(r["put"] for r in rows)
    tot_conc = sum(r["concrete"] for r in rows)
    tot_b = sum(r["b"] for r in rows)
    tot_cand = sum(r["candidate_rows"] for r in rows)
    tot_ref = sum(r["refused"] for r in rows)
    print()
    print(f"cases                : {len(rows)}")
    print(f"valid reference rows : {tot_valid}")
    print(f"PUT : concrete       : {tot_put} : {tot_conc}"
          + (f"  ({tot_put / tot_conc:.2f}x)" if tot_conc else "  (no concrete)"))
    print(f"CONVERSION RATE      : B / stage-4 candidate rows = "
          f"{tot_b} / {tot_cand}"
          + (f" = {tot_b / tot_cand:.3f}" if tot_cand else ""))
    print(f"refused rows         : {tot_ref}")
    tot_oracle = sum(r["oracle_rows"] for r in rows)
    print(f"oracle-bearing rows  : {tot_oracle}")
    print(f"  non-implied R1/R2  : {sum(r['non_implied_r1r2'] for r in rows)} of {tot_oracle}")
    print(f"  solver-certified   : {sum(r['certify_sourced'] for r in rows)} of {tot_oracle}"
          "   <- TODO 0 S4; Full-509 had 19.9% here")
    rounds, extra_queries, rescued, stopped, seq_rescued, requeried = round_gates(args.root)
    print()
    print("PER-ROUND gates (TODO 0 R1-R4) -- these protect the RQ3 ablations")
    print(f"  R1 refinement rounds : "
          + (", ".join(f"{k}={v}" for k, v in sorted(rounds.items())) or "NONE")
          + ("" if rounds.get("linear-refine") else "   <- FAILS R1"))
    print(f"  R2 regions rescued   : {len(rescued)}"
          + ("" if rescued else "   <- FAILS R2: refinement fired but changed no outcome"))
    print(f"     certify-query sequences FAILED -> ... -> SUCCESSFUL : {len(seq_rescued)}")
    print(f"     re-queried after a REFUSED coordinate (not a rescue) : {len(requeried)}")
    for subject, enc, verdicts in rescued[:6]:
        print(f"       {subject} enc={enc}: {' -> '.join(str(v) for v in verdicts)}")
    print(f"  R3 R1/R2 vs R0-only  : "
          f"{'both present' if any(r['non_implied_r1r2'] for r in rows) and any(not r['non_implied_r1r2'] for r in rows) else 'ONE-SIDED  <- FAILS R3'}")
    print(f"  R4 stopped by budget : {stopped}"
          + ("" if stopped == 0 else "   <- FAILS R4: these numbers measure the budget, not the method"))
    if failures:
        print()
        print("STOP -- these cases fail TODO 0. Investigate before the next batch;")
        print("do NOT widen a budget to make a criterion pass.")
        for subject, bad, path in failures:
            print(f"  {subject}: {'; '.join(bad)}")
            print(f"    {path}")
        return 1
    print()
    print("all per-case gates OK for this batch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
