#!/usr/bin/env python3

# Step 3 of the JIT pipeline: fetch the Bugzilla report for each bug number found in commit messages.

# Usage:
#     python fetch_bugs.py firefox.sqlite --start 2024-10-01
#     python fetch_bugs.py firefox.sqlite --start 2024-10-01 --end 2026-10-31

# Which bugs: distinct commits.bug_id from commits that are not merges, backouts or automated,
# with author_date from --start (day precision; leave a day or two of margin) up to --end (default: no end).

# Stores one row per bug in the `bugs` table:
#     id, found, type, status, resolution, creation_time, product, component
# found = 0 means Bugzilla returned nothing for that number (does not exist, or private/restricted).
# Safe to stop and re-run: bugs already in the table are skipped.

# Uses only the standard library. Be polite: small batches, a pause between requests.

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

FIELDS = "id,type,status,resolution,creation_time,product,component"

SCHEMA = """
CREATE TABLE IF NOT EXISTS bugs (
    id            INTEGER PRIMARY KEY,
    found         INTEGER,
    type          TEXT,
    status        TEXT,
    resolution    TEXT,
    creation_time TEXT,   -- ISO 8601, UTC ('Z')
    product       TEXT,
    component     TEXT
);
"""


def fetch(base_url, ids, retries=4):
    """Return the list of bug dicts Bugzilla gives back for these ids."""
    q = urllib.parse.urlencode({"bug_id": ",".join(map(str, ids)), "include_fields": FIELDS})
    req = urllib.request.Request(f"{base_url}/rest/bug?{q}",
                                 headers={"User-Agent": "cisc834-jit-replication/1.0 (student project)",
                                          "Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)["bugs"]
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise


def fetch_split(base_url, ids):
    """If a batch fails outright, split it in half so one bad number cannot sink the rest."""
    try:
        return fetch(base_url, ids)
    except urllib.error.HTTPError as e:
        if len(ids) == 1:
            print(f"  bug {ids[0]}: HTTP {e.code}, recorded as not found", file=sys.stderr)
            return []
        mid = len(ids) // 2
        return fetch_split(base_url, ids[:mid]) + fetch_split(base_url, ids[mid:])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db")
    ap.add_argument("--start", required=True, help="first commit date to include, YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="last commit date to include, YYYY-MM-DD (default: no end)")
    ap.add_argument("--batch", type=int, default=100, help="bug numbers per request")
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--base-url", default="https://bugzilla.mozilla.org")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.executescript(SCHEMA)

    sql = """SELECT DISTINCT bug_id FROM commits
             WHERE bug_id IS NOT NULL AND is_merge=0 AND is_backout=0 AND is_automated=0
               AND substr(author_date,1,10) >= ?"""
    params = [args.start]
    if args.end:
        sql += " AND substr(author_date,1,10) <= ?"
        params.append(args.end)
    wanted = sorted(r[0] for r in conn.execute(sql, params))
    done = {r[0] for r in conn.execute("SELECT id FROM bugs")}
    todo = [b for b in wanted if b not in done]
    print(f"{len(wanted)} distinct bug numbers in range, {len(done & set(wanted))} already fetched, "
          f"{len(todo)} to fetch ({-(-len(todo) // args.batch)} requests)")

    t0 = time.time()
    for i in range(0, len(todo), args.batch):
        ids = todo[i:i + args.batch]
        got = {b["id"]: b for b in fetch_split(args.base_url, ids)}
        rows = []
        for n in ids:
            b = got.get(n)
            if b:
                rows.append((n, 1, b.get("type"), b.get("status"), b.get("resolution"),
                             b.get("creation_time"), b.get("product"), b.get("component")))
            else:
                rows.append((n, 0, None, None, None, None, None, None))
        conn.executemany("INSERT OR REPLACE INTO bugs VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        print(f"  {min(i + args.batch, len(todo))}/{len(todo)} fetched ({time.time() - t0:,.0f}s)", file=sys.stderr)
        time.sleep(args.pause)

    q = lambda s, *a: conn.execute(s, a).fetchone()[0]
    print("\nbugs table:", q("SELECT COUNT(*) FROM bugs"))
    print("  found:      ", q("SELECT COUNT(*) FROM bugs WHERE found=1"))
    print("  not found:  ", q("SELECT COUNT(*) FROM bugs WHERE found=0"), "(missing or private)")
    print("  by type:    ", conn.execute("SELECT type, COUNT(*) FROM bugs WHERE found=1 GROUP BY 1 ORDER BY 2 DESC").fetchall())
    print("  defects FIXED:", q("SELECT COUNT(*) FROM bugs WHERE type='defect' AND resolution='FIXED'"))
    conn.close()


if __name__ == "__main__":
    main()