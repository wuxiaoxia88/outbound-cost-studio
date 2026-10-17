"""Import-operation groups and reversible row-state journal; raw records are never deleted."""
import datetime
from decimal import Decimal
import json
import uuid
from .store import now, q
from .analytics import VersionConflict


def migrate(wh):
    c=wh.con
    c.execute('''CREATE SEQUENCE IF NOT EXISTS import_event_id;
        CREATE TABLE IF NOT EXISTS import_groups(id VARCHAR PRIMARY KEY, created VARCHAR, status VARCHAR, conflict_rule VARCHAR, legacy BOOLEAN);
        CREATE TABLE IF NOT EXISTS import_files(group_id VARCHAR, batch_id VARCHAR, name VARCHAR, stats VARCHAR);
        CREATE TABLE IF NOT EXISTS import_members(batch_id VARCHAR, row_id BIGINT);
        CREATE TABLE IF NOT EXISTS import_events(id BIGINT PRIMARY KEY, group_id VARCHAR, new_batch VARCHAR);
        CREATE TABLE IF NOT EXISTS import_before(event_id BIGINT, row_id BIGINT, before_row VARCHAR);
    ''')
    c.execute("ALTER TABLE import_groups ADD COLUMN IF NOT EXISTS name VARCHAR DEFAULT ''")
    # Older files have no upload-operation identifier. Never guess that adjacent uploads were one action.
    c.execute("INSERT INTO import_groups(id,created,status,conflict_rule,legacy) SELECT b.id,b.created,'done','review',TRUE FROM batches b WHERE b.id NOT IN (SELECT batch_id FROM import_files)")
    c.execute('INSERT INTO import_files SELECT b.id,b.id,b.name,b.stats FROM batches b WHERE b.id NOT IN (SELECT batch_id FROM import_files)')
    c.execute('INSERT INTO import_members SELECT batch,id FROM ship WHERE batch NOT IN (SELECT DISTINCT batch_id FROM import_members)')
    # A crashed uploader leaves successfully committed files recoverable, rather than an eternal spinner.
    c.execute("UPDATE import_groups SET status='partial' WHERE status='open'")


def create(wh, req=None):
    req=req or {}
    rule=req.get('conflictRule',wh.config().get('conflictRule','replace'))
    if rule not in ('replace','review'):raise ValueError('冲突规则无效')
    name=str(req.get('name') or '').strip()[:200]
    with wh.wlock:
        gid=uuid.uuid4().hex
        wh.cursor().execute('INSERT INTO import_groups(id,created,status,conflict_rule,legacy,name) VALUES (?,?,?,?,FALSE,?)',[gid,datetime.datetime.now().isoformat(timespec='microseconds'),'open',rule,name])
    return {'id':gid}


def require_open(c,gid):
    r=c.execute('SELECT status,conflict_rule FROM import_groups WHERE id=?',[gid]).fetchone()
    if not r or r[0]!='open':raise ValueError('导入操作已结束，请重新选择文件')
    return r[1]


def finish(wh, req):
    gid=req['id']
    with wh.wlock:
        if any(j.get('groupId')==gid and j['state'] in ('queued','running') for j in wh.jobs.values()):raise ValueError('请等待文件导入完成')
        c=wh.cursor();require_open(c,gid)
        status='partial' if req.get('partial') else 'done'
        c.execute('UPDATE import_groups SET status=? WHERE id=?',[status,gid])
    return {'ok':True,'group':read(wh,gid)}


def event(c,gid,batch=None,ids_sql='SELECT id FROM ship WHERE FALSE',args=None):
    eid=c.execute("SELECT nextval('import_event_id')").fetchone()[0]
    c.execute('INSERT INTO import_events VALUES (?,?,?)',[eid,gid,batch])
    c.execute('INSERT INTO import_before SELECT ?,s.id,to_json(s) FROM ship s WHERE s.id IN ('+ids_sql+')',[eid]+(args or []))
    return eid


def group_for_row(c,rid):
    row=c.execute("SELECT f.group_id FROM import_files f JOIN ship s ON s.batch=f.batch_id JOIN import_groups g ON g.id=f.group_id WHERE s.id=? AND g.status<>'undone' ORDER BY g.created DESC,g.id DESC LIMIT 1",[rid]).fetchone()
    if not row:raise ValueError('待核记录没有有效导入操作')
    return row[0]


def file_link(c,gid,bid,name,stats):
    c.execute('INSERT INTO import_files VALUES (?,?,?,?)',[gid,bid,name,json.dumps(stats,ensure_ascii=False)])


