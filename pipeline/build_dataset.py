#!/usr/bin/env python3

# Step 6 of the JIT pipeline: build the model-ready CSV for ONE window (one row per commit, the
# 14 Kamei et al. change metrics, the bug label, and the Bugzilla product/component).
# Run it after find_bug_inducing_commits.py has written bug_inducing_<name>.

# Usage:
#     python build_dataset.py firefox.sqlite "C:\Users\sahee\OneDrive\Desktop\firefox-1" --start 2021-11-30 --end 2022-11-29 --name human
#     python build_dataset.py firefox.sqlite "C:\Users\sahee\OneDrive\Desktop\firefox-1" --start 2025-11-01 --end 2026-10-10 --name ai
#     ... --drop-last-days 60      # robustness check: leave out the newest 60 days of commits
#
# Writes dataset_<name>.csv (or dataset_<name>_drop<N>.csv).

# Rows: commits dated in the window (author date, UTC) that are not merges, backouts or automated and
# change at least one file, minus imported Android history (see below). No AI filter: the AI dataset
# is every such commit from the AI window.
# --drop-last-days N removes rows from the last N days of the window. Labels are NOT changed by it:
# a row is still buggy if a fix dated up to the window end blames it.

# Columns (names follow the Kamei et al. replication package):
#   transactionid, commitdate     commit hash and author date (UTC)
#   bug_id, product, component    first-line bug number and its Bugzilla product / component
#   ns       subsystems changed (top-level directories; files at the root count as "<root>")
#   nm       directories changed (full parent directory of each file)                 [paper: ND]
#   nf       files changed
#   entropy  Shannon entropy (log2) of the lines changed (added + deleted) across the files; raw,
#            not divided by log2(nf)
#   la, ld   lines added / deleted (raw; binary files count 0)
#   lt       lines in the changed files before the change (sum over files; new and binary files 0)
#   fix      1 if the commit's bug is a Bugzilla 'defect' (every commit message names a bug, so the
#            paper's keyword rule would mark almost everything as a fix)
#   ndev     distinct developers (author emails) who changed these files before
#   pd       mean days since each file was last changed (files with no history count 0)  [paper: AGE]
#   npt      distinct commits that last changed these files                             [paper: NUC]
#   exp      the author's earlier commits
#   rexp     the author's earlier commits weighted by 1 / (years ago + 1)
#   sexp     the author's earlier commits that touched any of the subsystems changed here
#   bug      1 if the commit is in bug_inducing_<name>, else 0
# No normalization here (la/lt, ld/lt, lt/nf, npt/nf, entropy/log2(nf)); do it in the modeling step.

# History metrics replay every non-merge commit (backouts and automated commits included, because they
# did change the files) in author-date order, using only commits dated before the row. Authors are
# identified by lowercased email; aliases are not merged. lt reads each file from the commit's first
# parent with `git cat-file --batch`.

import argparse
import json
import bisect
import csv
import math
import re
import sqlite3
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from itertools import groupby

DAY = 86400
YEAR = 365.25 * DAY


def day_ts(s, end=False):
    d = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    return int(d.timestamp()) + (DAY if end else 0)


def subsystem(path):
    return path.split("/", 1)[0] if "/" in path else "<root>"


def directory(path):
    return path.rsplit("/", 1)[0] if "/" in path else "<root>"


def entropy(churns):
    total = sum(churns)
    if total == 0:
        return 0.0
    return abs(-sum(c / total * math.log2(c / total) for c in churns if c))   # abs: no "-0.0"


def recent_exp(times, t):
    """Sum of 1 / (n + 1) over earlier commits, n = whole years between that commit and t."""
    total, hi, k = 0.0, len(times), 0
    while hi > 0:
        lo = bisect.bisect_right(times, t - (k + 1) * YEAR, 0, hi)
        total += (hi - lo) / (k + 1)
        hi, k = lo, k + 1
    return total


