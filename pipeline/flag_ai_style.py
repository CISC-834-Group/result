#!/usr/bin/env python3

# Flag commits whose MESSAGE is written in the style AI tools tend to produce (em dashes, arrows,
# markdown headers / bold, bullet lists, long bodies). Commit by commit: no propagation to authors.

# Usage:
#     python flag_ai_style.py firefox.sqlite --since 2025-11-01 --until 2026-10-10 --out ai_style_flags.csv
#     ... --baseline-since 2019-11-30 --baseline-until 2020-11-29     # also measure false positives
#     ... --threshold 3                                               # stricter

# Score = sum of the weights of the features present (each counts once):
#   2  em_dash     an em dash (U+2014)
#   2  arrow       a Unicode arrow (U+2192 or U+21D2)
#   1  md_header   a markdown header line ("# ", "## " ...); the Mozilla marker line
#                  "# ignore-this-changeset" (bulk reformat commits) is not counted
#   1  md_bold     **bold** text
#   1  bullets3    3 or more consecutive bullet or numbered list lines
#   1  long200     200 words or more
# flagged = 1 when score >= --threshold (default 2).

# Why these features and weights (measured on this database, non merge / backout / automated commits):
#   em dash and arrows are almost absent before ChatGPT (em dash 0.02-0.04%, arrow 0.00%) and rose
#   in 2025-26 (0.52%, 0.12%). Headers, bold, bullet lists and 200+ word bodies are 2-5x more common in
#   2025-26 but also written by people (Phabricator formatting), so they weigh less.
#   Left out on purpose: checkmarks (an Android UI-test template, "passed 100x on Firebase"), 500+ word
#   messages (same rate before and after AI) and "AI-sounding" words (no change).

# What the score means: a population-level signal, not proof. With the baseline options it prints the
# flag rate in a pre-AI period; (rate - baseline rate) / rate is an UPPER bound on precision.
# Rows match build_dataset.py: merges, backouts, automated commits and imported Android history are
# skipped, and windows use the author date in UTC. Join on sha = transactionid.

import argparse
import csv
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone

DAY = 86400

IGNORE_LINE = re.compile(r"^# ignore-this-changeset\s*$", re.IGNORECASE | re.MULTILINE)
FEATURES = [   # (name, weight, regex; None = length rule)
    ("em_dash", 2, re.compile("—")),
    ("arrow", 2, re.compile("[→⇒]")),
    ("md_header", 1, re.compile(r"^#{1,4} \S", re.MULTILINE)),
    ("md_bold", 1, re.compile(r"\*\*[^*\n]+\*\*")),
    ("bullets3", 1, re.compile(r"(^[ \t]*(?:[-*•]|\d+\.) \S.*\n?){3,}", re.MULTILINE)),
    ("long200", 1, None),
]


def day_ts(s, end=False):
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()) + (DAY if end else 0)


def score(message):
    msg = IGNORE_LINE.sub("", message or "")
    found = [(name, w) for name, w, rx in FEATURES
             if (len(msg.split()) >= 200 if rx is None else rx.search(msg))]
    return sum(w for _, w in found), [name for name, _ in found]


def scan(conn, since, until):
    return conn.execute(
        """SELECT hash, author_email, author_ts, message FROM commits
           WHERE author_ts >= ? AND author_ts < ?
             AND is_merge = 0 AND is_backout = 0 AND is_automated = 0
             AND message NOT LIKE '[fenix]%' AND message NOT LIKE '[focus]%'
             AND message NOT LIKE '[components]%'
           ORDER BY author_ts""", (day_ts(since), day_ts(until, end=True))).fetchall()


def summarize(label, scored, threshold):
    n = len(scored)
    flagged = sum(s >= threshold for s, _ in scored)
    print(f"\n{label}: {n} commits, flagged {flagged} ({100 * flagged / max(n, 1):.2f}%)")
    feats = Counter(f for _, fs in scored for f in fs)
    print("  feature rates: " + "  ".join(f"{name} {100 * feats[name] / max(n, 1):.2f}%" for name, _, _ in FEATURES))
    by_score = Counter(min(s, 5) for s, _ in scored)
    print("  commits by score: " + "  ".join(f"{s}{'+' if s == 5 else ''}: {by_score[s]}" for s in range(6)))
    return 100 * flagged / max(n, 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db")
    ap.add_argument("--since", required=True, help="first day, YYYY-MM-DD (inclusive)")
    ap.add_argument("--until", required=True, help="last day, YYYY-MM-DD (inclusive)")
    ap.add_argument("--threshold", type=int, default=2, help="minimum score to flag (default 2)")
    ap.add_argument("--out", default="ai_style_flags.csv")
    ap.add_argument("--baseline-since", help="first day of a pre-AI period to measure false positives")
    ap.add_argument("--baseline-until", help="last day of that period")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    rows = scan(conn, args.since, args.until)
    scored = []
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sha", "author", "date", "style_score", "features", "flagged"])
        for h, email, ts, msg in rows:
            s, fs = score(msg)
            scored.append((s, fs))
            w.writerow([h, (email or "").lower(),
                        datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        s, ";".join(fs), int(s >= args.threshold)])
    print(f"wrote {args.out}  (threshold: score >= {args.threshold})")
    rate = summarize(f"{args.since} to {args.until}", scored, args.threshold)

    by_month = {}
    for (h, _, ts, _), (s, _) in zip(rows, scored):
        m = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m")
        n, k = by_month.get(m, (0, 0))
        by_month[m] = (n + 1, k + (s >= args.threshold))
    print("  flagged per month: " + "  ".join(f"{m}: {k}/{n}" for m, (n, k) in sorted(by_month.items())))

    if args.baseline_since and args.baseline_until:
        base_scored = [score(r[3]) for r in scan(conn, args.baseline_since, args.baseline_until)]
        base = summarize(f"Baseline {args.baseline_since} to {args.baseline_until} (every flag here is a false positive)",
                         base_scored, args.threshold)
        if rate > 0:
            print(f"\nUpper bound on precision: ({rate:.2f}% - {base:.2f}%) / {rate:.2f}% = {100 * (rate - base) / rate:.0f}%")
    elif args.baseline_since or args.baseline_until:
        sys.exit("give both --baseline-since and --baseline-until")
    conn.close()


if __name__ == "__main__":
    main()
