"""Opt-in cost scenarios. Bill comparisons and source data remain unchanged."""
import copy
import datetime as dt
import math
from . import rules

DEFAULT = {
    'subsidy': {'on':False,'v':.2}, 'operation': {'on':False,'v':.35},
    'management': {'on':False,'v':0}, 'puhui': {'on':False,'v':1.044},
    'w115': {'on':False,'v':.4}, 'w13': {'on':False,'base':0,'v':.35},
    'tongpiao': {'on':False,'v':.1}, 'heavy': {'on':False,'base':0,'rate':1,'share':.4},
    'idle': {'on':False,'target':0,'v':.5}, 'custom': {'on':False,'v':.8},
    'service': {'on':False,'v':2}, 'addon': {'on':False},
}
LABELS = dict(zip(DEFAULT, ['一客一价补贴','操作成本','管理成本','新普惠返利','1–1.5kg 补贴',
    '1–3kg 超基数补贴','通票扣减','重货考核','市场闲置费','返利（手工）','售后客服成本','退改附加成本摊入客户成本']))


def validate(value=None):
    if value is not None and not isinstance(value,dict): raise ValueError('成本模拟须为对象')
    value=value or {}
    if set(value)-set(DEFAULT): raise ValueError('未知成本模拟项')
    out=copy.deepcopy(DEFAULT)
    for k,item in value.items():
        if not isinstance(item,dict) or set(item)-set(DEFAULT[k]): raise ValueError('模拟参数不正确：'+k)
        out[k].update(item)
        if not isinstance(out[k]['on'],bool): raise ValueError('模拟开关须为布尔值')
        for f,v in out[k].items():
            if f=='on':continue
            n=rules.finite(v,LABELS[k])
            if n<0 or n>1e12 or (f=='share' and n>1):raise ValueError('模拟参数超出范围：'+LABELS[k])
            out[k][f]=n
    return out


def migrated(cfg):
    """Keep original values; migration never enables a charge automatically."""
    out=validate()
    policy=next((p for p in cfg.get('subsidyPolicies',[]) if p['type']=='一客一价'),None)
    if policy:out['subsidy']['v']=policy['perTicket']
    groups={'operation':[],'management':[],'custom':[]}
    for a in cfg.get('adjustments',[]):
        key='custom' if a.get('sign')==-1 or '返利' in a.get('name','') else 'management' if '管理' in a.get('name','') or a.get('id')=='management' else 'operation' if '操作' in a.get('name','') or a.get('id')=='operation' else None
        if key:groups[key].append(a['amount'])
    for k,values in groups.items():
        if values:out[k]['v']=sum(values)
    return validate(out)


def state(wh,req,cur):
    return validate(req.get('simulation',wh.config(cur).get('simulation')))


def returns(ctx,names,sm):
    keys=('n','rn','n1','n2','n3','rfee','rbill')
    rs=[ctx['returns'][n] for n in names if n in ctx['returns']]
    out={k:math.fsum(r[k] for r in rs) if k in ('rfee','rbill') else sum(r[k] for r in rs) for k in keys}
    out['normalN']=sum(ctx['stats'][n]['total']['n'] for n in names if n in ctx['stats'])
    out['service']=sm['service']['v']*out['rn'] if sm['service']['on'] else 0
    out['total']=out['rfee']+out['rbill']+out['service']
    out['rate']=out['rn']/out['n'] if out['n'] else 0
    out['per']=out['total']/out['normalN'] if out['normalN'] else None
    return out


def constants(ctx,sm):
    from . import analysis as a
    d=a.dist(ctx['net'],10);n=ctx['netTotal'][a.N]
    n13=sum(v[a.N] for v in d[11:31]);n3=sum(v[a.N] for v in d[31:])
    w3=sum(v[a.W] for v in d[31:])/10000;t3=sum(v[a.TRAN] for v in d[31:])/10000
    h=sm['heavy'];amt=-(h['base']-w3)*h['rate'] if w3<h['base'] else (w3-h['base'])*(t3/w3 if w3 else 0)*h['share']
    start,end=ctx['settings']['scope'].get('start'),ctx['settings']['scope'].get('end')
    start=start or ctx['dateRange'][0];end=end or ctx['dateRange'][1]
    days=(dt.date.fromisoformat(end)-dt.date.fromisoformat(start)).days+1 if start and end else 0
    daily=n/days if days else 0
    tiers=[(2,.31),(4,.305),(6,.29),(8,.285),(10,.28),(12,.275),(14,.27),(16,.265),(None,.26)]
    i=next(i for i,(hi,_) in enumerate(tiers) if hi is None or daily<=hi*10000)
    hi,ratio=tiers[i]
    return {'n':n,'n13':n13,'n3':n3,'w3':w3,'t3':t3,'heavyAmount':amt,'heavy':-amt/n3 if n3 else 0,
            'w13':max(0,n13-sm['w13']['base'])*sm['w13']['v']/n13 if n13 else 0,
            'idle':max(0,sm['idle']['target']-n)*sm['idle']['v']/n if n else 0,
            'days':days,'daily':daily,'tier':{'lo':i*2,'hi':hi,'ratio':ratio,'value':round((1-ratio)*1.45,4)}}


