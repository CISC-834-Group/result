#!/usr/bin/env python3

# Step 5a of the JIT pipeline: from the SZZ results, keep the VALID bug-inducing commits for ONE window.
# Run it once per dataset, after run_szz.py has finished.

# Usage:
#     python find_bug_inducing_commits.py firefox.sqlite --start 2021-11-30 --end 2022-11-29 --name human
#     python find_bug_inducing_commits.py firefox.sqlite --start 2025-11-01 --end 2026-10-10 --name ai

# A commit is a VALID bug-inducing commit for the window when
#   * it is dated inside the window (--start to --end, both inclusive, UTC),
#   * it is not a merge, backout or automated commit (those never become rows), and
#   * at least one (fix, commit) pair from szz_pairs is accepted:
#       - the commit is not dated after the bug report (after_report = 0), and
#       - the fix is dated on or before the window end (no lag: same censoring for every window).
# Pairs whose fix came after the window end are ignored. A commit whose fixes ALL came later is not
# removed from the dataset: it is simply not in this table, so it gets bug = 0 when the rows are built.

# Writes (rebuilt on every run for that name):
#     bug_inducing_<name>   hash, author_date, author_ts, n_fixes, first_fix_gap_days
#                           n_fixes = accepted fixes that blame the commit
#                           first_fix_gap_days = days from the commit to the earliest accepted fix
#                           (a negative gap, from author dates out of order, is counted as 0)

# It prints the candidate funnel for the report, the bug rate per month (it drops toward the window end,
# because recent commits have had less time to be caught), and the gap percentiles as a diagnostic.
# Note: no AI filter is applied here; the rows (and the AI filter) are chosen when the dataset is built.

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timezone

DAY = 86400


def day_ts(s, end=False):
    d = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    return int(d.timestamp()) + (DAY if end else 0)


