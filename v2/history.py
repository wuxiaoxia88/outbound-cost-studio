"""Bill-only history and daily denominators. Never uses simulation parameters."""
from calendar import monthrange
from collections import OrderedDict, defaultdict
from datetime import date, timedelta
from threading import RLock
from . import rules

MIN_PREV = 300
_LOCK = RLock()


def bounds(start, end):
    """Inclusive current range and the immediately preceding comparable range."""
    if not start or not end:
        return None
    a, b = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    days = (b-a).days+1
    if days <= 0:
        raise ValueError('结束日期不能早于开始日期')
    full = a.day == 1 and a.year == b.year and a.month == b.month and b.day == monthrange(b.year,b.month)[1]
    pb = a-timedelta(days=1)
    pa = pb.replace(day=1) if full else a-timedelta(days=days)
    return {'start':str(a),'end':str(b),'days':days,'previousStart':str(pa),'previousEnd':str(pb),
            'label':f'{pb.month} 月' if full else f'上期（{pa:%m/%d}–{pb:%m/%d}）','fullMonth':full}


def daily(n, names, dates, days, all_view=False):
    active = set().union(*(dates.get(x,set()) for x in names)) if names else set()
    divisor = days if all_view else len(active)
    return {'daily':n/divisor if divisor else None,'activeDays':len(active),'calendarDays':days}


def change(current, previous):
    if previous['n'] < MIN_PREV:
        return None
    delta = {}
    for k in ('n','daily','avgW','avg','criticalShare','mainShare'):
        a,b = current.get(k),previous.get(k)
        delta[k] = None if a is None or b is None else ((a/b-1) if b else None) if k in ('n','daily') else a-b
    return delta


def _rows(cur, scope, steps):
    """One monthly/period aggregation, exact sums; date sets retain union semantics."""
    where,args=rules.filter_sql(scope)
    where.append('(flags & %d)=0'%rules.exclude_mask(scope['excludeRules']))
    crit = ' OR '.join('(weight>? AND weight<=?)' for _ in steps) or 'FALSE'
    critargs=[v for s in steps for v in (s['step'],round(s['step']+.1,8))]
    sql = "SELECT coalesce(customer,''),count(*),coalesce(sum(weight),0),coalesce(sum(fee),0),count(*) FILTER (WHERE "+crit+") FROM ship WHERE "+' AND '.join(where)+' GROUP BY 1'
    stats={n:{'n':cnt,'w':float(w),'fee':float(f),'criticalN':cn} for n,cnt,w,f,cn in cur.execute(sql,critargs+args).fetchall()}
    dates=defaultdict(set)
    for n,d in cur.execute("SELECT coalesce(customer,''),send_day FROM ship WHERE "+' AND '.join(where)+' AND send_day IS NOT NULL GROUP BY 1,2',args).fetchall():dates[n].add(d)
    bins=defaultdict(lambda:[0]*12)
    for n,u,c in cur.execute("SELECT coalesce(customer,''),least(greatest(cast(ceil(coalesce(weight,0)*2-1e-9) AS INTEGER),1),12),count(*) FROM ship WHERE "+' AND '.join(where)+' GROUP BY 1,2',args).fetchall():bins[n][u-1]=c
    return stats,dict(dates),dict(bins)


def _summary(data,names,all_view,days,main):
    stats,dates,bins=data
    names=list(stats) if all_view else names
    result={k:sum(stats.get(n,{}).get(k,0) for n in names) for k in ('n','w','fee','criticalN')}
    n=result['n'];ds=[sum(bins.get(x,[0]*12)[i] for x in names) for i in range(12)]
    result.update(avg=result['fee']/n if n else None,avgW=result['w']/n if n else None,
                  criticalShare=result['criticalN']/n if n else None,mainShare=ds[main]/n if n else None,
                  distribution=[v/n if n else 0 for v in ds],**daily(n,names,dates,days,all_view))
    return result


