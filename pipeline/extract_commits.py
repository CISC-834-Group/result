#!/usr/bin/env python3

# Step 1 of the JIT pipeline: read the full git history once and store it in SQLite.

# Usage:
#     python extract_commits.py "C:\Users\sahee\OneDrive\Desktop\firefox-1" firefox.sqlite
#     python extract_commits.py "C:\Users\sahee\OneDrive\Desktop\firefox-1" --max-count 2000   # quick test

# What it stores
#   commits       one row per commit (merges included, flagged)
#   file_changes  one row per (commit, file) with lines added / deleted

# It uses plain `git log --numstat` (streamed), not PyDriller, because that is much
# faster on a repo with ~1M commits and needs no extra packages.

# No date window or AI filter is applied here. Later steps filter with SQL.
# Re-running is safe: existing commits are skipped (INSERT OR IGNORE).

# Notes / choices to document in the report
#   * --no-renames: a renamed file shows up as one delete + one add (simple, consistent).
#   * Merge commits get no file_changes rows (git shows no diff for them by default).
#   * Binary files are stored with added = deleted = 0 and is_binary = 1.
#   * bug_id / bug_ids: "Bug NNNNNN" numbers on the FIRST LINE of the message only (the bug the
#     commit is for). bug_ids_all also lists numbers mentioned in the body, for inspection.
#   * is_backout: first line starts with "Back(ed) out" or "Revert". Check this on a sample.
#   * fix_kw: message contains fix/fixed/fixes/defect/patch (the paper's keyword idea,
#     minus "bug", which almost every Firefox commit contains).

import argparse
import re
import sqlite3
import subprocess
import sys
import time

REC = "\x1e"   # record separator, between commits
FLD = "\x1f"   # field separator, inside a commit
# hash, parents, author name, author email, author date, commit date, full message
GIT_FORMAT = f"{REC}%H{FLD}%P{FLD}%an{FLD}%ae{FLD}%aI{FLD}%cI{FLD}%B{FLD}"

BUG_RE = re.compile(r"\bbug\s*#?\s*(\d{4,8})\b", re.IGNORECASE)
BACKOUT_RE = re.compile(r"^\s*(back(ed|ing)?[\s-]*out|revert)", re.IGNORECASE)
FIX_RE = re.compile(r"\b(fix(es|ed)?|defect|patch)\b", re.IGNORECASE)

SCHEMA = """
CREATE TABLE IF NOT EXISTS commits (
    hash          TEXT PRIMARY KEY,
    parents       TEXT,
    is_merge      INTEGER,
    author_name   TEXT,
    author_email  TEXT,
    author_date   TEXT,      -- ISO 8601, sorts correctly as text
    commit_date   TEXT,
    message       TEXT,
    bug_id        INTEGER,   -- first bug number on the FIRST LINE of the message, or NULL
    bug_ids       TEXT,      -- all bug numbers on the first line, comma separated
    bug_ids_all   TEXT,      -- every bug number anywhere in the message (references in the body too)
    is_backout    INTEGER,
    fix_kw        INTEGER
);
CREATE TABLE IF NOT EXISTS file_changes (
    hash       TEXT,
    path       TEXT,
    added      INTEGER,
    deleted    INTEGER,
    is_binary  INTEGER
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_commits_author_date ON commits(author_date);
CREATE INDEX IF NOT EXISTS idx_commits_bug ON commits(bug_id);
CREATE INDEX IF NOT EXISTS idx_commits_email ON commits(author_email);
CREATE INDEX IF NOT EXISTS idx_fc_hash ON file_changes(hash);
CREATE INDEX IF NOT EXISTS idx_fc_path ON file_changes(path);
"""


def stream_records(proc, chunk_size=1 << 20):
    """Yield one decoded text record per commit from git's stdout."""
    buf = b""
    sep = REC.encode()
    while True:
        chunk = proc.stdout.read(chunk_size)
        if not chunk:
            break
        buf += chunk
        *done, buf = buf.split(sep)
        for rec in done:
            if rec.strip():
                yield rec.decode("utf-8", errors="replace")
    if buf.strip():
        yield buf.decode("utf-8", errors="replace")


