"""V3 cost analysis. One snapshot, exact money sums, reusable 0.05kg cubes.

Amounts in internal vectors are integer ten-thousandths. Statistical rates retain
full precision; presentation rounds only at the edge. Network references never
inherit the selected-customer filter. Price comparisons use system fees, while
simulation overlays are applied only to displayed costs (v3.1).
"""
from collections import OrderedDict, defaultdict
from decimal import Decimal, ROUND_HALF_UP
import json
import math
import re
import threading
import time

from . import rules, simulation, history, regions

FIELDS = ('n', 'w', 'fee', 'disp', 'tran', 'mf', 'system', 'subsidy', 'rebate', 'adj', 'subN')
SIZE = len(FIELDS)
N, W, FEE, DISP, TRAN, MF, SYSTEM, SUB, REB, ADJ, SUBN = range(SIZE)
U05 = 'least(greatest(cast(ceil(coalesce(weight,0)*20-1e-9) AS INTEGER),1),120)'
DEFAULTS = {'baseWidth': .5, 'mainShare': .4, 'secondaryShare': .1, 'tailShare': .02, 'minGroupN': 300, 'steps': None}
REGION = {'华东': ['上海', '江苏省', '浙江省', '安徽省'], '华南': ['广东省', '福建省', '广西壮族自治区', '海南省'],
          '华北': ['北京', '天津', '河北省', '山西省', '内蒙古自治区', '山东省'], '华中': ['河南省', '湖北省', '湖南省', '江西省'],
          '西南': ['四川省', '重庆', '云南省', '贵州省', '西藏自治区'], '西北': ['陕西省', '甘肃省', '宁夏回族自治区', '青海省', '新疆维吾尔自治区'],
          '东北': ['辽宁省', '吉林省', '黑龙江省']}
REGIONS = {p: r for r, ps in REGION.items() for p in ps}
# Cache belongs to each Warehouse instance (not a path which may be re-created).
_CACHE_LOCK = threading.RLock()


def zero():
    return [0] * SIZE


def add(a, b):
    for i in range(SIZE):
        a[i] += b[i]
    return a


def sumv(values):
    a = zero()
    for v in values:
        add(a, v)
    return a


def rate(v, k=FEE):
    return v[k] / (10000 * v[N]) if v[N] else None


def public(v):
    return {**dict(zip(FIELDS, [v[N]] + [x / 10000 for x in v[1:]])),
            'avg': rate(v), 'avgW': rate(v, W), 'avgSystem': rate(v, SYSTEM)}


def integer(value, name, low=0, high=1000000):
    n = rules.finite(value, name)
    if isinstance(value, bool) or int(n) != n or not low <= n <= high:
        raise ValueError(name + '须为 %d–%d 的整数' % (low, high))
    return int(n)


def parameters(value=None):
    if value is not None and not isinstance(value, dict):
        raise ValueError('分析设置须为对象')
    out = {**DEFAULTS, **(value or {})}
    out['baseWidth'] = rules.finite(out['baseWidth'], '基础区间')
    if out['baseWidth'] not in (.25, .5, 1):
        raise ValueError('基础区间只支持 0.25 / 0.5 / 1kg')
    for k in ('mainShare', 'secondaryShare', 'tailShare'):
        out[k] = rules.finite(out[k], k)
    if not 0 <= out['tailShare'] < out['secondaryShare'] < out['mainShare'] <= 1:
        raise ValueError('门槛须满足 0 ≤ 两端合并 < 次要 < 主要 ≤ 100%')
    out['minGroupN'] = integer(out['minGroupN'], '分群最低票数', 1)
    if out['steps'] is not None:
        if not isinstance(out['steps'], list):
            raise ValueError('手工档位须为列表，自动识别请用 null')
        steps = []
        for s in out['steps']:
            step = rules.finite(s['step'], '档位重量')
            jump = rules.finite(s['jump'], '档位加价')
            if step <= 0 or step >= 5 or abs(step * 10 - round(step * 10)) > 1e-8 or jump < 0:
                raise ValueError('档位须为 0–5kg 内的 0.1kg 刻度，加价不能为负')
            if any(x['step'] == step for x in steps):
                raise ValueError('档位重量不能重复')
            steps.append({'step': step, 'jump': jump, 'source': 'manual'})
        out['steps'] = sorted(steps, key=lambda x: x['step'])
    return out


def settings(wh, req, cur):
    cfg = wh.config(cur)
    f = {k: req.get(k) for k in ('start', 'end')}
    f['dateBasis'] = req.get('dateBasis') or cfg.get('dateBasis', 'send')
    f['excludeRules'] = req.get('excludeRules', cfg.get('excludeRules', rules.DEFAULT_EXCLUDE))
    region_config = cfg['regionGroups']
    f.update(regions.scope(region_config,req.get('excludeZones')))
    rules.filter_sql(f)
    rules.exclude_mask(f['excludeRules'])
    cost = req.get('cost') or {}
    return {'scope': f, 'regionGroups':region_config, 'parameters': parameters(req.get('analysis', cfg.get('analysis'))),
            'policies': rules.policies(cost.get('subsidyPolicies', cfg['subsidyPolicies'])),
            'deductRebate': bool(cost.get('deductRebate', cfg.get('deductRebate'))),
            'adjustments': rules.adjustments(cost.get('adjustments', cfg.get('adjustments')))}


