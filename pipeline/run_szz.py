#!/usr/bin/env python3

# Step 4b of the JIT pipeline: SZZ. For every fix commit, find the commits that last touched the lines
# the fix removed or changed. Those are the candidate bug-introducing commits.

# Run find_fixes.py first: it decides which commits are fixes and saves them in the table fix_commits.

# Usage:
#     python run_szz.py firefox.sqlite C:\path\to\firefox --limit 200            # timing test
#     python run_szz.py firefox.sqlite C:\path\to\firefox --workers 4            # full run

# No lag cutoff is applied here: it is chosen later from the gaps this script records.

# How each fix is traced (SZZ steps 3.1 and 3.2):
#   1. git diff -U0 parent..fix  ->  the exact old lines the fix deleted or changed, per file.
#      Blank lines are skipped. A fix that only adds lines has nothing to trace (status no_deleted_lines).
#   2. git blame -w (ignore whitespace) on those lines in the fix's parent. Each line points to the
#      commit that last changed it. If the repo has .git-blame-ignore-revs it is used (bulk reformats).
#   3. Assignment rule: a candidate whose author_date is later than the bug's creation_time is flagged
#      after_report = 1 (kept in the table, so the report can count it, but not to be used as a label).

# Writes (re-runnable; finished fixes are skipped, failed ones are retried):
#     szz_fixes            one row per traced fix commit: status, files, lines, seconds
#     szz_pairs            one row per (fix, candidate): both timestamps, after_report flag

# Statuses: ok | no_deleted_lines | too_many_files | no_parent | error
# Choices to document: -w ignores whitespace-only changes; blank deleted lines skipped; comment-only
# lines are NOT skipped; fixes touching more than --max-files files are skipped (bulk changes).

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from multiprocessing import Pool

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
BLAME_RE = re.compile(r"^([0-9a-f]{40}) \d+ \d+")

REPO = None
IGNORE_REVS = None
CHUNK = 300      # -L ranges per git blame call


