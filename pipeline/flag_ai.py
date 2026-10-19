#!/usr/bin/env python3

# Run both AI flaggers and combine them into one CSV.
#   1. flag_ai_commits.py  content: AI trailers / tool names, propagated to the author's later commits
#   2. flag_ai_style.py    style: em dashes, arrows, markdown, long bodies; commit by commit only
# Each script's own output and summary is kept. The combined file is the union: a commit is AI-flagged
# when EITHER script flags it. Style flags are never propagated to authors.

# Usage:
#     python flag_ai.py firefox.sqlite --since 2025-11-01 --until 2026-10-10
#     ... --baseline-since 2019-11-30 --baseline-until 2020-11-29     # false-positive check for the style flags
#     ... --threshold 3                                               # stricter style flags
#
# Writes ai_flags.csv, ai_style_flags.csv and ai_flags_combined.csv (one row per commit that can be a
# dataset row; join on sha = transactionid):
#     sha, content_tier, style_score, style_features, source, ai_flag
#     source  = content | style | both | none
#     ai_flag = 1 unless source is none

import argparse
import csv
import os
import subprocess
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))


def run(args):
    print(f"\n$ {' '.join(args)}", flush=True)
    r = subprocess.run([sys.executable] + args, cwd=HERE)
    if r.returncode != 0:
        sys.exit(f"{args[0]} failed (exit code {r.returncode})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db")
    ap.add_argument("--since", required=True, help="first day, YYYY-MM-DD (inclusive)")
    ap.add_argument("--until", required=True, help="last day, YYYY-MM-DD (inclusive)")
    ap.add_argument("--threshold", type=int, default=2, help="minimum style score to flag (default 2)")
    ap.add_argument("--baseline-since", help="first day of a pre-AI period (style false-positive check)")
    ap.add_argument("--baseline-until", help="last day of that period")
    ap.add_argument("--content-out", default="ai_flags.csv")
    ap.add_argument("--style-out", default="ai_style_flags.csv")
    ap.add_argument("--out", default="ai_flags_combined.csv")
    args = ap.parse_args()
    db = os.path.abspath(args.db)
    paths = {k: os.path.abspath(getattr(args, k)) for k in ("content_out", "style_out", "out")}

    # flag_ai_commits.py gets no --until: it compares ISO dates as text, so its --until drops the last
    # day. The window end is applied below, through the style file's rows (author date, UTC).
    run(["flag_ai_commits.py", "--db", db, "--since", args.since, "--out", paths["content_out"]])

    style = ["flag_ai_style.py", db, "--since", args.since, "--until", args.until,
             "--threshold", str(args.threshold), "--out", paths["style_out"]]
    if args.baseline_since and args.baseline_until:
        style += ["--baseline-since", args.baseline_since, "--baseline-until", args.baseline_until]
    run(style)

    with open(paths["content_out"], newline="", encoding="utf-8") as f:
        tier = {r["sha"]: r["tier"] for r in csv.DictReader(f)}

    # The style file lists exactly the commits that can be dataset rows (no merges, backouts, automated
    # commits or imported Android history, inside the window), so it defines the rows here.
    counts = Counter()
    with open(paths["style_out"], newline="", encoding="utf-8") as f, \
         open(paths["out"], "w", newline="", encoding="utf-8") as g:
        w = csv.writer(g)
        w.writerow(["sha", "content_tier", "style_score", "style_features", "source", "ai_flag"])
        for r in csv.DictReader(f):
            t = tier.get(r["sha"], "none")
            c, s = t != "none", r["flagged"] == "1"
            source = "both" if c and s else "content" if c else "style" if s else "none"
            counts[source] += 1
            w.writerow([r["sha"], t, r["style_score"], r["features"], source, int(source != "none")])

    n = sum(counts.values())
    flagged = n - counts["none"]
    print(f"\nwrote {args.out}: {n} commits, AI-flagged {flagged} ({100 * flagged / max(n, 1):.1f}%)")
    for k in ("content", "style", "both", "none"):
        print(f"  {k:<8} {counts[k]:>7}")


if __name__ == "__main__":
    main()