def attach(wh,cur,version,req,ctx,result,names,all_view):
    scope=ctx['settings']['scope'];pr=bounds(scope.get('start') or ctx['dateRange'][0],scope.get('end') or ctx['dateRange'][1])
    days=pr['days'] if pr else 0
    result['overview'].update(daily(result['overview']['n'],names,ctx['activeDates'],days,all_view))
    for s in result.get('ranking',[])+result.get('comparison',[]):s.update(daily(s['total']['n'],[s['name']],ctx['activeDates'],days))
    result['previous']={'enabled':bool(req.get('comparePrevious')), 'range':pr,'minimum':MIN_PREV}
    if not pr:
        result['history']={'months':[],'delta':None};return
    anchor=pr['end'][:7]; steps=ctx['steps']; mask=rules.exclude_mask(scope['excludeRules'])
    # Cache period aggregates independent of customer selection or simulation.
    def read(sc):
        key=(version,sc.get('dateBasis'),sc['start'],sc['end'],mask,tuple(s['step'] for s in steps))
        with _LOCK:
            cache=getattr(wh,'_history_cache',None)
            if cache is None:wh._history_cache=cache=OrderedDict()
            if key not in cache:
                cache[key]=_rows(cur,sc,steps)
                while len(cache)>12:cache.popitem(last=False)
            cache.move_to_end(key)
            return cache[key]
    # Always anchored to the selected end month; no future months in a September view.
    month_where = "state=1 AND (flags & %d)=0 AND send_day IS NOT NULL AND strftime(send_day,'%%Y-%%m')<=?" % mask
    month_args = [anchor]
    if not all_view:
        month_where += " AND coalesce(customer,'') IN ("+','.join('?' for _ in names)+')' if names else ' AND FALSE'
        month_args += names
    months=[r[0] for r in cur.execute("SELECT DISTINCT strftime(send_day,'%Y-%m') FROM ship WHERE "+month_where+" ORDER BY 1 DESC LIMIT 6",month_args).fetchall()][::-1]
    first=date.fromisoformat(anchor+'-01');prev=(first-timedelta(days=1)).strftime('%Y-%m')
    monthly={}
    for m in set(months+[anchor,prev]):
        a=date.fromisoformat(m+'-01');length=monthrange(a.year,a.month)[1]
        monthly[m]=(read({**scope,'dateBasis':'send','start':str(a),'end':str(a.replace(day=length))}),length)
    nowdata,nowdays=monthly[anchor]
    prelim=_summary(nowdata,names,all_view,nowdays,0)
    main=max(range(12),key=lambda i:prelim['distribution'][i])
    sums={m:{'month':m,**_summary(d,names,all_view,ds,main)} for m,(d,ds) in monthly.items()}
    result['history']={'months':[sums[m] for m in months],'current':sums[anchor],'previous':sums[prev],
                       'mainBand':{'lo':main*.5,'hi':(main+1)*.5 if main<11 else None},'delta':change(sums[anchor],sums[prev]),'minimum':MIN_PREV}
    if req.get('comparePrevious'):
        pd=read({**scope,'start':pr['previousStart'],'end':pr['previousEnd']})
        prevdays=(date.fromisoformat(pr['previousEnd'])-date.fromisoformat(pr['previousStart'])).days+1
        ps=_summary(pd,names,all_view,prevdays,main)
        o=result['overview'];current={'n':o['n'],'daily':o['daily'],'avg':o['avgSystem'],'avgW':o['avgW'],'criticalShare':o['critical']['n']/o['n'] if o['n'] else None}
        result['previous'].update(summary=ps,delta=change(current,ps))
        for s in result.get('ranking',[]):
            p=_summary(pd,[s['name']],False,prevdays,main)
            s['previous']={'n':p['n'],'delta':change({'n':s['total']['n'],'avg':s['total']['avgSystem']},p)}
