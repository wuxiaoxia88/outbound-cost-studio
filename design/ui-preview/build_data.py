"""Build the preview dataset (data.js) from a v2 warehouse.

Usage: python build_data.py <warehouse dir> <start> <end> "<label>"
Normal tickets only (no special-case flags); weights binned to 0.1kg (capped at 6kg)."""
import json, sys
from pathlib import Path

import duckdb

home, start, end, label = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
c = duckdb.connect(str(Path(home) / 'warehouse.duckdb'), read_only=True)
scope = "state = 1 AND send_day BETWEEN CAST('%s' AS DATE) AND CAST('%s' AS DATE)" % (start, end)
normal = scope + ' AND (flags & 31) = 0'
W01 = 'least(greatest(cast(ceil(weight * 10 - 1e-9) AS INT), 1), 60)'

customers = [r[0] for r in c.execute('SELECT customer FROM ship WHERE %s GROUP BY 1 ORDER BY count(*) DESC' % normal).fetchall()]
provinces = [r[0] for r in c.execute('SELECT province FROM ship WHERE %s GROUP BY 1 ORDER BY count(*) DESC' % normal).fetchall()]
ci = {x: i for i, x in enumerate(customers)}
pi = {x: i for i, x in enumerate(provinces)}
r3 = lambda v: round(float(v or 0), 3)

# A: customer, province, 0.1kg unit, tickets, weight, fee, dispatch, transit, waybill fee, tickets flagged 一客一价
A = [[ci[cu], pi[p], u, n, r3(sw), r3(fee), r3(d), r3(t), r3(m), sub] for cu, p, u, n, sw, fee, d, t, m, sub in c.execute(
    "SELECT customer, province, %s, count(*), sum(weight), sum(fee), sum(dispatch), sum(transit), sum(waybill_fee), count(*) FILTER (WHERE subsidy = '一客一价') FROM ship WHERE %s GROUP BY ALL" % (W01, normal)).fetchall()]
cities, C = {}, []
for cu, p, city, u, n, fee in c.execute('SELECT customer, province, city, %s, count(*), sum(fee) FROM ship WHERE %s GROUP BY ALL' % (W01, normal)).fetchall():
    k = cities.setdefault(p + '/' + city, len(cities))
    C.append([ci[cu], k, u, n, r3(fee)])
D = [[ci[cu], str(d), n, r3(fee)] for cu, d, n, fee in c.execute(
    'SELECT customer, send_day, count(*), sum(fee) FROM ship WHERE %s GROUP BY ALL ORDER BY 2' % normal).fetchall()]
kind = "CASE WHEN flags & 5 <> 0 THEN 'invalid' WHEN flags & 8 <> 0 THEN 'self' WHEN flags & 16 <> 0 THEN 'return' ELSE 'noweight' END"
K = [[ci.get(cu, -1), k, n, r3(fee)] for cu, k, n, fee in c.execute(
    'SELECT customer, %s, count(*), sum(fee) FROM ship WHERE %s AND (flags & 31) <> 0 GROUP BY ALL' % (kind, scope)).fetchall()]
# Billing steps: jumps in dispatch fee between neighbouring 0.1kg bins, measured within the same province.
c.execute('CREATE TEMP TABLE pb AS SELECT province, %s AS w1, count(*) AS n, avg(dispatch) AS d FROM ship WHERE %s GROUP BY ALL' % (W01, normal))
# Ignore the capped last bin and thinly supported heavy weights.
steps = [[round(w / 10, 1), round(j, 2)] for w, j, support in c.execute('''SELECT a.w1, sum(least(a.n, b.n) * (b.d - a.d)) / sum(least(a.n, b.n)), sum(least(a.n, b.n))
    FROM pb a JOIN pb b ON a.province = b.province AND b.w1 = a.w1 + 1 WHERE a.n >= 30 AND b.n >= 30 AND a.w1 < 50 GROUP BY 1 ORDER BY 1''').fetchall()
    if j >= 0.1 and support >= 300]