def to_ts(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def git(args):
    try:
        r = subprocess.run(["git", "-C", REPO, "-c", "core.quotepath=false"] + args,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:          # e.g. command line too long on Windows
        return -1, "", str(e)
    return r.returncode, r.stdout.decode("utf-8", errors="replace"), r.stderr.decode("utf-8", errors="replace")


def deleted_lines(diff_text):
    """{old file path: sorted list of old line numbers that the diff deleted or changed (non-blank)}."""
    files = {}
    path = None
    in_header = False
    cur = None
    for line in diff_text.split("\n"):
        if line.startswith("diff --git "):
            in_header, path, cur = True, None, None
            continue
        if in_header:
            if line.startswith("--- "):
                p = line[4:].rstrip("\t")
                path = None if p == "/dev/null" else (p[2:] if p.startswith("a/") else p)
            elif line.startswith("@@"):
                in_header = False
            else:
                continue
        if line.startswith("@@"):
            m = HUNK_RE.match(line)
            cur = int(m.group(1)) if m else None
            continue
        if cur is None or path is None:
            continue
        if line.startswith("-"):
            if line[1:].strip():
                files.setdefault(path, []).append(cur)
            cur += 1
    return {p: sorted(set(n)) for p, n in files.items()}


def ranges(nums):
    out, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n != prev + 1:
            out.append((start, prev))
            start = n
        prev = n
    out.append((start, prev))
    return out


def trace_fix(task):
    """Worker: returns (fix_hash, status, n_files, n_lines, n_fail, secs, {inducing_hash: lines})."""
    fix, parent, max_files = task
    t0 = time.time()
    if not parent:
        return fix, "no_parent", 0, 0, 0, 0.0, {}
    code, diff, err = git(["diff", "-U0", "--no-renames", "--no-color", parent, fix])
    if code != 0:
        return fix, "error", 0, 0, 0, time.time() - t0, {}
    files = deleted_lines(diff)
    if len(files) > max_files:
        return fix, "too_many_files", len(files), 0, 0, time.time() - t0, {}
    if not files:
        return fix, "no_deleted_lines", 0, 0, 0, time.time() - t0, {}
    counts, n_lines, n_fail = Counter(), 0, 0
    for path, nums in files.items():
        base = ["blame", "--porcelain", "-w"]
        if IGNORE_REVS:
            base += ["--ignore-revs-file", IGNORE_REVS]
        # Windows limits a command line to ~32k characters, and a file can have thousands of
        # separate ranges, so blame the ranges in chunks (same result, several calls).
        rs = ranges(nums)
        file_failed = False
        for i in range(0, len(rs), CHUNK):
            cmd = list(base)
            for a, b in rs[i:i + CHUNK]:
                cmd += ["-L", f"{a},{b}"]
            cmd += [parent, "--", path]
            code, out, err = git(cmd)
            if code != 0:
                file_failed = True
                continue
            for line in out.split("\n"):
                m = BLAME_RE.match(line)
                if m:
                    counts[m.group(1)] += 1
                    n_lines += 1
        if file_failed:
            n_fail += 1
    status = "ok" if (n_fail < len(files)) else "error"
    return fix, status, len(files), n_lines, n_fail, time.time() - t0, dict(counts)


def init_worker(repo, ignore_revs):
    global REPO, IGNORE_REVS
    REPO, IGNORE_REVS = repo, ignore_revs


SCHEMA = """
CREATE TABLE IF NOT EXISTS szz_fixes (
    fix_hash     TEXT PRIMARY KEY,
    bug_id       INTEGER,
    status       TEXT,
    n_files      INTEGER,
    n_lines      INTEGER,
    n_blame_fail INTEGER,
    secs         REAL
);
CREATE TABLE IF NOT EXISTS szz_pairs (
    fix_hash       TEXT,
    inducing_hash  TEXT,
    n_lines        INTEGER,   -- lines of the fix that trace back to this candidate
    fix_ts         INTEGER,
    inducing_ts    INTEGER,
    bug_created_ts INTEGER,
    after_report   INTEGER    -- 1 if the candidate is dated after the bug report was created
);
CREATE INDEX IF NOT EXISTS idx_pairs_fix ON szz_pairs(fix_hash);
CREATE INDEX IF NOT EXISTS idx_pairs_ind ON szz_pairs(inducing_hash);
"""


def percentile(sorted_vals, p):
    return sorted_vals[min(len(sorted_vals) - 1, int(p / 100 * len(sorted_vals)))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db")
    ap.add_argument("repo")
    ap.add_argument("--limit", type=int, default=None, help="only trace this many fixes (timing test)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--max-files", type=int, default=100, help="skip fixes touching more files than this")
    args = ap.parse_args()

    chk = subprocess.run(["git", "-C", args.repo, "rev-parse", "--git-dir"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if chk.returncode != 0:
        sys.exit(f"'{args.repo}' is not a git repository. Give the full path to your Firefox clone.")
    conn = sqlite3.connect(args.db)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='fix_commits'").fetchone():
        sys.exit("table 'fix_commits' not found. Run find_fixes.py first.")
    conn.executescript(SCHEMA)

    ignore = os.path.join(args.repo, ".git-blame-ignore-revs")
    ignore = ignore if os.path.exists(ignore) else None
    print(f"blame ignore file: {ignore or 'none found'}   workers: {args.workers}", file=sys.stderr)

    fixes = conn.execute("""SELECT hash, parents, bug_id, author_ts, bug_created
                            FROM fix_commits ORDER BY hash""").fetchall()   # hash order = a random-like spread
    done = {r[0] for r in conn.execute("SELECT fix_hash FROM szz_fixes WHERE status != 'error'")}
    todo = [f for f in fixes if f[0] not in done]
    total_todo = len(todo)
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(fixes)} fix commits, {len(fixes) - total_todo} already done, "
          f"{len(todo)} to trace this run", file=sys.stderr)

    info = {f[0]: f for f in todo}
    tasks = [(f[0], (f[1] or "").split()[0] if (f[1] or "").split() else "", args.max_files) for f in todo]

    t0 = time.time()
    n = 0
    pool = Pool(args.workers, initializer=init_worker, initargs=(args.repo, ignore))
    try:
        for fix, status, nf, nl, nfail, secs, cands in pool.imap_unordered(trace_fix, tasks):
            _, _, bug_id, fix_ts, created = info[fix]
            created_ts = to_ts(created) if created else None
            conn.execute("DELETE FROM szz_pairs WHERE fix_hash=?", (fix,))
            conn.execute("INSERT OR REPLACE INTO szz_fixes VALUES (?,?,?,?,?,?,?)",
                         (fix, bug_id, status, nf, nl, nfail, secs))
            for ind, lines in cands.items():
                r = conn.execute("SELECT author_ts FROM commits WHERE hash=?", (ind,)).fetchone()
                ind_ts = r[0] if r else None
                after = None if (ind_ts is None or created_ts is None) else int(ind_ts > created_ts)
                conn.execute("INSERT INTO szz_pairs VALUES (?,?,?,?,?,?,?)",
                             (fix, ind, lines, fix_ts, ind_ts, created_ts, after))
            n += 1
            if n % 100 == 0:
                conn.commit()
                el = time.time() - t0
                print(f"  {n}/{len(todo)} traced ({el:,.0f}s, {el / n:.2f}s per fix)", file=sys.stderr)
    finally:
        pool.terminate()
        conn.commit()

    el = time.time() - t0
    print(f"\ntraced {n} fixes in {el:,.0f}s  ({el / max(n, 1):.2f}s per fix, wall clock, {args.workers} workers)")
    remaining = total_todo - n
    if remaining > 0 and n:
        print(f"remaining {remaining} fixes at this rate: about {remaining * el / n / 3600:.1f} hours")

    q = lambda s, *a: conn.execute(s, a).fetchone()[0]
    stat = conn.execute("SELECT status, COUNT(*) FROM szz_fixes GROUP BY 1").fetchall()
    print("\nszz_fixes by status:", stat)
    if n and sum(c for st, c in stat if st == "error") > 0.5 * n:
        print("WARNING: most fixes ended in 'error', so the timing above is not meaningful. "
              "Check the repo path and that git is on your PATH.")
    print("pairs (fix, candidate):", q("SELECT COUNT(*) FROM szz_pairs"))
    print("  distinct candidates:    ", q("SELECT COUNT(DISTINCT inducing_hash) FROM szz_pairs"))
    print("  rejected, after report: ", q("SELECT COUNT(*) FROM szz_pairs WHERE after_report=1"))
    gaps = sorted((f - i) / 86400 for f, i in conn.execute(
        "SELECT fix_ts, inducing_ts FROM szz_pairs WHERE after_report=0 AND inducing_ts IS NOT NULL"))
    if gaps:
        print("  days from candidate to fix (accepted pairs in the table): "
              f"median {percentile(gaps, 50):.0f}, 80th pct {percentile(gaps, 80):.0f}, "
              f"90th pct {percentile(gaps, 90):.0f}, max {gaps[-1]:.0f}")
    conn.close()


if __name__ == "__main__":
    main()