def pct(sorted_vals, p):
    return sorted_vals[min(len(sorted_vals) - 1, int(p / 100 * len(sorted_vals)))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db")
    ap.add_argument("--start", required=True, help="first day of the window, YYYY-MM-DD (inclusive)")
    ap.add_argument("--end", required=True, help="last day of the window, YYYY-MM-DD (inclusive); also the fix cutoff")
    ap.add_argument("--name", required=True, help="window name, e.g. human or ai; output table is bug_inducing_<name>")
    args = ap.parse_args()
    if not re.fullmatch(r"\w+", args.name):
        sys.exit("--name may only contain letters, digits and _")
    table = f"bug_inducing_{args.name}"

    conn = sqlite3.connect(args.db)
    for t in ("commits", "szz_fixes", "szz_pairs"):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone():
            sys.exit(f"table '{t}' not found. Run find_bug_fixing_commits.py and run_szz.py first.")

    w0, w1 = day_ts(args.start), day_ts(args.end, end=True)   # window is [w0, w1)
    q = lambda s, *a: conn.execute(s, a).fetchone()[0]

    # Is SZZ finished for the fixes of this window? fix_commits only holds the range it was last built
    # for, so check it covers this window before trusting the count.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='fix_commits'").fetchone():
        n_fix = q("SELECT COUNT(*) FROM fix_commits WHERE author_ts >= ? AND author_ts < ?", w0, w1)
        n_done = q("""SELECT COUNT(*) FROM fix_commits f JOIN szz_fixes s ON s.fix_hash = f.hash
                      WHERE f.author_ts >= ? AND f.author_ts < ? AND s.status != 'error'""", w0, w1)
        if n_fix == 0:
            print("Note: fix_commits does not cover this window (it was built for another range), "
                  "so SZZ progress cannot be checked here.\n")
        else:
            print(f"SZZ has traced {n_done} of the {n_fix} fixes dated in this window.")
            if n_done < n_fix:
                print("WARNING: SZZ is not finished for this window, so everything below is incomplete.\n")

    conn.executescript(f"DROP TABLE IF EXISTS {table};")
    conn.execute(f"""CREATE TABLE {table} AS
                     SELECT c.hash, c.author_date, c.author_ts,
                            COUNT(DISTINCT p.fix_hash) AS n_fixes,
                            MIN(MAX(p.fix_ts - p.inducing_ts, 0)) / 86400.0 AS first_fix_gap_days
                     FROM szz_pairs p JOIN commits c ON c.hash = p.inducing_hash
                     WHERE c.author_ts >= ? AND c.author_ts < ?
                       AND c.is_merge = 0 AND c.is_backout = 0 AND c.is_automated = 0
                       AND p.after_report = 0 AND p.fix_ts < ?
                     GROUP BY c.hash""", (w0, w1, w1))
    conn.execute(f"CREATE UNIQUE INDEX idx_{table}_hash ON {table}(hash)")
    conn.commit()

    # Funnel: every pair whose candidate commit is dated inside the window.
    inw = "FROM szz_pairs p JOIN commits c ON c.hash = p.inducing_hash WHERE c.author_ts >= ? AND c.author_ts < ?"
    clean = "c.is_merge = 0 AND c.is_backout = 0 AND c.is_automated = 0"
    pairs = q(f"SELECT COUNT(*) {inw}", w0, w1)
    rej_report = q(f"SELECT COUNT(*) {inw} AND p.after_report = 1", w0, w1)
    unknown = q(f"SELECT COUNT(*) {inw} AND p.after_report IS NULL", w0, w1)
    late = q(f"SELECT COUNT(*) {inw} AND p.after_report = 0 AND p.fix_ts >= ?", w0, w1, w1)
    cands = q(f"SELECT COUNT(DISTINCT p.inducing_hash) {inw}", w0, w1)
    flagged = q(f"SELECT COUNT(DISTINCT p.inducing_hash) {inw} AND NOT ({clean})", w0, w1)
    # Clean candidates with no accepted pair, split by why: only late fixes, or never accepted at all.
    only_late = q(f"""SELECT COUNT(*) FROM (SELECT p.inducing_hash {inw} AND {clean}
                      GROUP BY p.inducing_hash
                      HAVING SUM(p.after_report = 0 AND p.fix_ts < ?) = 0 AND SUM(p.after_report = 0) > 0)""",
                  w0, w1, w1)
    valid = q(f"SELECT COUNT(*) FROM {table}")
    neg = q(f"SELECT COUNT(*) {inw} AND p.after_report = 0 AND p.fix_ts < ? AND p.fix_ts < p.inducing_ts",
            w0, w1, w1)

    print(f"Window '{args.name}': {args.start} to {args.end} (fixes accepted up to {args.end})")
    print("Candidate funnel (candidates dated inside the window):")
    print(f"  (fix, candidate) pairs found by SZZ:                        {pairs:>8}")
    print(f"     rejected, candidate dated after the bug report:          {rej_report:>8}")
    print(f"     rejected, report date or candidate date unknown:         {unknown:>8}")
    print(f"     ignored, fix dated after the window end:                 {late:>8}")
    print(f"  distinct candidate commits:                                 {cands:>8}")
    print(f"     dropped, merge / backout / automated commits:            {flagged:>8}")
    print(f"     not buggy, every accepted fix came after the window end: {only_late:>8}")
    print(f"     not buggy, every pair rejected or unknown:               {cands - flagged - only_late - valid:>8}")
    print(f"  VALID bug-inducing commits ({table}): {valid:>8}")
    print(f"  accepted pairs with a negative gap (counted as 0 days):     {neg:>8}")

    # Bug rate per month among commits that can become rows (before any AI filter).
    print("\nBug rate per month (non-merge / backout / automated commits, author date in UTC):")
    rows = conn.execute(f"""SELECT strftime('%Y-%m', c.author_ts, 'unixepoch') AS m, COUNT(*),
                                   SUM(b.hash IS NOT NULL)
                            FROM commits c LEFT JOIN {table} b ON b.hash = c.hash
                            WHERE c.author_ts >= ? AND c.author_ts < ? AND {clean}
                            GROUP BY m ORDER BY m""", (w0, w1)).fetchall()
    tot_n = tot_b = 0
    for m, n, b in rows:
        tot_n, tot_b = tot_n + n, tot_b + b
        print(f"  {m}: {b:>6} / {n:>6}  ({100 * b / n:5.1f}%)")
    if tot_n:
        print(f"  total:   {tot_b:>6} / {tot_n:>6}  ({100 * tot_b / tot_n:5.1f}%)")

    gaps = sorted(r[0] for r in conn.execute(f"SELECT first_fix_gap_days FROM {table}"))
    if gaps:
        print(f"\nGap from a buggy commit to its earliest accepted fix ({len(gaps)} commits, diagnostic only):")
        print(f"  median {pct(gaps, 50):.0f} d, 80th pct {pct(gaps, 80):.0f} d, 90th pct {pct(gaps, 90):.0f} d, "
              f"95th pct {pct(gaps, 95):.0f} d, max {gaps[-1]:.0f} d")
    else:
        print("\nNo valid bug-inducing commits in this window (is SZZ finished?).")
    conn.close()


if __name__ == "__main__":
    main()
