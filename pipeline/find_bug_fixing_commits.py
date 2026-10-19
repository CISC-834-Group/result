#!/usr/bin/env python3

# Step 4a of the JIT pipeline: decide which commits are real bug fixes, and save them as a table.

# Usage:
#     python find_fixes.py firefox.sqlite --start 2024-10-30
#     python find_fixes.py firefox.sqlite --start 2024-10-30 --end 2026-10-31

# A commit is a FIX when
#   * its first line names a bug (commits.bug_id),
#   * it is not a merge, a backout or an automated commit (the flags from add_flags.py),
#   * Bugzilla returned that bug (bugs.found = 1), and
#   * the bug has type 'defect' and resolution 'FIXED'.
# The commit's own message wording (fix_kw) plays no part.

# Writes (rebuilt from scratch on every run, so re-run it if the commits or bugs tables change):
#     fix_commits       the commits table filtered to the fixes: every column of commits (hash, author,
#                       dates, message, flags, ...) plus bug_created, product and component from Bugzilla.
#                       This is the input of run_szz.py.
#     commits.author_ts UTC epoch seconds of author_date (added if missing). author_date is stored in
#                       each author's own time zone, so it does not sort correctly as text.

# It also prints the filtering funnel (how many commits survive each test), for the report.

import argparse
import sqlite3
import sys
from datetime import datetime


def to_ts(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def ensure_author_ts(conn):
    have = {r[1] for r in conn.execute("PRAGMA table_info(commits)")}
    if "author_ts" not in have:
        conn.execute("ALTER TABLE commits ADD COLUMN author_ts INTEGER")
    rows = conn.execute("SELECT hash, author_date FROM commits WHERE author_ts IS NULL").fetchall()
    if rows:
        print(f"computing author_ts (UTC) for {len(rows)} commits...", file=sys.stderr)
        conn.executemany("UPDATE commits SET author_ts=? WHERE hash=?", [(to_ts(d), h) for h, d in rows])
        conn.commit()
    conn.execute("CREATE INDEX IF NOT EXISTS idx_commits_author_ts ON commits(author_ts)")
    conn.commit()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db")
    ap.add_argument("--start", required=True, help="first commit date to include, YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="last commit date to include, YYYY-MM-DD (default: no end)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    for t in ("commits", "bugs"):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone():
            sys.exit(f"table '{t}' not found. Run extract_commits.py, add_flags.py and the Bugzilla fetch first.")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(commits)")}
    if "is_automated" not in cols:
        sys.exit("commits.is_automated not found. Run add_flags.py first.")
    ensure_author_ts(conn)

    rng = "substr(c.author_date,1,10) >= ?"
    params = [args.start]
    if args.end:
        rng += " AND substr(c.author_date,1,10) <= ?"
        params.append(args.end)
    ok = "c.is_merge=0 AND c.is_backout=0 AND c.is_automated=0"

    conn.executescript("DROP TABLE IF EXISTS fix_commits;")
    conn.execute(f"""CREATE TABLE fix_commits AS
                     SELECT c.*, b.creation_time AS bug_created, b.product, b.component
                     FROM commits c JOIN bugs b ON b.id = c.bug_id
                     WHERE {rng} AND {ok} AND b.found=1 AND b.type='defect' AND b.resolution='FIXED'""", params)
    conn.execute("CREATE UNIQUE INDEX idx_fix_hash ON fix_commits(hash)")
    conn.commit()

    q = lambda sql, *a: conn.execute(sql, params + list(a)).fetchone()[0]
    print(f"\nFiltering funnel for commits dated {args.start} to {args.end or 'now'}")
    n_all = q(f"SELECT COUNT(*) FROM commits c WHERE {rng}")
    print(f"  commits in range:                                {n_all:>8}")
    for label, flag in (("merges", "c.is_merge=1"), ("backouts", "c.is_backout=1"), ("automated", "c.is_automated=1")):
        print(f"     flagged {label:<10} (excluded):                {q(f'SELECT COUNT(*) FROM commits c WHERE {rng} AND {flag}'):>8}")
    n_cand = q(f"SELECT COUNT(*) FROM commits c WHERE {rng} AND {ok}")
    print(f"  candidates (none of the three flags):            {n_cand:>8}")
    n_bug = q(f"SELECT COUNT(*) FROM commits c WHERE {rng} AND {ok} AND c.bug_id IS NOT NULL")
    print(f"  ... with a bug number on the first line:         {n_bug:>8}")
    print(f"     no bug number (excluded, cannot be confirmed): {n_cand - n_bug:>7}")
    kw = q(f"SELECT COUNT(*) FROM commits c WHERE {rng} AND {ok} AND c.bug_id IS NULL AND c.fix_kw=1")
    print(f"        of which the message has a fix keyword:    {kw:>8}   (possible missed fixes)")
    n_found = q(f"""SELECT COUNT(*) FROM commits c JOIN bugs b ON b.id=c.bug_id
                    WHERE {rng} AND {ok} AND b.found=1""")
    print(f"  ... whose bug Bugzilla returned:                 {n_found:>8}")
    print(f"     bug not found / private (excluded):           {n_bug - n_found:>8}")
    print("     by bug type:", conn.execute(f"""SELECT b.type, COUNT(*) FROM commits c JOIN bugs b ON b.id=c.bug_id
                    WHERE {rng} AND {ok} AND b.found=1 GROUP BY 1 ORDER BY 2 DESC""", params).fetchall())
    n_def = q(f"""SELECT COUNT(*) FROM commits c JOIN bugs b ON b.id=c.bug_id
                  WHERE {rng} AND {ok} AND b.found=1 AND b.type='defect'""")
    print(f"  ... of type 'defect':                            {n_def:>8}")
    print("     by resolution:", conn.execute(f"""SELECT b.resolution, COUNT(*) FROM commits c JOIN bugs b ON b.id=c.bug_id
                    WHERE {rng} AND {ok} AND b.found=1 AND b.type='defect' GROUP BY 1 ORDER BY 2 DESC""", params).fetchall())
    n_fix = conn.execute("SELECT COUNT(*) FROM fix_commits").fetchone()[0]
    print(f"  ... resolution FIXED = FIX COMMITS:              {n_fix:>8}")
    print("  distinct bugs among the fixes:                   ",
          conn.execute("SELECT COUNT(DISTINCT bug_id) FROM fix_commits").fetchone()[0])

    print("\nFix commits per month (author date, as stored):")
    per = conn.execute("SELECT substr(author_date,1,7), COUNT(*) FROM fix_commits GROUP BY 1 ORDER BY 1").fetchall()
    print("  " + "   ".join(f"{m}: {n}" for m, n in per))
    conn.close()


if __name__ == "__main__":
    main()