def lines_before(repo, pairs):
    """{(parent, path): number of lines in that file at that parent}; files missing there count 0."""
    proc = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def feed():
        for parent, path in pairs:
            proc.stdin.write(f"{parent}:{path}\n".encode("utf-8"))
        proc.stdin.close()
    threading.Thread(target=feed, daemon=True).start()

    out, t0 = {}, time.time()
    for i, key in enumerate(pairs, 1):
        header = proc.stdout.readline().split()
        if len(header) == 3 and header[2].isdigit():
            data = proc.stdout.read(int(header[2]) + 1)[:-1]     # content plus a trailing newline
            n = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
            out[key] = n if header[1] == b"blob" else 0
        else:
            parent, path = key
            check = subprocess.run(["git", "-C", repo, "ls-tree", parent, "--", path],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if check.returncode != 0 or check.stdout.strip():
                proc.terminate()
                raise RuntimeError(f"Historical contents unavailable for {parent}:{path}; complete the Git objects before building LT")
            out[key] = 0  # The parent tree confirms this is a new path.
        if i % 50000 == 0:
            print(f"  lt: {i}/{len(pairs)} files read ({time.time() - t0:,.0f}s)", file=sys.stderr)
    proc.wait()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db")
    ap.add_argument("repo", help="path to the Firefox clone (for lt)")
    ap.add_argument("--start", required=True, help="first day of the window, YYYY-MM-DD (inclusive)")
    ap.add_argument("--end", required=True, help="last day of the window, YYYY-MM-DD (inclusive)")
    ap.add_argument("--name", required=True, help="window name; labels come from bug_inducing_<name>")
    ap.add_argument("--drop-last-days", type=int, default=0, help="leave out rows from the last N days")
    ap.add_argument("--out", default=None, help="CSV path (default dataset_<name>[_drop<N>].csv)")
    ap.add_argument("--defer-lt", action="store_true", help="Save parent-file references for a separate verified LT pass")
    args = ap.parse_args()
    if not re.fullmatch(r"\w+", args.name):
        sys.exit("--name may only contain letters, digits and _")
    label_table = f"bug_inducing_{args.name}"
    out_path = args.out or (f"dataset_{args.name}" + (f"_drop{args.drop_last_days}" if args.drop_last_days else "") + ".csv")

    conn = sqlite3.connect(args.db)
    for t in ("commits", "file_changes", "bugs", label_table):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone():
            sys.exit(f"table '{t}' not found. Run find_bug_inducing_commits.py --name {args.name} first.")

    w0, w_end = day_ts(args.start), day_ts(args.end, end=True)
    w1 = w_end - args.drop_last_days * DAY                      # rows are dated in [w0, w1)
    q = lambda s, *a: conn.execute(s, a).fetchone()[0]
    ok = "is_merge = 0 AND is_backout = 0 AND is_automated = 0"
    n_window = q(f"SELECT COUNT(*) FROM commits WHERE author_ts >= ? AND author_ts < ? AND {ok}", w0, w1)
    # Android history (Fenix, Focus, Android Components) developed in separate GitHub repos with GitHub
    # issues until 2023 and was imported later with its original dates. Its fixes cannot be linked to
    # Bugzilla, so SZZ labels almost all of it clean. Leave it out of the rows (still part of history).
    imported = {r[0] for r in conn.execute(
        f"""SELECT hash FROM commits WHERE author_ts >= ? AND author_ts < ? AND {ok}
            AND (message LIKE '[fenix]%' OR message LIKE '[focus]%' OR message LIKE '[components]%')""", (w0, w1))}
    buggy = {r[0] for r in conn.execute(f"SELECT hash FROM {label_table}")}
    bugs = {r[0]: r[1:] for r in conn.execute("SELECT id, type, product, component FROM bugs WHERE found = 1")}

    # Replay history up to the end of the rows, computing metrics for each row before adding it.
    print("Replaying commit history for the history metrics...", file=sys.stderr)
    file_last = {}                         # path -> (ts, commit number) of the latest change
    file_devs = defaultdict(set)           # path -> author ids who changed it
    author_times = defaultdict(list)       # author id -> sorted commit timestamps
    author_sub = defaultdict(list)         # (author id, subsystem) -> commit numbers
    author_id = {}
    rows = []
    cur = conn.execute("""SELECT c.hash, c.parents, c.author_ts, c.author_email, c.author_date, c.bug_id,
                                 c.is_backout, c.is_automated, f.path, f.added, f.deleted
                          FROM commits c JOIN file_changes f ON f.hash = c.hash
                          WHERE c.is_merge = 0 AND c.author_ts < ?
                          ORDER BY c.author_ts, c.hash""", (w1,))
    t0 = time.time()
    for n, (h, grp) in enumerate(groupby(cur, key=lambda r: r[0])):
        grp = list(grp)
        _, parents, ts, email, adate, bug_id, is_backout, is_auto = grp[0][:8]
        files = [(r[8], r[9], r[10]) for r in grp]
        paths = [p for p, _, _ in files]
        subs = {subsystem(p) for p in paths}
        a = author_id.setdefault((email or "").lower(), len(author_id))

        if ts >= w0 and not is_backout and not is_auto and h not in imported:
            known = [file_last[p] for p in paths if p in file_last]
            devs = set().union(*(file_devs[p] for p in paths if p in file_devs))
            prior_sub = set().union(*(author_sub[(a, s)] for s in subs))
            b = bugs.get(bug_id)
            rows.append({
                "transactionid": h,
                "commitdate": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "bug_id": bug_id if bug_id is not None else "",
                "product": b[1] if b else "",
                "component": b[2] if b else "",
                "ns": len(subs),
                "nm": len({directory(p) for p in paths}),
                "nf": len(paths),
                "entropy": round(entropy([ad + de for _, ad, de in files]), 6),
                "la": sum(ad for _, ad, _ in files),
                "ld": sum(de for _, _, de in files),
                "lt": 0,                                             # filled in below
                "fix": 1 if b and b[0] == "defect" else 0,
                "ndev": len(devs),
                "pd": round(sum(max(ts - lt, 0) for lt, _ in known) / DAY / len(paths), 4),
                "npt": len({num for _, num in known}),
                "exp": len(author_times[a]),
                "rexp": round(recent_exp(author_times[a], ts), 4),
                "sexp": len(prior_sub),
                "bug": 1 if h in buggy else 0,
                "_parent": (parents or "").split()[0] if (parents or "").split() else "",
                "_files": [(p, ad + de) for p, ad, de in files],
            })

        for p in paths:
            file_last[p] = (ts, n)
            file_devs[p].add(a)
        author_times[a].append(ts)
        for s in subs:
            author_sub[(a, s)].append(n)
        if n % 100000 == 0 and n:
            print(f"  {n} commits replayed ({time.time() - t0:,.0f}s)", file=sys.stderr)

    # lt: line count of every changed, non-binary file in the row's parent.
    binary = {(r[0], r[1]) for r in conn.execute(
        f"""SELECT f.hash, f.path FROM file_changes f JOIN commits c ON c.hash = f.hash
            WHERE c.author_ts >= ? AND c.author_ts < ? AND f.is_binary = 1""", (w0, w1))}
    pairs = sorted({(r["_parent"], p) for r in rows if r["_parent"]
                    for p, _ in r["_files"] if (r["transactionid"], p) not in binary})
    print(f"Reading {len(pairs)} files for lt...", file=sys.stderr)
    if args.defer_lt:
        with open(out_path + ".lt-refs.jsonl", "w") as refs:
            for r in rows:
                refs.write(json.dumps({"hash": r["transactionid"], "parent": r["_parent"],
                    "paths": [p for p, _ in r["_files"] if (r["transactionid"], p) not in binary]}) + "\n")
                r["lt"] = ""
    else:
        sizes = lines_before(args.repo, pairs)
        for r in rows:
            r["lt"] = sum(sizes.get((r["_parent"], p), 0) for p, _ in r["_files"])

    cols = [k for k in rows[0] if not k.startswith("_")] if rows else []
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    n_bug = sum(r["bug"] for r in rows)
    print(f"\nWindow '{args.name}': rows dated {args.start} to {datetime.fromtimestamp(w1 - 1, timezone.utc).date()}"
          + (f" (last {args.drop_last_days} days dropped)" if args.drop_last_days else ""))
    print(f"  commits in range (not merge / backout / automated): {n_window:>7}")
    print(f"     dropped, imported Android history ([fenix] / [focus] / [components]): {len(imported):>7}"
          f"  ({sum(h in buggy for h in imported)} of them buggy)")
    print(f"     dropped, no file changes:                        {n_window - len(imported) - len(rows):>7}")
    print(f"  ROWS written to {out_path}:  {len(rows)}")
    if rows:
        print(f"  buggy: {n_bug} ({100 * n_bug / len(rows):.1f}%)   fix=1: {sum(r['fix'] for r in rows)}")
        no_comp = sum(1 for r in rows if not r["component"])
        print(f"  rows without a Bugzilla product/component (no bug number, or bug missing/private): {no_comp}")
        by_comp = defaultdict(lambda: [0, 0])
        for r in rows:
            if r["component"]:
                k = f"{r['product']} :: {r['component']}"
                by_comp[k][0] += 1
                by_comp[k][1] += r["bug"]
        by_prod = defaultdict(lambda: [0, 0])
        for r in rows:
            if r["product"]:
                by_prod[r["product"]][0] += 1
                by_prod[r["product"]][1] += r["bug"]
        print("\n  Top products (rows, buggy):")
        for k, (n, b) in sorted(by_prod.items(), key=lambda x: -x[1][0])[:8]:
            print(f"     {n:>6} {b:>5}  {k}")
        print("  Top components (rows, buggy):")
        for k, (n, b) in sorted(by_comp.items(), key=lambda x: -x[1][0])[:10]:
            print(f"     {n:>6} {b:>5}  {k}")
    conn.close()


if __name__ == "__main__":
    main()