def parse_record(rec):
    parts = rec.split(FLD)
    if len(parts) < 8:
        return None
    h, parents, an, ae, ad, cd, msg, numstat = parts[:8]
    h = h.strip()
    parents = parents.strip()
    msg = msg.rstrip("\n")

    first_line = msg.split("\n", 1)[0]
    bugs = BUG_RE.findall(first_line)      # the bug the commit is for
    bugs_all = BUG_RE.findall(msg)         # plus any bugs merely mentioned in the body
    commit_row = (
        h,
        parents,
        1 if len(parents.split()) > 1 else 0,
        an,
        ae,
        ad,
        cd,
        msg,
        int(bugs[0]) if bugs else None,
        ",".join(dict.fromkeys(bugs)) if bugs else None,
        ",".join(dict.fromkeys(bugs_all)) if bugs_all else None,
        1 if BACKOUT_RE.match(first_line) else 0,
        1 if FIX_RE.search(msg) else 0,
    )

    file_rows = []
    for line in numstat.splitlines():
        line = line.strip("\n")
        if not line.strip():
            continue
        bits = line.split("\t", 2)
        if len(bits) != 3:
            continue
        a, d, path = bits
        if a == "-" or d == "-":
            file_rows.append((h, path, 0, 0, 1))
        else:
            file_rows.append((h, path, int(a), int(d), 0))
    return commit_row, file_rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", help="path to the cloned git repository")
    ap.add_argument("db", help="SQLite file to create or extend")
    ap.add_argument("--rev", default="HEAD", help="revision to walk back from (default HEAD)")
    ap.add_argument("--max-count", type=int, default=None, help="only read this many commits (for testing)")
    ap.add_argument("--batch", type=int, default=5000, help="commits per database write")
    args = ap.parse_args()

    cmd = [
        "git", "-C", args.repo, "-c", "core.quotepath=false",
        "log", args.rev, "--no-renames", "--numstat", f"--format={GIT_FORMAT}",
    ]
    if args.max_count:
        cmd += ["-n", str(args.max_count)]

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)

    # Skip commits we already have, so a re-run does not duplicate file rows.
    have = {r[0] for r in conn.execute("SELECT hash FROM commits")}
    print(f"{len(have)} commits already in the database", file=sys.stderr)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    c_batch, f_batch = [], []
    n_new = n_seen = 0
    t0 = time.time()

    def flush():
        nonlocal c_batch, f_batch
        if c_batch:
            conn.executemany("INSERT OR IGNORE INTO commits VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", c_batch)
        if f_batch:
            conn.executemany("INSERT INTO file_changes VALUES (?,?,?,?,?)", f_batch)
        conn.commit()
        c_batch, f_batch = [], []

    for rec in stream_records(proc):
        parsed = parse_record(rec)
        if parsed is None:
            continue
        n_seen += 1
        commit_row, file_rows = parsed
        if commit_row[0] in have:
            continue
        c_batch.append(commit_row)
        f_batch.extend(file_rows)
        n_new += 1
        if len(c_batch) >= args.batch:
            flush()
            rate = n_seen / max(time.time() - t0, 1e-9)
            print(f"  {n_seen:>9} commits read  ({rate:,.0f}/s)", file=sys.stderr)
    flush()

    err = proc.stderr.read().decode("utf-8", errors="replace").strip()
    proc.wait()
    if proc.returncode != 0:
        print(f"git exited with code {proc.returncode}: {err}", file=sys.stderr)
        sys.exit(1)

    print("Building indexes...", file=sys.stderr)
    conn.executescript(INDEXES)
    conn.commit()

    # Quick summary so you can eyeball the result.
    q = lambda sql: conn.execute(sql).fetchone()[0]
    print(f"\nDone. {n_new} new commits ({n_seen} read) in {time.time() - t0:,.0f}s")
    print("commits total:        ", q("SELECT COUNT(*) FROM commits"))
    print("  merges:             ", q("SELECT COUNT(*) FROM commits WHERE is_merge=1"))
    print("  backouts:           ", q("SELECT COUNT(*) FROM commits WHERE is_backout=1"))
    print("  with a bug number:  ", q("SELECT COUNT(*) FROM commits WHERE bug_id IS NOT NULL"))
    print("file_changes rows:    ", q("SELECT COUNT(*) FROM file_changes"))
    print("date range:           ", conn.execute("SELECT MIN(author_date), MAX(author_date) FROM commits").fetchone())
    conn.close()


if __name__ == "__main__":
    main()