def per(k,u,sm,K):
    if not sm[k]['on']:return 0
    if k in ('operation','management','tongpiao'):return sm[k]['v']
    if k in ('puhui','custom'):return -sm[k]['v']
    if k=='w115':return -sm[k]['v'] if 11<=u<=15 else 0
    if k=='w13':return -K[k] if 11<=u<=30 else 0
    if k=='heavy':return K[k] if u>=31 else 0
    if k=='idle':return K[k]
    return 0


def effects(cb,sm,K,addon=0,units=None):
    from . import analysis as a
    out={k:[] for k in DEFAULT}
    for cp in cb.values():
        for q,v in cp.items():
            if units is not None and q not in units:continue
            for k in DEFAULT:out[k].append(v[a.N]*per(k,(q+1)//2,sm,K))
            if sm['subsidy']['on']:out['subsidy'].append(-v[a.SUBN]/10000*sm['subsidy']['v'])
            out['addon'].append(addon*v[a.N])
    return {k:math.fsum(v) for k,v in out.items()}


def total_effect(cb,sm,K,addon=0,units=None):
    return math.fsum(effects(cb,sm,K,addon,units).values())


def adjusted(p,amount):
    """A fresh public value. All comparison fields continue to refer to bill costs."""
    return {**p,'fee':p['system']+amount,'avg':(p['system']+amount)/p['n'] if p['n'] else None,'simulation':amount}


def heat(h,cb,sm,K,addon):
    out=copy.deepcopy(h)
    for r in out['rows']+[x for x in (out['rest'],out['total']) if x]:
        rc={p:cb[p] for p in r.get('members',[r['flow']]) if p in cb}
        for i,b in enumerate(out['bands']):r['cells'][i]=adjusted(r['cells'][i],total_effect(rc,sm,K,addon,b['units']))
        r['total']['fee']=sum(c['fee'] for c in r['cells']);r['total']['avg']=r['total']['fee']/r['total']['n'] if r['total']['n'] else None
    # Cost colors use simulated prices; comparison colors/badges keep their bill values.
    avgs=sorted((p['system']+total_effect({flow:cp},sm,K,addon,b['units']))/p['n'] for flow,cp in cb.items() for b in out['bands'] if (p:=_band(cp,b))['n']>=30)
    out['cuts']=[avgs[min(len(avgs)-1,int(q*len(avgs)))] if avgs else None for q in (.2,.4,.6,.8)]
    for r in out['rows']:
        for c in r['cells']:c['costClass']='empty' if not c['n'] else 'small' if c['n']<30 else 's%d'%(1+sum(c['avg']>=v for v in out['cuts']))
    return out


def _band(cp,b):
    from . import analysis as a
    return a.public(a.sum_units(cp,b['units']))


def apply(result,ctx,cb,heat_cb,sm):
    from . import analysis as a
    r=copy.deepcopy(result);K=constants(ctx,sm)
    names=list(ctx['returns']) if r['mode']=='all' else r['customers']
    ret=returns(ctx,names,sm);netret=returns(ctx,list(ctx['returns']),validate())
    addon=(ret['per'] or 0) if sm['addon']['on'] else 0
    eff=effects(cb,sm,K,addon);amount=math.fsum(eff.values())
    r['overview']=adjusted(r['overview'],amount)
    t=r['overview'];t['eq']=t['eqSystem']+amount/t['n'] if t['eqSystem'] is not None and t['n'] else None
    r['heat']=heat(r['heat'],heat_cb,sm,K,addon)
    for p in r['distribution']:
        v=total_effect(cb,sm,K,addon,(p['u']*2-1,p['u']*2));p.update(adjusted(p,v))
    eff['service']=ret['service']
    r['simulation']={'items':sm,'count':sum(x['on'] for x in sm.values()),'constants':K,'effects':eff,
                     'adjustment':amount,'per':amount/t['n'] if t['n'] else None}
    group=r.get('cluster');r['aftersales']={**ret,'network':netret,'group':returns(ctx,group['members'],validate()) if group else None}
    # Table unit-cost columns intentionally stay at bill values.
    for s in r.get('ranking',[])+r.get('comparison',[]):
        ar=returns(ctx,[s['name']],sm);s.update(returnRate=ar['rate'],returnPer=ar['per'])
    r['settings']['simulation']=sm
    return r


def sql(sm,K):
    """Same per-weight rules as the cube path, without per-ticket rounding."""
    parts=[];u='ceil(weight*10-1e-9)'
    for k in DEFAULT:
        if not sm[k]['on'] or k in ('service','addon','subsidy'):continue
        if k=='w115':parts.append('CASE WHEN '+u+' BETWEEN 11 AND 15 THEN '+repr(-sm[k]['v'])+' ELSE 0 END')
        elif k=='w13':parts.append('CASE WHEN '+u+' BETWEEN 11 AND 30 THEN '+repr(-K[k])+' ELSE 0 END')
        elif k=='heavy':parts.append('CASE WHEN '+u+'>=31 THEN '+repr(K[k])+' ELSE 0 END')
        else:parts.append(repr(per(k,1,sm,K)))
    if sm['subsidy']['on']:parts.append("CASE WHEN subsidy='一客一价' THEN %r ELSE 0 END"%(-sm['subsidy']['v']))
    return ' + '.join('('+p+')' for p in parts) or '0'
