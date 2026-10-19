#!/usr/bin/env python3
"""Prepare general and top-five product inputs for the human and AI-filtered datasets."""
import argparse
import csv
import math
import re
from pathlib import Path
from collections import Counter

COLUMNS = ['transactionid','commitdate','ns','nm','nf','entropy','la','ld','lt','fix',
           'ndev','pd','npt','exp','rexp','sexp','bug']

def read(path):
    with open(path,newline='',encoding='utf-8') as f:
        return list(csv.DictReader(f))

def normalize(row):
    r=dict(row)
    for key in COLUMNS[2:]:
        r[key]=float(r[key])
    nf,lt=r['nf'],r['lt']
    if nf<1 or lt<0:
        raise ValueError('Invalid NF or LT: '+r['transactionid'])
    if lt>=1:
        r['la']/=lt
        r['ld']/=lt
    r['lt']/=nf
    r['npt']/=nf
    if nf>=2:
        r['entropy']/=math.log2(nf)
    if not all(math.isfinite(r[k]) and r[k]>=0 for k in COLUMNS[2:]):
        raise ValueError('Invalid feature: '+r['transactionid'])
    r['bug']=int(r['bug'])
    r['fix']=int(r['fix'])
    return r

def write(path,rows,columns):
    with open(path,'w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--jit',required=True,type=Path)
    ap.add_argument('--human',default='dataset_human.csv')
    ap.add_argument('--ai',default='dataset_ai.csv')
    ap.add_argument('--flags',default='ai_flags_combined.csv')
    ap.add_argument('--summary',default='model_manifest.csv')
    args=ap.parse_args()
    flags={r['sha'] for r in read(args.flags) if r['ai_flag']=='1'}
    datasets={'human':read(args.human),'ai':[r for r in read(args.ai) if r['transactionid'] in flags]}
    summary=[]
    baseline=args.jit/'input/mozilla.csv'
    if baseline.exists():
        data=read(baseline)
        summary.append(dict(project='mozilla',dataset='original_mozilla',product='all',role='primary',
                            rows=len(data),buggy=sum(float(r['bug'])==1 for r in data)))
    human_products=[p for p,n in Counter(r['product'] for r in datasets['human'] if r['product']).most_common(5)]
    for name,rows in datasets.items():
        if not rows:
            raise ValueError('Empty dataset: '+name)
        if len({r['transactionid'] for r in rows})!=len(rows):
            raise ValueError('Duplicate commit: '+name)
        products=[p for p,n in Counter(r['product'] for r in rows if r['product']).most_common(5)]
        if len(products)!=5:
            raise ValueError('Fewer than five products: '+name)
        extra=[p for p in human_products if p not in products] if name=='ai' else []
        for product in ['all']+products+extra:
            sub=rows if product=='all' else [r for r in rows if r['product']==product]
            project='firefox_'+name+('' if product=='all' else '_'+re.sub('[^A-Za-z0-9]+','_',product).strip('_'))
            write(args.jit/'input'/(project+'.csv'),[normalize(r) for r in sub],COLUMNS)
            summary.append(dict(project=project,dataset=name,product=product,role='supplemental_matched' if product in extra else 'primary',rows=len(sub),
                                buggy=sum(r['bug']=='1' for r in sub)))
    write(args.summary,summary,list(summary[0]))
    (args.jit/'script_r/firefox_projects.r').write_text('projects = c('+', '.join('"'+s['project']+'"' for s in summary)+')\n')
    print('Wrote',len(summary),'model inputs. Product ranking is computed separately for each dataset.')

if __name__=='__main__':
    main()