def base_sql(s, masked=True):
    where, args = rules.filter_sql(s['scope'])
    if masked:
        where.append('(flags & %d)=0' % rules.exclude_mask(s['scope']['excludeRules']))
    # V3.1 bill-only cube. Scenario amounts are overlaid after aggregation.
    return ("WITH s AS (SELECT *, coalesce(fee,0) AS actual, 0 AS sub, 0 AS extra FROM ship WHERE "
            + ' AND '.join(where) + ')', args)


def read_cubes(cur, s, level='province', customers=None, network_only=False):
    sql, args = base_sql(s)
    flow = "coalesce(province,'')" if level == 'province' else "coalesce(province,'')||'/'||coalesce(city,'')"
    cols = ('weight', 'actual', 'dispatch', 'transit', 'waybill_fee', 'fee', 'sub',
            '0', 'extra', "CASE WHEN subsidy='一客一价' THEN 1 ELSE 0 END")
    customer_expr="''" if network_only else "coalesce(customer,'')"
    where=''
    if customers is not None:
        where=' WHERE customer IN ('+','.join('?' for _ in customers)+')' if customers else ' WHERE FALSE'
        args+=customers
    query = sql + ' SELECT %s, %s, %s, count(*), %s FROM s%s GROUP BY 1,2,3' % (
        customer_expr, flow, U05, ','.join('CAST(coalesce(sum(%s),0)*10000 AS BIGINT)' % x for x in cols),where)
    cubes, net = {}, {}
    for customer, flow, u, *v in cur.execute(query, args).fetchall():
        cubes.setdefault(customer, {}).setdefault(flow, {})[u] = v
        add(net.setdefault(flow, {}).setdefault(u, zero()), v)
    return cubes, net


