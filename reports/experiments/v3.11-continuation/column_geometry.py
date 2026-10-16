"""Column-geometry experiment for continuation detection (V3.11).

Pairs every two candidate tables (all tools and strategies) with the same column count and compares
them by table extent overlap and column-separator shift. Run from the repository root:
    python reports/experiments/v3.11-continuation/column_geometry.py
"""
import json, glob, os, itertools
from collections import defaultdict

def intervals(c):
    out = {}
    for x in c['cells']:
        b = x.get('bbox')
        if not b or x['end_col_offset_idx'] - x['start_col_offset_idx'] != 1: continue
        if not (x.get('text') or '').strip(): continue
        a = out.setdefault(x['start_col_offset_idx'], [1e9, -1e9]); a[0] = min(a[0], b['x0']); a[1] = max(a[1], b['x1'])
    return out

def extent(c):
    bs = [x['bbox'] for x in c['cells'] if x.get('bbox') and (x.get('text') or '').strip()]
    return min(b['x0'] for b in bs), max(b['x1'] for b in bs)

def compare(c1, c2):
    i1, i2 = intervals(c1), intervals(c2)
    n = c1['column_count']
    ratios = []
    for k in range(n):
        if k in i1 and k in i2:
            (a0, a1), (b0, b1) = i1[k], i2[k]
            w = min(a1 - a0, b1 - b0)
            ratios.append(max(0.0, min(a1, b1) - max(a0, b0)) / w if w > 0 else 0.0)
    (p0, p1), (q0, q1) = extent(c1), extent(c2)
    ext = max(0.0, min(p1, q1) - max(p0, q0)) / (max(p1, q1) - min(p0, q0))
    return len(ratios) / n, (min(ratios) if ratios else None), ext



def separators(c):
    iv = intervals(c); n = c['column_count']
    seps = {}
    for k in range(n - 1):
        if k in iv and k + 1 in iv:
            r, l = iv[k][1], iv[k + 1][0]
            seps[k] = (min(r, l), max(r, l), iv[k][1] - iv[k][0], iv[k + 1][1] - iv[k + 1][0])
    return seps

def sep_shift(c1, c2):
    s1, s2 = separators(c1), separators(c2)
    worst = 0.0; worst_abs = 0.0; n = 0
    for k in s1.keys() & s2.keys():
        a0, a1, aw1, aw2 = s1[k]; b0, b1, bw1, bw2 = s2[k]
        dist = max(0.0, max(a0, b0) - min(a1, b1))       # 0 when the separator ranges intersect
        width = min(aw1, aw2, bw1, bw2)                   # narrowest adjacent column on either side
        worst = max(worst, dist / width if width > 0 else 9); worst_abs = max(worst_abs, dist); n += 1
    total = c1['column_count'] - 1
    return (n / total if total else 1.0), worst, worst_abs

cands = []
for f in sorted(glob.glob(r'.rag/indexes/structured/documents/*/tables.json')):
    d = json.load(open(f, encoding='utf-8'))
    doc = os.path.basename(os.path.dirname(f))[:14]
    by = {c['candidate_id']: c for r in d['extraction_reports'] for c in r['candidates']}
    for g in d['grouping']['groups']:
        for cid in g['member_candidate_ids']:
            c = by[cid]
            if c['column_count'] and any(x.get('bbox') for x in c['cells']):
                cands.append((f"{doc}/{g['slot_id']}", c))

rows = []
for (t1, c1), (t2, c2) in itertools.combinations(cands, 2):
    if c1['column_count'] != c2['column_count'] or c1['column_count'] < 2: continue
    comp, rel, ab = sep_shift(c1, c2)
    (p0, p1), (q0, q1) = extent(c1), extent(c2)
    ext = max(0.0, min(p1, q1) - max(p0, q0)) / (max(p1, q1) - min(p0, q0))
    rows.append((t1 == t2, t1, f"{c1['tool']}/{c1['strategy']}", t2, f"{c2['tool']}/{c2['strategy']}", c1['column_count'], comp, rel, ab, ext))


cont = frozenset(('签字-ORDER PERU /slot_002', '签字-ORDER PERU /slot_003'))
def group(r):
    if r[0] or frozenset((r[1], r[3])) == cont: return 'same physical table (incl. continuation)'
    return 'different tables'
ok = lambda r: r[9] >= 0.8 and r[7] <= 0.35 and r[6] >= 0.5
from collections import defaultdict
g = defaultdict(list)
for r in rows: g[group(r)].append(r)
for name, rs in g.items():
    rels = sorted(r[7] for r in rs)
    print(f"{name}: pairs={len(rs)} rel max(excluding structurally wrong network/text)=", max(x for x in rels if x < 1.5), "passing rule:", sum(ok(r) for r in rs))
print('\ndifferent tables passing the rule:')
from collections import Counter
print(Counter((r[1], r[3]) for r in g['different tables'] if ok(r)))
print('\nsame physical table failing the rule:')
for r in g['same physical table (incl. continuation)']:
    if not ok(r): print('  ', r[1], r[2], 'vs', r[3], r[4], f'rel={r[7]:.2f} ext={r[9]:.2f}')
