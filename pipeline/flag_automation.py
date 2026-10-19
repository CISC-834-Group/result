#!/usr/bin/env python3

# Step 2 of the JIT pipeline: add flag columns to the commits table. Nothing is deleted.

# Usage:
#     python flag_automation.py firefox.sqlite

# Adds (or refreshes) one column on `commits`:
#     is_automated     1 if the first line of the message matches a machine-generated pattern
#                      (see AUTOMATED below), else 0.

# The printed summary breaks the count down by pattern, but the reason is not stored.
# Safe to re-run: the column is created if missing and recomputed from the messages.

# Report notes: these are message patterns, not proof of who made the commit. The summary prints the
# most common authors among the flagged commits so you can check that they look like bots or scripts.

import re
import sqlite3
import sys
from collections import Counter

# (name, regex) - checked in order, first match wins. Applied to the first line of the message.
AUTOMATED = [
    ("wpt_sync",        re.compile(r"\[wpt PR \d+\]", re.IGNORECASE)),
    ("vendor_import",   re.compile(r"\bVendor\s+\S+\s+from\b", re.IGNORECASE)),
    ("no_bug",          re.compile(r"^\s*no\s+bug\b", re.IGNORECASE)),
    ("l10n_bump",       re.compile(r"l10n-bump|Bumping .*l10n changesets|Import translations from", re.IGNORECASE)),
    ("testonly_tag",    re.compile(r"\ba=testonly\b", re.IGNORECASE)),
]

BATCH = 20000


def ensure_columns(conn):
    have = {r[1] for r in conn.execute("PRAGMA table_info(commits)")}
    if "is_automated" not in have:
        conn.execute("ALTER TABLE commits ADD COLUMN is_automated INTEGER")
    conn.commit()


def classify(message):
    """Return the name of the first automated pattern that matches, or None."""
    first = (message or "").split("\n", 1)[0]
    for name, rx in AUTOMATED:
        if rx.search(first):
            return name
    return None


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    conn = sqlite3.connect(sys.argv[1])
    ensure_columns(conn)

    rows = conn.execute("SELECT hash, message FROM commits").fetchall()
    by_reason = Counter()
    updates = []
    for h, msg in rows:
        reason = classify(msg)
        if reason:
            by_reason[reason] += 1
        updates.append((1 if reason else 0, h))
        if len(updates) >= BATCH:
            conn.executemany("UPDATE commits SET is_automated=? WHERE hash=?", updates)
            conn.commit()
            updates = []
    if updates:
        conn.executemany("UPDATE commits SET is_automated=? WHERE hash=?", updates)
        conn.commit()

    q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
    total = q("SELECT COUNT(*) FROM commits")
    print(f"commits: {total}")
    print(f"  merges:        {q('SELECT COUNT(*) FROM commits WHERE is_merge=1')}")
    print(f"  backouts:      {q('SELECT COUNT(*) FROM commits WHERE is_backout=1')}")
    print(f"  automated:     {q('SELECT COUNT(*) FROM commits WHERE is_automated=1')}")
    for reason, n in by_reason.most_common():
        print(f"      {reason:<14} {n}")
    clean = q("""SELECT COUNT(*) FROM commits
                 WHERE is_merge=0 AND is_backout=0 AND is_automated=0""")
    print(f"  candidate commits (not merge / backout / automated): {clean}")

    print("\nTop authors among automated commits (check these look like bots or scripts):")
    for email, n in conn.execute("""SELECT author_email, COUNT(*) FROM commits
                                    WHERE is_automated=1 GROUP BY 1 ORDER BY 2 DESC LIMIT 10"""):
        print(f"   {n:>7}  {email}")
    conn.close()


if __name__ == "__main__":
    main()