def _read(c,gid,details=True):
    g=c.execute('SELECT id,created,status,conflict_rule,legacy,name FROM import_groups WHERE id=?',[gid]).fetchone()
    if not g:raise ValueError('导入记录不存在')
    result=dict(zip(('id','created','status','conflictRule','legacy','name'),g))
    files=c.execute('SELECT batch_id,name,stats FROM import_files WHERE group_id=?',[gid]).fetchall()
    result['files']=[{'id':b,'name':n,**json.loads(s)} for b,n,s in files]
    result['stats']={k:sum(f.get(k,0) or 0 for f in result['files']) for k in ('rows','new','duplicate','conflict','replaced')}
    if details:
        base='FROM ship WHERE id IN (SELECT row_id FROM import_members WHERE batch_id IN (SELECT batch_id FROM import_files WHERE group_id=?))'
        span=c.execute('SELECT quantile_disc(send_day,.005),quantile_disc(send_day,.995),count(*) FILTER (WHERE send_day IS NULL),min(day),max(day) '+base,[gid]).fetchone()
        lo,hi=span[:2]
        outside=c.execute('SELECT count(*) '+base+' AND (send_day<? OR send_day>? OR send_day IS NULL)',[gid,lo,hi]).fetchone()[0]
        result.update(start=str(lo) if lo else None,end=str(hi) if hi else None,outside=outside,missingDate=span[2])
        result['rules']=dict(zip(('invalid','no_weight','self_sign','return'),c.execute('SELECT '+','.join('count(*) FILTER (WHERE flags & %d <> 0)'%bit for bit in (5,2,8,16))+' '+base,[gid]).fetchone()))
        result['customers']=[r[0] for r in c.execute('SELECT customer '+base+' GROUP BY 1 ORDER BY count(*) DESC,customer',[gid]).fetchall()]
        result['dateRanges']={'send':{'start':result['start'],'end':result['end']},'handover':{'start':str(span[3]) if span[3] else None,'end':str(span[4]) if span[4] else None}}
    return result


def read(wh,gid):
    with wh.snapshot() as (c,v):return _read(c,gid)


def listing(wh,c=None):
    c=c or wh.cursor()
    ids=c.execute('SELECT id FROM import_groups ORDER BY created DESC,id DESC').fetchall()
    return {'groups':[_read(c,g[0],False) for g in ids]}


def latest(c):
    r=c.execute("SELECT g.id FROM import_groups g WHERE status<>'undone' AND EXISTS (SELECT 1 FROM import_files f WHERE f.group_id=g.id) ORDER BY created DESC,id DESC LIMIT 1").fetchone()
    return _read(c,r[0]) if r else None


def undo(wh,req):
    gid=req['id']
    with wh.wlock:
        c=wh.cursor();c.execute('BEGIN')
        try:
            if req.get('expectedVersion') is None or int(req['expectedVersion'])!=wh.version(c):
                raise VersionConflict('数据已更新，请刷新导入记录再撤销')
            g=_read(c,gid,False)
            if g['status'] in ('open','undone'):raise ValueError('该次导入尚未结束或已经撤销')
            events=c.execute('SELECT id,new_batch FROM import_events WHERE group_id=? ORDER BY id DESC',[gid]).fetchall()
            if g['legacy']:
                # Legacy imports have no before-images. Only pristine, conflict-free ones can be undone.
                altered=c.execute("SELECT count(*) FROM ship WHERE batch=? AND (state<>1 OR issue<>'')",[gid]).fetchone()[0]
                if altered:raise ValueError('此旧版导入存在历史冲突处理，缺少恢复记录，不能自动撤销')
                event(c,gid,gid);events=c.execute('SELECT id,new_batch FROM import_events WHERE group_id=?',[gid]).fetchall()
            c.execute('''CREATE OR REPLACE TEMP TABLE undo_ids AS SELECT id FROM ship WHERE batch IN (SELECT new_batch FROM import_events WHERE group_id=?)
                UNION SELECT row_id FROM import_before WHERE event_id IN (SELECT id FROM import_events WHERE group_id=?)''',[gid,gid])
            if events:
                first=min(e[0] for e in events)
                dep=c.execute('''SELECT DISTINCT e.group_id FROM import_events e JOIN import_groups g ON g.id=e.group_id
                    JOIN ship s ON (s.batch=e.new_batch OR s.id IN (SELECT row_id FROM import_before b WHERE b.event_id=e.id))
                    WHERE e.group_id<>? AND g.status<>'undone' AND (e.id>? OR ?)
                    AND s.waybill IN (SELECT waybill FROM ship WHERE id IN (SELECT id FROM undo_ids))''',[gid,first,g['legacy']]).fetchall()
                if dep:raise ValueError('后续导入或待核处理使用了相同运单，请先撤销相关后续导入：'+', '.join(x[0][:8] for x in dep))
            backfill_return=False
            for eid,batch in events:
                if batch:c.execute("UPDATE ship SET state=-1,issue='已撤销导入' WHERE batch=?",[batch])
                for rid,raw in c.execute('SELECT row_id,before_row FROM import_before WHERE event_id=?',[eid]).fetchall():
                    before=json.loads(raw,parse_float=Decimal);backfill_return |= 'return_bill' not in before;cols=[k for k in before if k!='id']
                    c.execute('UPDATE ship SET '+','.join(q(k)+'=?' for k in cols)+' WHERE id=?',[before[k] for k in cols]+[rid])
            if backfill_return:wh._fill_return_bill(c,'SELECT id FROM undo_ids')
            from . import business_day
            business_day.fill(c,'SELECT id FROM undo_ids')
            c.execute("UPDATE import_groups SET status='undone' WHERE id=?",[gid]);wh.audit(c,'撤销导入',{'group':gid});wh.bump(c);version=wh.version(c);c.execute('COMMIT')
        except Exception:c.execute('ROLLBACK');raise
        finally:c.close()
    return {'ok':True,'version':version}
