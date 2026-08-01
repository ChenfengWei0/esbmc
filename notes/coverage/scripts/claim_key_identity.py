#!/usr/bin/env python3
"""Do the repeated solves of ONE path claim share a byte-identical key?

This is the falsifier for the proposed one-line repair of the duplicate-solve
defect. The repair is: `reached_claims` is INSERTED with
`claim_sig = comment + "\\t" + loc` and LOOKED UP with
`claim_cstr = comment + " at " + loc`, so the skip can never fire under goto
coverage; correcting the lookup would make it fire.

That repair only helps if the repeated solves really do share the key. ESBMC
prints `Solving claim '<comment> at <loc>'`, which is exactly `claim_cstr`. So:

  * every occurrence of one path's line is byte-identical
        -> the copies share `claim_loc`, hence share `claim_sig`, and the
           corrected lookup would suppress them. The premise holds.
  * the occurrences differ in their `at file … line … column …` suffix
        -> `claim_loc` differs per copy, `claim_sig` differs, and the corrected
           lookup would fire ZERO times while costing a string build per job.
           **The whole premise collapses** and the fix belongs somewhere else.

The second outcome is the one that is not a win, and it is why this runs before
anything is changed rather than after.

Usage: python3 claim_key_identity.py <esbmc-run.log> [more.log ...]
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

PREFIX = "Solving claim '"


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    total = 0
    per_comment = defaultdict(Counter)   # comment -> full-string -> count
    for p in argv[1:]:
        f = Path(p)
        if not f.exists():
            print(f"MISSING {p}")
            continue
        for line in f.read_text(errors="replace").splitlines():
            s = line.strip()
            if not s.startswith(PREFIX):
                continue
            total += 1
            # `Solving claim '<cstr>' with solver X` -- take everything between
            # the first quote and the LAST quote on the line, so a comment
            # containing a quote cannot truncate it.
            body = s[len(PREFIX):]
            end = body.rfind("'")
            cstr = body[:end] if end != -1 else body
            comment = cstr.split(" at ", 1)[0]
            per_comment[comment][cstr] += 1

    print("## Are the repeated solves of one claim byte-identical?\n")
    print(f"  `Solving claim` lines read : {total}")
    print(f"  distinct claim COMMENTS    : {len(per_comment)}\n")

    split = []
    for comment in sorted(per_comment):
        forms = per_comment[comment]
        n = sum(forms.values())
        print(f"  {comment}   solved {n} time(s), {len(forms)} distinct full "
              f"key(s)")
        if len(forms) > 1:
            split.append(comment)
            for form, c in sorted(forms.items(), key=lambda kv: -kv[1]):
                print(f"        x{c:<4} {form}")
    print()
    if not per_comment:
        print("  VERDICT: no `Solving claim` lines at all -- this log does not "
              "answer the question. Contributes nothing, not zero.")
        return 1
    if split:
        print("  ⛔ VERDICT: at least one comment appears with MORE THAN ONE "
              "full key, so the\n     repeated solves do NOT share "
              "`claim_loc`. `claim_sig` differs per copy, the\n     corrected "
              "lookup would fire zero times, and the proposed one-line repair "
              "is\n     wrong. Comments affected: " + ", ".join(split))
        return 1
    print("  ✅ VERDICT: every comment has exactly ONE full key across all its "
          "solves, so all\n     copies share `comment` AND `claim_loc`. "
          "`claim_sig` is identical per path and a\n     lookup on it would "
          "suppress every repeat. The premise of the repair holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
