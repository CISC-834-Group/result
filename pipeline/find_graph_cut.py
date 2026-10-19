#!/usr/bin/env python3
"""Find ancestry boundaries that exclude no eligible dataset commit."""
import argparse
import csv
import json
import os
import sqlite3
import subprocess
import tempfile

ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('--db',required=True)
ap.add_argument('--dataset',required=True)
ap.add_argument('--repo',required=True)
ap.add_argument('--out',required=True)
args=ap.parse_args()
with open(args.dataset,newline='') as f:
    target={row['transactionid'] for row in csv.DictReader(f)}
conn=sqlite3.connect(args.db)
fixes=conn.execute('SELECT hash,parents FROM fix_commits').fetchall()
starts={h for h,parents in fixes}
contains={}
boundaries=set()
with tempfile.TemporaryFile(mode='w+') as graph:
    subprocess.run(['git','-C',args.repo,'rev-list','--parents','--topo-order','--reverse','--stdin'],
                   input='\n'.join(sorted(starts))+'\n',text=True,stdout=graph,check=True,
                   env=dict(os.environ,GIT_NO_LAZY_FETCH='1'))
    graph.seek(0)
    for line in graph:
        parts=line.split()
        h,parents=parts[0],parts[1:]
        has_target=h in target or any(contains[p] for p in parents)
        contains[h]=has_target
        if has_target:
            boundaries.update(p for p in parents if not contains[p])
assert not boundaries.intersection(target)
assert all(not contains[h] for h in boundaries)
result={'boundary_commits':sorted(boundaries),'target_hashes':sorted(target),
        'fix_parents_without_targets':[h for h,ps in fixes if not ps or not contains[ps.split()[0]]],
        'reachable_commits':len(contains)}
with open(args.out,'w') as f:
    json.dump(result,f)
    f.write('\n')
print('Safe boundaries:',len(boundaries),'eligible commits:',len(target))
