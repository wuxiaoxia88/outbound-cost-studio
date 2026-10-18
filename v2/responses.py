"""Versioned, single-flight response LRU. Cached bytes never escape as mutable data."""
from collections import OrderedDict
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import gzip
import json
import threading
from . import analysis, simulation, reports, imports


try: SHANGHAI=ZoneInfo('Asia/Shanghai')
except ZoneInfoNotFoundError: SHANGHAI=timezone(timedelta(hours=8),'Asia/Shanghai')

def dumps(data):
    return json.dumps(data, ensure_ascii=False, default=str, separators=(',', ':')).encode()


class Encoded:
    def __init__(self, data):
        self.raw = data if isinstance(data, bytes) else dumps(data)
        self.gzip = gzip.compress(self.raw, compresslevel=5, mtime=0)


def accepts_gzip(header):
    values = {}
    for part in header.lower().split(','):
        bits = part.strip().split(';'); quality = 1.
        for param in bits[1:]:
            if param.strip().startswith('q='):
                try: quality = float(param.strip()[2:])
                except ValueError: quality = 0.
        values[bits[0]] = quality
    return values.get('gzip', values.get('*', 0)) > 0


def rank_public(row):
    keys = ('name','key','total','daily','avgW','main','avg','eqSystem','gap','critical','returnRate','returnPer','previous','tags')
    return {k: row[k] for k in keys if k in row}


def compact(result):
    r = dict(result)
    if r['mode'] == 'all':
        r['rankingCount'] = len(r['ranking'])
        r['ranking'] = [rank_public(x) for x in reports.ranking_rows(r['ranking'], {})]
        r['clusters'] = {**r['clusters'], **{k:[{**{a:b for a,b in g.items() if a!='members'},'memberCount':len(g['members'])} for g in r['clusters'][k]] for k in ('groups','unique')}}
        r['lazy'] = True
    return r


class Responses:
    def __init__(self, wh, limit=16):
        self.wh=wh;self.limit=limit;self.lock=threading.RLock();self.entries=OrderedDict();self.inflight={};self.revision=None
        self.stop=threading.Event();self.error=None

    def current(self):
        # Duplicate-only imports change metadata without changing dataVersion.
        with self.wh.snapshot() as (c,v):
            batches=c.execute('SELECT count(*) FROM batches').fetchone()[0]
            groups=c.execute('SELECT id,status FROM import_groups ORDER BY id').fetchall()
        return v,batches,tuple(groups)

    def sync(self):
        rev=self.current()
        with self.lock:
            if rev != self.revision:
                self.entries.clear();self.revision=rev
        return rev

    def get(self, kind, req=None):
        req=req or {};rev=self.sync()
        if kind=='analysis':
            with self.wh.snapshot() as (c,v):
                s=analysis.settings(self.wh,req,c);sm=simulation.state(self.wh,req,c)
            normalized={**s, 'simulation':sm, 'customers':req.get('customers','all'), 'level':analysis.level_of(req),
                        'expand':bool(req.get('expand')), 'showAll':bool(req.get('showAll')), 'compareBand':req.get('compareBand'), 'comparePrevious':bool(req.get('comparePrevious'))}
        else: normalized=req
        key=(rev,kind,json.dumps(normalized,ensure_ascii=False,sort_keys=True,separators=(',',':')))
        with self.lock:
            if key in self.entries:
                entry=self.entries[key];self.entries.move_to_end(key)
                return self.wire(entry,req)
            pending=self.inflight.get(key)
            owner=pending is None
            if owner:pending=self.inflight[key]=Future()
        if not owner:return self.wire(pending.result(),req)
        try:
            data=(analysis.analysis if kind=='analysis' else analysis.meta)(self.wh,req)
            if kind=='meta':data={**data,'responseRevision':rev}
            entry={'data':data,'full':Encoded(data)}
            if kind=='analysis':entry['compact']=Encoded(compact(data))
            with self.lock:
                if self.current()==rev:
                    self.entries[key]=entry
                    while len(self.entries)>self.limit:self.entries.popitem(last=False)
            pending.set_result(entry)
            return self.wire(entry,req)
        except Exception as ex:
            pending.set_exception(ex)
            raise
        finally:
            with self.lock:
                if self.inflight.get(key) is pending:self.inflight.pop(key,None)

    def wire(self,entry,req):
        if 'snapshotId' in entry['data']:reports.retain(self.wh,entry['data'])
        return entry['compact'] if req.get('compact') and 'compact' in entry else entry['full']

    def default_request(self, preset='previous'):
        today=datetime.now(SHANGHAI).date();first=today.replace(day=1)
        if preset=='previous':end=first-timedelta(days=1);start=end.replace(day=1)
        elif preset=='month':start=first;end=today
        else:
            with self.wh.snapshot() as (c,v):last=imports.latest(c)
            if not last or not last.get('start'):return None
            start=last['start'];end=last['end']
        return {'customers':'all','dateBasis':'send','start':str(start),'end':str(end),'level':'province','compact':True}

    def warm(self):
        self.get('analysis',self.default_request())
        self.get('meta')
        for preset in ('import','month'):
            req=self.default_request(preset)
            if req and not self.stop.is_set():self.get('analysis',req)

    def start(self):
        def worker():
            last=None
            while not self.stop.is_set():
                try:
                    rev=(self.current(),datetime.now(SHANGHAI).date())
                    if rev!=last:
                        self.warm();last=rev;self.error=None
                except Exception as ex:self.error=str(ex)
                self.stop.wait(1)
        self.worker=threading.Thread(target=worker,name='analysis-prewarm',daemon=True);self.worker.start()

    def close(self):
        self.stop.set()
        if hasattr(self,'worker'):self.worker.join()


_INIT=threading.Lock()
def service(wh):
    with _INIT:
        if not hasattr(wh,'_responses'):wh._responses=Responses(wh)
        return wh._responses


def details(wh,req):
    r=reports.read(wh,req.get('snapshotId'))
    part=req.get('part')
    if part=='ranking':return {'ranking':[rank_public(x) for x in r.get('ranking',[])]}
    if part=='groups':return {'clusters':r['clusters'],'groupStats':[rank_public(x) for x in r['ranking']]}
    if part=='report':return compact(r) | {'ranking':[rank_public(x) for x in r.get('ranking',[])],'lazy':False}
    raise ValueError('展开模块无效')