rows = c.execute('SELECT count(*) FROM ship WHERE %s' % scope).fetchone()[0]
# RET: per customer after-sales (拦截 / 退件 / 退改) tickets: all tickets, return tickets by type, their bill fee and the separate 退件费 column
RF = 'TRY_CAST(NULLIF(x."退件费", \'\') AS DOUBLE)'
RET = [[ci[cu], n, rn, n1, n2, n3, r3(rfee), r3(rbill)] for cu, n, rn, n1, n2, n3, rfee, rbill in c.execute('''SELECT s.customer, count(*),
    count(*) FILTER (WHERE (s.flags & 16) <> 0),
    count(*) FILTER (WHERE (s.flags & 16) <> 0 AND s.product LIKE '%%拦截%%' AND s.product LIKE '%%退改%%'),
    count(*) FILTER (WHERE (s.flags & 16) <> 0 AND s.product LIKE '%%拦截%%' AND s.product NOT LIKE '%%退改%%'),
    count(*) FILTER (WHERE (s.flags & 16) <> 0 AND s.product NOT LIKE '%%拦截%%'),
    coalesce(sum(s.fee) FILTER (WHERE (s.flags & 16) <> 0), 0), coalesce(sum(%s) FILTER (WHERE (s.flags & 16) <> 0), 0)
    FROM ship s JOIN raw x USING (id) WHERE %s GROUP BY 1''' % (RF, scope.replace('state', 's.state').replace('send_day', 's.send_day'))).fetchall() if cu in ci]
data = {'customers': customers, 'provinces': provinces, 'cities': list(cities), 'A': A, 'C': C, 'D': D, 'K': K, 'RET': RET, 'steps': steps,
        'dates': [start, end], 'file': label, 'rows': rows}
out = Path(__file__).parent / 'data.js'
out.write_text('window.SAMPLE=' + json.dumps(data, ensure_ascii=False, separators=(',', ':')) + ';')
print('customers', len(customers), 'A', len(A), 'C', len(C), 'steps', steps, 'bytes', out.stat().st_size)

# Generate the optional prototype's history companion from the same local source.
# This output is ignored by Git, just like data.js.
from calendar import monthrange
from collections import defaultdict
hist={'months':[],'days':{},'net':{},'rows':[]}
month_names=[x[0] for x in c.execute("SELECT DISTINCT strftime(send_day,'%Y-%m') FROM ship WHERE state=1 AND flags&31=0 AND send_day<=? ORDER BY 1 DESC LIMIT 6",[end]).fetchall()][::-1]
crit=' OR '.join('(weight>%s AND weight<=%s)'%(s,round(s+.1,8)) for s,_ in steps) or 'FALSE'
for m in month_names:
    y,mo=map(int,m.split('-'));hist['months'].append(m);hist['days'][m]=monthrange(y,mo)[1]
    wc="state=1 AND flags&31=0 AND strftime(send_day,'%Y-%m')=?"
    bins=defaultdict(lambda:[0]*12)
    for name,u,n in c.execute("SELECT customer,least(greatest(cast(ceil(weight*2-1e-9) AS INT),1),12),count(*) FROM ship WHERE "+wc+' GROUP BY 1,2',[m]).fetchall():bins[name][u-1]=n
    vals=c.execute('SELECT customer,count(*),count(DISTINCT send_day),sum(weight),sum(fee),count(*) FILTER (WHERE '+crit+') FROM ship WHERE '+wc+' GROUP BY 1',[m]).fetchall()
    net=[0]*16
    for name,n,days,w,fee,cr in vals:
        value=[n,float(w),float(fee),cr]+bins[name];net=[a+b for a,b in zip(net,value)]
        if name in ci:hist['rows'].append([ci[name],m,n,days,float(w),float(fee),cr]+bins[name])
    hist['net'][m]=net
(Path(__file__).parent/'history.js').write_text('window.HIST='+json.dumps(hist,ensure_ascii=False,separators=(',',':'))+';')
c.close()