def roll01(cp):
    out = {}
    for q, v in cp.items():
        add(out.setdefault((q + 1) // 2, zero()), v)
    return out


def dist(cb, quantum=20):
    out = [zero() for _ in range(6 * quantum + 1)]
    for cp in cb.values():
        for q, v in cp.items():
            add(out[q if quantum == 20 else (q + 1) // 2], v)
    return out


def total(cb):
    return sumv(v for cp in cb.values() for v in cp.values())


def sum_units(cp, units):
    return sumv(cp[q] for q in units if q in cp)


def combine(cubes, names):
    out = {}
    for name in names:
        for p, cp in cubes.get(name, {}).items():
            dest = out.setdefault(p, {})
            for q, v in cp.items():
                add(dest.setdefault(q, zero()), v)
    return out


def smart_bands(d, par, expand=False):
    tot = sum(v[N] for v in d) or 1
    size = int(par['baseWidth'] * 20)
    bs = []
    for lo in range(0, 120, size):
        us = list(range(lo + 1, lo + size + 1))
        bs.append({'lo': lo / 20, 'hi': (lo + size) / 20 if lo + size < 120 else None,
                   'units': us, 'n': sum(d[q][N] for q in us)})
    i, j = 0, len(bs) - 1
    while i < j and bs[i]['n'] / tot < par['tailShare']:
        i += 1
    while j > i and bs[j]['n'] / tot < par['tailShare']:
        j -= 1
    def merged(xs, side):
        return {'lo': xs[0]['lo'], 'hi': xs[-1]['hi'], 'units': [q for b in xs for q in b['units']],
                'n': sum(b['n'] for b in xs), 'merged': side}
    bands = ([merged(bs[:i], 'low')] if i else []) + bs[i:j+1] + ([merged(bs[j+1:], 'high')] if j+1 < len(bs) else [])
    for b in bands:
        b['share'] = b['n'] / tot
        b['cls'] = 'main' if b['share'] >= par['mainShare'] else 'sub' if b['share'] >= par['secondaryShare'] else 'low'
        b['label'] = ('≤%gkg' % b['hi'] if b.get('merged') == 'low' and b['hi'] is not None else
                      '%gkg以上' % b['lo'] if b['hi'] is None else '%g–%gkg' % (b['lo'], b['hi']))
    if expand:
        out = []
        for b in bands:
            if b['cls'] != 'main':
                out.append(b)
                continue
            # A quarter-kg edge clips a 0.1kg column at its actual boundary.
            parts = defaultdict(list)
            for q in b['units']:
                parts[(q+1)//2].append(q)
            for u, qs in parts.items():
                n = sum(d[q][N] for q in qs)
                if not n:
                    continue
                lo, hi = (qs[0]-1)/20, qs[-1]/20 if qs[-1] < 120 else None
                out.append({'lo':lo,'hi':hi,'units':qs,'n':n,'share':n/tot,'cls':'main','fine':True,
                            'label': '%gkg以上' % lo if hi is None else '%g–%gkg' % (lo,hi)})
        bands = out
    for b in bands:
        b['avgW'] = sum(d[q][W] for q in b['units']) / (10000*b['n']) if b['n'] else None
    return bands


def reference(cp, np, units, np01=None):
    band = sum_units(np, units)
    out = {'n': band[N], 'coveredN': 0, 'avg': None, 'system': None}
    if not band[N]:
        return out
    np01 = np01 if np01 is not None else roll01(np)
    sums = [0.] * SIZE
    for q in units:
        c = cp.get(q)
        if not c or not c[N]:
            continue
        r = np01.get((q+1)//2)
        r = r if r and r[N] >= 5 else band
        out['coveredN'] += c[N]
        for k in (FEE, SYSTEM, DISP, TRAN, MF):
            sums[k] += c[N] * rate(r, k)
    if out['coveredN']:
        for k, name in ((FEE, 'avg'), (SYSTEM, 'system'), (DISP, 'disp'), (TRAN, 'tran'), (MF, 'mf')):
            out[name] = sums[k] / out['coveredN']
    return out


def reference_rates(net):
    # Fixed 0.5kg fallback, independent of the displayed smart bands.
    rates = {}
    for p, cp in net.items():
        micro = roll01(cp)
        bands = [sum_units(cp, range(b*10+1, b*10+11)) for b in range(12)]
        rates[p] = {u: (v if v[N] >= 5 else bands[(u-1)//5]) for u, v in micro.items()}
    return rates


def equivalent(cb, refs):
    n, actual, system = 0, 0., 0.
    for p, cp in cb.items():
        for q, v in cp.items():
            r = refs.get(p, {}).get((q+1)//2)
            if r and r[N]:
                n += v[N]
                actual += v[N] * rate(r)
                system += v[N] * rate(r, SYSTEM)
    return {'n':n,'avg':actual/n if n else None,'system':system/n if n else None}


def detect_steps(net):
    jumps = defaultdict(lambda: [0.,0])
    for cp in net.values():
        m = roll01(cp)
        for u in range(1,50):
            a, b = m.get(u), m.get(u+1)
            if a and b and a[N] >= 30 and b[N] >= 30:
                w = min(a[N], b[N]); jumps[u][0] += w * (rate(b,DISP)-rate(a,DISP)); jumps[u][1] += w
    return [{'step':u/10,'jump':float(Decimal(str(j/n)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)),
             'rawJump':j/n,'support':n,'source':'auto'} for u,(j,n) in sorted(jumps.items()) if n>=300 and j/n>=.1]


def critical(cb, net01, steps):
    detail = []
    for step in steps:
        u = round(step['step']*10)+1; n=0; saving=0.
        for p, cp in cb.items():
            x = sum_units(cp, (u*2-1,u*2))
            if not x[N]:
                continue
            hi, lo = net01.get(p,{}).get(u), net01.get(p,{}).get(u-1)
            jump = rate(hi,SYSTEM)-rate(lo,SYSTEM) if hi and lo and hi[N]>=10 and lo[N]>=10 else step['jump']
            n += x[N]; saving += x[N] * max(0,jump)
        detail.append({'step':step['step'],'n':n,'save':saving})
    return {'n':sum(x['n'] for x in detail),'save':sum(x['save'] for x in detail),'detail':detail}


def shape(cb, d, n, net_regions, province_order):
    weights = [sum(v[N] for v in d[b*10+1:b*10+11])/n for b in range(12)]
    flows = {p:sum(v[N] for v in cb[p].values())/n for p in sorted(cb,key=lambda p:province_order[p])}
    regions = defaultdict(float)
    for p, share in flows.items():
        regions[REGIONS.get(p,'其他')] += share
    top = max(range(12),key=lambda b:weights[b])
    if weights[top]>=.6:
        label = ('≤0.5kg' if top==0 else '%g–%gkg' % (top*.5,(top+1)*.5))+' 集中'
    else:
        bi = max(range(11),key=lambda b:weights[b]+weights[b+1])
        label = '%g–%gkg 双主要' % (bi*.5,(bi+2)*.5) if weights[bi]+weights[bi+1]>=.7 else '重量分散'
    ps = sorted(flows,key=lambda p:(-flows[p],province_order[p]))
    tilt = max(regions,key=lambda r:regions[r]-net_regions.get(r,0))
    flabel = re.sub('省|市|壮族自治区|回族自治区|维吾尔自治区|自治区','',ps[0])+'为主' if flows[ps[0]]>=.35 else (
        '偏'+tilt if regions[tilt]-net_regions.get(tilt,0)>=.06 else '流向均衡')
    return {'weightShares':weights,'flowShares':flows,'wlabel':label,'flabel':flabel,'key':label+' · '+flabel,'topProvinces':ps[:3]}


def customer_stats(name, cb, ctx):
    t, d = total(cb), dist(cb)
    eq = equivalent(cb,ctx['refs']); cr = critical(cb,ctx['net01'],ctx['steps'])
    bs = smart_bands(d,ctx['settings']['parameters'])
    gap = rate(t,SYSTEM)-eq['system'] if t[N] and eq['system'] is not None else None
    result = {'name':name, 'total':public(t),'avg':rate(t),'avgW':rate(t,W),'eq':eq['avg'],'eqSystem':eq['system'],
              'gap':gap,'critical':cr,'bands':bs,'main':' + '.join(b['label'] for b in bs if b['cls']=='main') or max(bs,key=lambda b:b['n'])['label']}
    result.update(shape(cb,d,t[N],ctx['netRegions'],ctx['provinceOrder']))
    result['tags'] = (['比网点贵'] if gap is not None and gap>=.03 else ['比网点便宜'] if gap is not None and gap<=-.03 else []) + (
        ['超档件多'] if cr['n']/t[N]>=.15 else [])
    return result


def build_data(cur, s):
    cubes, net = read_cubes(cur,s)
    nett = total(net)
    regions = defaultdict(float)
    for p, cp in net.items():
        regions[REGIONS.get(p,'其他')] += sum(v[N] for v in cp.values())/(nett[N] or 1)
    ctx = {'settings':s,'cubes':cubes,'net':net,'netTotal':nett,'net01':{p:roll01(cp) for p,cp in net.items()},
           'refs':reference_rates(net),'netRegions':regions,'stepsAuto':detect_steps(net)}
    ctx['provinceOrder']={p:i for i,p in enumerate(sorted(net,key=lambda p:(-sum(v[N] for v in net[p].values()),p)))}
    # Rule counts can overlap; the excluded count is their distinct union.
    where,args = rules.filter_sql(s['scope']); mask=rules.exclude_mask(s['scope']['excludeRules'])
    query = 'SELECT coalesce(customer,\'\'), count(*), count(*) FILTER (WHERE flags&%d<>0), ' % mask
    query += ','.join('count(*) FILTER (WHERE flags&%d<>0)' % r['bit'] for r in rules.RULES if r['bit'])
    query += ' FROM ship WHERE '+' AND '.join(where)+' GROUP BY 1'
    ctx['excluded'] = {r[0]:{'rawN':r[1],'n':r[2], 'rules':dict(zip([x['code'] for x in rules.RULES if x['bit']],r[3:]))}
                       for r in cur.execute(query,args).fetchall()}
    rq = "SELECT coalesce(customer,''),count(*),count(*) FILTER (WHERE flags&16<>0)," + ",".join([
        "count(*) FILTER (WHERE flags&16<>0 AND product LIKE '%拦截%' AND product LIKE '%退改%')",
        "count(*) FILTER (WHERE flags&16<>0 AND product LIKE '%拦截%' AND product NOT LIKE '%退改%')",
        "count(*) FILTER (WHERE flags&16<>0 AND product NOT LIKE '%拦截%')",
        "coalesce(sum(fee) FILTER (WHERE flags&16<>0),0)",
        "coalesce(sum(return_bill) FILTER (WHERE flags&16<>0),0)"])
    rq += ' FROM ship WHERE '+' AND '.join(where)+' GROUP BY 1'
    ctx['returns']={r[0]:dict(zip(('n','rn','n1','n2','n3','rfee','rbill'),[float(v) if isinstance(v,Decimal) else v for v in r[1:]])) for r in cur.execute(rq,args).fetchall()}
    col=rules.date_col(s['scope'])
    ctx['dateRange']=[str(x) if x else None for x in cur.execute('SELECT min('+col+'),max('+col+') FROM ship WHERE '+' AND '.join(where),args).fetchone()]
    sql, args = base_sql(s)
    ctx['activeDates'] = defaultdict(set)
    for name, day in cur.execute(sql+" SELECT coalesce(customer,''),send_day FROM s WHERE send_day IS NOT NULL GROUP BY 1,2",args).fetchall():
        ctx['activeDates'][name].add(day)
    return ctx


def build_context(cur, s, data):
    ctx={**data,'settings':s,'data':data}
    ctx['steps'] = s['parameters']['steps'] if s['parameters']['steps'] is not None else ctx['stepsAuto']
    stats = [customer_stats(name,cb,ctx) for name,cb in ctx['cubes'].items()]
    stats.sort(key=lambda x:(-x['total']['n'],x['name']))
    ctx['stats'] = {x['name']:x for x in stats}
    ctx['names'] = [x['name'] for x in stats]
    nr=simulation.returns(ctx,list(ctx['returns']),simulation.validate())
    for x in stats:
        r=simulation.returns(ctx,[x['name']],simulation.validate());x.update(returnRate=r['rate'],returnPer=r['per'])
        if nr['rate'] and r['rate']>=nr['rate']*1.5:x['tags'].append('退改偏高')
    groups = {}
    small = []
    for x in stats:
        if x['total']['n']<s['parameters']['minGroupN']:
            small.append(x['name']); continue
        g = groups.setdefault(x['key'],{'key':x['key'],'wlabel':x['wlabel'],'flabel':x['flabel'],'members':[],'n':0,'fee':0.})
        g['members'].append(x['name']); g['n']+=x['total']['n']; g['fee']+=x['total']['fee']
    ordered = sorted(groups.values(),key=lambda x:-x['n'])
    for g in ordered:
        g['distribution']=[v[N] for v in dist(combine(ctx['cubes'],g['members']),10)[1:]]
    ctx['clusters'] = {'groups':[g for g in ordered if len(g['members'])>1],
                       'unique':[g for g in ordered if len(g['members'])==1],
                       'smallCustomers':len(small),'smallN':sum(ctx['stats'][x]['total']['n'] for x in small),
                       'eligibleCustomers':len(stats)-len(small)}
    ctx['views'] = {}
    return ctx


def context(wh, req, cur, version):
    s = settings(wh,req,cur)
    key = (version,json.dumps(s,sort_keys=True,ensure_ascii=False))
    # Serialise first builds only; snapshot reads themselves never acquire the write lock.
    with _CACHE_LOCK:
        cache = getattr(wh,'_analysis_cache',None)
        if cache is None:
            wh._analysis_cache = cache = OrderedDict()
            wh._analysis_data = OrderedDict()
        if key not in cache:
            data_key=(version,json.dumps({k:v for k,v in s.items() if k!='parameters'},sort_keys=True,ensure_ascii=False))
            data_cache=wh._analysis_data
            if data_key not in data_cache:
                data_cache[data_key]=build_data(cur,s)
                while len(data_cache)>3:
                    data_cache.popitem(last=False)
            data_cache.move_to_end(data_key)
            cache[key] = build_context(cur,s,data_cache[data_key])
            while len(cache)>6:
                cache.popitem(last=False)
        cache.move_to_end(key)
        return cache[key]


def selection(req, ctx):
    names = req.get('customers','all')
    if names == 'all' or names == []:
        return list(ctx['names']), True
    if not isinstance(names,list) or not all(isinstance(x,str) for x in names):
        raise ValueError('客户须为名称列表或 all')
    names = list(dict.fromkeys(names))
    if len(names)>8:
        raise ValueError('最多选择 8 个客户')
    return names, False


def level_of(req):
    level = req.get('level','province')
    if level not in ('province','city','zone'):
        raise ValueError('流向只支持区域、省或市')
    return level


def level_cubes(ctx,cur,level,names=None,all_view=False):
    if level=='province':
        return ctx['cubes'],ctx['net']
    if level=='zone':
        cfg=ctx['settings']['regionGroups']
        return {n:regions.cube(cb,cfg) for n,cb in ctx['cubes'].items()},regions.cube(ctx['net'],cfg)
    with _CACHE_LOCK:
        data=ctx['data']
        if 'cityNet' not in data:
            _,data['cityNet']=read_cubes(cur,ctx['settings'],'city',network_only=True)
            data['cityCubes']={}
            data['cityLoaded']=set()
        missing=[n for n in (names or []) if n not in data['cityLoaded']]
        if not all_view and missing:
            selected,_=read_cubes(cur,ctx['settings'],'city',customers=missing)
            data['cityCubes'].update(selected)
            data['cityLoaded'].update(missing)
    return data['cityCubes'],data['cityNet']


def excluded(ctx,names):
    rows = [ctx['excluded'][x] for x in names if x in ctx['excluded']]
    return {'n':sum(x['n'] for x in rows),'rawN':sum(x['rawN'] for x in rows),
            'rules':{r['code']:sum(x['rules'][r['code']] for x in rows) for r in rules.RULES if r['bit']}}


def similarity(a,b):
    return .5*sum(min(x,y) for x,y in zip(a['weightShares'],b['weightShares'])) + .5*sum(min(v,b['flowShares'].get(p,0)) for p,v in a['flowShares'].items())


def similar_public(s):
    """Only the fields rendered by the five-row similar-customer table."""
    return {'name':s['name'],'key':s['key'],'topProvinces':s['topProvinces'],
            'n':s['total']['n'],'avg':s['avg'],'gap':s['gap'],'criticalN':s['critical']['n']}


def cell_value(cp,np,units,all_n,np01=None):
    v = sum_units(cp,units); r = reference(cp,np,units,np01)
    deviation = rate(v,SYSTEM)/r['system']-1 if v[N]>=30 and r['n']>=30 and r['system'] else None
    cls = 'small' if v[N]<30 else 'n0' if deviation is None else 'g2' if deviation<=-.05 else 'g1' if deviation<=-.02 else 'n0' if deviation<.02 else 'r1' if deviation<.05 else 'r2'
    return {**public(v),'share':v[N]/all_n if all_n else 0,'ref':r,'deviation':deviation,'comparisonClass':cls,
            'badge':'up' if deviation is not None and deviation>=.02 else 'down' if deviation is not None and deviation<=-.02 else None}


def heat(cb,net,bands,all_n,show_all=False,all_view=False):
    rows=[]
    for p,cp in cb.items():
        np=net.get(p,{}); np01=roll01(np)
        cells=[cell_value(cp,np,b['units'],all_n,np01) for b in bands]
        rows.append({'flow':p,'cells':cells,'total':public(total({p:cp}))})
    rows.sort(key=lambda r:(-r['total']['n'],r['flow']))
    avgs=sorted(c['avg'] for r in rows for c in r['cells'] if c['n']>=30)
    cuts=[avgs[min(len(avgs)-1,math.floor(q*len(avgs)))] if avgs else None for q in (.2,.4,.6,.8)]
    for r in rows:
        for c in r['cells']:
            c['costClass']='empty' if not c['n'] else 'small' if c['n']<30 else 's%d' % (1+sum(c['avg']>=v for v in cuts))
            if all_view:
                c.update(deviation=None,badge=None,comparisonClass='small' if c['n']<30 else 'n0')
    # Aggregation rows deliberately carry no comparison color (same as prototype).
    def aggregate(rs,label):
        cols=[]
        for i,b in enumerate(bands):
            vals=[r['cells'][i] for r in rs]
            n=sum(x['n'] for x in vals)
            sums={k:float(sum(Decimal(str(x[k])) for x in vals)) for k in FIELDS[1:]}
            cols.append({'n':n,**sums,'avg':sums['fee']/n if n else None,'avgW':sums['w']/n if n else None,'share':n/all_n if all_n else 0})
        n=sum(c['n'] for c in cols); fee=sum(c['fee'] for c in cols)
        return {'flow':label,'cells':cols,'total':{'n':n,'fee':fee,'avg':fee/n if n else None,'avgW':sum(c['w'] for c in cols)/n if n else None},'members':[r['flow'] for r in rs]}
    return {'rows':rows if show_all else rows[:12], 'rowCount':len(rows),'cuts':cuts,
            'rest':aggregate(rows[12:],'其余') if not show_all and len(rows)>12 else None,
            'total':aggregate(rows,'全部流向'),'bands':bands}


def heat_remainder(rows,bands,n):
    if not rows:return None
    cells=[]
    for i,b in enumerate(bands):
        vs=[r['cells'][i] for r in rows];count=sum(v['n'] for v in vs)
        sums={k:math.fsum(v.get(k,0) for v in vs) for k in FIELDS[1:]}
        cells.append({'n':count,**sums,'avg':sums['fee']/count if count else None,'avgW':sums['w']/count if count else None,'share':count/n if n else 0})
    count=sum(c['n'] for c in cells);fee=math.fsum(c['fee'] for c in cells)
    return {'flow':'其余','members':[r['flow'] for r in rows],'cells':cells,'total':{'n':count,'fee':fee,'avg':fee/count if count else None,'avgW':math.fsum(c['w'] for c in cells)/count if count else None}}


def _decompose(a,b,ctx):
    refs=ctx['refs']
    def scan(cb):
        n=0; fee=0; actual=0; e=0.; by_u=defaultdict(int); ru=defaultdict(float)
        for p,cp in cb.items():
            for q,v in cp.items():
                u=(q+1)//2; r=refs.get(p,{}).get(u)
                if not r or not r[N]:
                    continue
                rr=rate(r,SYSTEM); n+=v[N]; fee+=v[SYSTEM]; actual+=v[FEE]; e+=v[N]*rr
                by_u[u]+=v[N];ru[u]+=v[N]*rr
        return n,fee/(10000*n) if n else None,actual/(10000*n) if n else None,e/n if n else None,by_u,ru
    an,aa,acta,ea,au,ar=scan(a); bn,ba,actb,eb,bu,br=scan(b)
    if not an or not bn:
        return {'avgA':acta,'avgB':actb,'weight':None,'flow':None,'price':None,'adjustment':None,'delta':None,'coveredA':an,'coveredB':bn}
    nd=dist(ctx['net'],10)
    mix=sum(n/an*(br[u]/bu[u] if bu.get(u) else (rate(nd[u],SYSTEM) or 0)) for u,n in au.items())
    return {'avgA':acta,'avgB':actb,'systemA':aa,'systemB':ba,'weight':mix-eb,'flow':ea-mix,'price':(aa-ea)-(ba-eb),
            'adjustment':(acta-actb)-(aa-ba),'delta':acta-actb,'coveredA':an,'coveredB':bn}


def decompose(wh,req):
    with wh.snapshot() as (cur,version):
        ctx=context(wh,req,cur,version)
        a,b=req.get('a'),req.get('b','network')
        if not isinstance(a,str) or a not in ctx['cubes'] or (b!='network' and b not in ctx['cubes']):
            raise ValueError('请选择当前范围内有数据的客户，B 也可为 network')
        return {'version':version,'scope':ctx['settings']['scope'],'a':a,'b':b,**_decompose(ctx['cubes'][a],ctx['net'] if b=='network' else ctx['cubes'][b],ctx)}


def analysis(wh,req):
    t0=time.perf_counter(); timings={}
    def phase(name):
        now=time.perf_counter();timings[name]=round((now-phase.last)*1000,2);phase.last=now
    phase.last=t0
    with wh.snapshot() as (cur,version):
        ctx=context(wh,req,cur,version); names,all_view=selection(req,ctx); level=level_of(req)
        phase('context')
        compare_band=integer(req['compareBand'],'对比重量段',0,11) if req.get('compareBand') is not None else None
        cache_key=(tuple(names),all_view,level,bool(req.get('expand')),bool(req.get('showAll')),compare_band)
        # Immutable cached results: callers must never mutate returned structures.
        with _CACHE_LOCK:
            result=ctx['views'].get(cache_key)
        if result is None:
            cb=ctx['net'] if all_view else combine(ctx['cubes'],names)
            t=total(cb); d=dist(cb); d01=dist(cb,10)
            eq=equivalent(cb,ctx['refs']); cr=critical(cb,ctx['net01'],ctx['steps'])
            bands=smart_bands(d,ctx['settings']['parameters'],bool(req.get('expand')) and level=='province')
            cubes,net=level_cubes(ctx,cur,level,names,all_view)
            heat_cb=net if all_view else combine(cubes,names)
            h=heat(heat_cb,net,bands,t[N],True,all_view)
            ex=excluded(ctx,list(ctx['excluded']) if all_view else names)
            gap=rate(t,SYSTEM)-eq['system'] if t[N] and eq['system'] is not None and not all_view else None
            mode='all' if all_view else 'single' if len(names)==1 else 'multi'
            result={'version':version,'mode':mode,'customers':names,'level':level,'scope':ctx['settings']['scope'],
                    'settings':ctx['settings'],'overview':{**public(t),'eq':None if all_view else eq['avg'],'eqSystem':None if all_view else eq['system'],
                                                        'gap':gap,'critical':cr,'customerCount':len(ctx['names']) if all_view else len(names)},
                    'excluded':ex,'network':public(ctx['netTotal']),'steps':ctx['steps'],'stepsAuto':ctx['stepsAuto'],
                    'distribution':[dict(u=u,**public(v)) for u,v in enumerate(d01) if u],
                    'networkDistribution':[dict(u=u,**public(v)) for u,v in enumerate(dist(ctx['net'],10)) if u],
                    'bands':bands,'baseBands':smart_bands(d,ctx['settings']['parameters']),'heat':h,'insights':{'mainBands':[b for b in bands if b['cls']=='main'],
                        'largestStep':max(cr['detail'],key=lambda x:x['save'],default=None),'systemGap':gap,'excludedN':ex['n']},
                    'comparison':[], 'similar':[], 'cluster':None}
            if mode=='all':
                result.update(clusters=ctx['clusters'],ranking=list(ctx['stats'].values()))
            else:
                result['comparison']=[ctx['stats'][x] for x in names if x in ctx['stats']]
                if mode=='single' and names[0] in ctx['stats']:
                    st=ctx['stats'][names[0]]
                    similar=[{'customer':similar_public(s),'similarity':similarity(st,s)} for n,s in ctx['stats'].items() if n!=names[0] and s['total']['n']>=ctx['settings']['parameters']['minGroupN']]
                    result['similar']=sorted(similar,key=lambda x:-x['similarity'])[:5]
                    result['cluster']=next((g for g in ctx['clusters']['groups']+ctx['clusters']['unique'] if g['key']==st['key']),None)
                if mode=='multi':
                    active=[n for n in names if n in ctx['stats']]
                    result['comparison']=[{**ctx['stats'][n],'distribution':[{'u':u,'n':v[N],'fee':v[FEE]/10000} for u,v in enumerate(dist(ctx['cubes'][n],10)) if u]} for n in active]
                    result['decomposition']={'a':active[0],'b':'network',**_decompose(ctx['cubes'][active[0]],ctx['net'],ctx)} if active else None
                    result['flowStructure']=[{'name':n,'shares':ctx['stats'][n]['flowShares']} for n in active]
                    fixed=[{'lo':b*.5,'hi':(b+1)*.5 if b<11 else None,'units':list(range(b*10+1,b*10+11)),
                            'label':('%g–%gkg'%(b*.5,(b+1)*.5)) if b<11 else '5.5kg以上'} for b in range(12)]
                    bi=compare_band if compare_band is not None else max(range(12),key=lambda b:sum(d[q][N] for q in fixed[b]['units']))
                    result.update(sameBands=fixed,sameBand=bi)
                    result['sameCells']=[{'name':n,'heat':heat(ctx['cubes'][n],ctx['net'],[fixed[bi]],ctx['stats'][n]['total']['n'],True)} for n in active]
                    result['sameNetwork']=heat(ctx['net'],ctx['net'],[fixed[bi]],ctx['netTotal'][N],True,True)
            with _CACHE_LOCK:
                ctx['views'][cache_key]=result
                if len(ctx['views'])>24:
                    ctx['views'].pop(next(iter(ctx['views'])))
        phase('view')
        cb=ctx['net'] if all_view else combine(ctx['cubes'],names)
        cubes,net=level_cubes(ctx,cur,level,names,all_view)
        result=simulation.apply(result,ctx,cb,net if all_view else combine(cubes,names),simulation.state(wh,req,cur))
        phase('simulation')
        result['customerIndex']=[{'name':s['name'],'normalN':s['total']['n'],'cluster':s['key']} for s in ctx['stats'].values()]
        history.attach(wh,cur,version,req,ctx,result,names,all_view)
        phase('history')
        full=context(wh,{**req,'excludeZones':[]},cur,version) if ctx['settings']['scope']['excludeZones'] else ctx
        cfg=ctx['settings']['regionGroups'];off=ctx['settings']['scope']['excludeZones']
        result['zones']=regions.summary(full,names,all_view,cfg,off)
        result['scope']={**result['scope'],'zoneExcluded':result['zones']['excluded']}
        if level=='zone':
            ordered={g['name']:i for i,g in enumerate(regions.groups(cfg))}
            result['heat']['rows'].sort(key=lambda r:ordered[r['flow']])
            ph=simulation.heat(heat(cb,ctx['net'],result['bands'],result['overview']['n'],True,all_view),cb,result['simulation']['items'],result['simulation']['constants'],(result['aftersales']['per'] or 0) if result['simulation']['items']['addon']['on'] else 0)
            for row in result['heat']['rows']:
                row['provinces']=[p for p in ph['rows'] if regions.zone_of(p['flow'],cfg)==row['flow']]
        result['heat']['allRows']=result['heat']['rows']
        result['heat']['topRest']=heat_remainder(result['heat']['allRows'][12:],result['bands'],result['overview']['n']) if level!='zone' else None
        if level!='zone' and not req.get('showAll'):
            result['heat']['rows']=result['heat']['allRows'][:12]
            # Aggregate displayed remainder from the exact simulated cell sums.
            result['heat']['rest']=heat_remainder(result['heat']['allRows'][12:],result['bands'],result['overview']['n'])
        phase('zonesAndHeat')
        from . import reports
        result={**result,'timingsMs':timings,'elapsedMs':round((time.perf_counter()-t0)*1000,2)}
        return reports.remember(wh,result)



def cell(wh,req):
    with wh.snapshot() as (cur,version):
        ctx=context(wh,req,cur,version);names,all_view=selection(req,ctx);level=level_of(req)
        if level=='zone':raise ValueError('请展开区域后选择省份明细')
        band=req.get('band') or {};lo=rules.finite(band.get('lo',0),'重量下限')
        hi=rules.finite(band['hi'],'重量上限') if band.get('hi') is not None else None
        if lo<0 or lo>=6 or (hi is not None and (hi<=lo or hi>=6)) or abs(lo*20-round(lo*20))>1e-8 or (hi is not None and abs(hi*20-round(hi*20))>1e-8):
            raise ValueError('格子范围须为 0.05kg 刻度，末段上限用 null')
        us=list(range(round(lo*20)+1,round(hi*20)+1 if hi is not None else 121))
        flows=req.get('flows',[req.get('flow')])
        if not isinstance(flows,list) or not flows or not all(isinstance(x,str) for x in flows):
            raise ValueError('请选择流向')
        flows=list(dict.fromkeys(flows))
        cubes,net=level_cubes(ctx,cur,level,names,all_view); cb=net if all_view else combine(cubes,names)
        # Preserve per-flow references when aggregating several destinations.
        scope_n=total(cb)[N]
        vals=[cell_value(cb.get(p,{}),net.get(p,{}),us,scope_n) for p in flows]
        agg=sumv(sum_units(cb.get(p,{}),us) for p in flows)
        covered=sum(v['ref']['coveredN'] for v in vals)
        ref={'n':sum(v['ref']['n'] for v in vals),'coveredN':covered}
        for k in ('avg','system','disp','tran','mf'):
            ref[k]=sum(v['ref'].get(k,0)*v['ref']['coveredN'] for v in vals if v['ref']['coveredN'])/covered if covered else None
        cities=[]
        if level=='province':
            city_cubes,city_net=level_cubes(ctx,cur,'city',names,all_view)
            cc=city_net if all_view else combine(city_cubes,names)
            for p,cp in cc.items():
                if p.split('/',1)[0] in flows:
                    v=cell_value(cp,city_net.get(p,{}),us,agg[N])
                    if v['n']:
                        cities.append({'flow':p,**v})
            cities.sort(key=lambda x:(-x['n'],x['flow']))
        sql,args=base_sql(ctx['settings'])
        flow_expr="coalesce(province,'')" if level=='province' else "coalesce(province,'')||'/'||coalesce(city,'')"
        cond=flow_expr+' IN ('+','.join('?' for _ in flows)+') AND '+U05+' BETWEEN ? AND ?'
        args+=flows+[us[0],us[-1]]
        if not all_view:
            cond+=' AND customer IN ('+','.join('?' for _ in names)+')';args+=names
        page=integer(req.get('page',1),'页码',1);limit=integer(req.get('pageSize',50),'每页条数',1,200)
        rows=cur.execute(sql+' SELECT id,waybill,customer,province,city,send_day,day,weight,fee,actual,dispatch,transit,waybill_fee,sub,extra,flags,subsidy FROM s WHERE '+cond+' ORDER BY id LIMIT ? OFFSET ?',args+[limit,(page-1)*limit]).fetchall()
        keys=('id','waybill','customer','province','city','sendDay','handoverDay','weight','system','actual','dispatch','transit','waybillFee','subsidy','adjustment','flags','subsidyType')
        out_rows=[{k:float(v) if isinstance(v,Decimal) else v.isoformat() if hasattr(v,'isoformat') else v for k,v in zip(keys,row)} for row in rows]
        sm=simulation.state(wh,req,cur);K=simulation.constants(ctx,sm)
        ret=simulation.returns(ctx,list(ctx['returns']) if all_view else names,sm)
        addon=(ret['per'] or 0) if sm['addon']['on'] else 0
        eff=simulation.effects({p:cb.get(p,{}) for p in flows},sm,K,addon,us)
        for x in out_rows:
            u=max(1,math.ceil((x['weight'] or 0)*10-1e-9))
            delta=sum(simulation.per(k,u,sm,K) for k in sm)+addon
            if sm['subsidy']['on'] and x['subsidyType']=='一客一价':delta-=sm['subsidy']['v']
            x['actual']=(x['system'] or 0)+delta
        for city in cities:
            city.update(simulation.adjusted(city,simulation.total_effect({city['flow']:cc[city['flow']]},sm,K,addon,us)))
        return {'simulationLines':[{'key':k,'name':simulation.LABELS[k],'total':eff[k],'per':eff[k]/agg[N] if agg[N] else None} for k in sm if sm[k]['on'] and k!='service'],
                'version':version,'scope':ctx['settings']['scope'],'level':level,'flows':flows,'band':{'lo':lo,'hi':hi},'total':simulation.adjusted(public(agg),sum(eff.values())),'ref':ref,
                'gap':rate(agg,SYSTEM)-ref['system'] if agg[N] and ref['system'] is not None else None,
                'cities':cities,'rows':out_rows,'page':page,'pageSize':limit,'totalRows':agg[N],
                'quoteScope':{'customers':'all' if all_view else names,'flows':flows,'band':{'lo':lo,'hi':hi},**ctx['settings']['scope']}}


def meta(wh, req=None):
    """Metadata and import groups for the three-page application."""
    from .analytics import _meta
    with wh.snapshot() as (cur,version):
        ctx=context(wh,req or {},cur,version)
        result=_meta(wh,cur,version)
        for c in result['customers']:
            stat=ctx['stats'].get(c['name'])
            c.update(normalN=stat['total']['n'] if stat else 0,cluster=stat['key'] if stat else None)
        from .version import VERSION, RELEASED
        result.update(version=VERSION,released=RELEASED,dataVersion=version)
        result['features']['handoverBusinessDay']=True
        result['features']['historyV32']=True
        result['features']['analysisV3']=True
        result['features']['simulationV31']=True
        result['analysisScope']=ctx['settings']['scope']
        result['analysisConfig']=ctx['settings']['parameters']
        result['normalN']=ctx['netTotal'][N]
        result['excluded']=excluded(ctx,list(ctx['excluded']))
        result['steps']=ctx['steps']
        from . import imports
        result['lastImport']=imports.latest(cur)
        result['importGroups']=imports.listing(wh,cur)['groups']
        return result
