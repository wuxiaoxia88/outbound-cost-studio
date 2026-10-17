#!/usr/bin/env python3
"""每天按交件业务日同步。仅通过 HTTP 访问成本库；凭据只在 232 本机读取。"""
import argparse
from contextlib import contextmanager
import datetime as dt
from decimal import Decimal, InvalidOperation
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v2.store import REQUIRED, calamine_rows, streaming_rows

try: SHANGHAI = ZoneInfo('Asia/Shanghai')
except ZoneInfoNotFoundError: SHANGHAI = dt.timezone(dt.timedelta(hours=8), 'Asia/Shanghai')
EXPECTED = json.loads(Path(__file__).with_name('outbound_columns.json').read_text())


class SyncError(Exception): pass
class UncertainExport(SyncError): pass


def now(): return dt.datetime.now(SHANGHAI).isoformat(timespec='microseconds')


def window(day):
    return {'startTime': f'{day} 12:00:00',
            'endTime': f'{day + dt.timedelta(days=1)} 11:59:59', 'queryDateType': 2}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


class HTTP:
    def __init__(self, url, headers=None):
        u = urllib.parse.urlsplit(url)
        if u.scheme != 'http' or u.hostname not in ('127.0.0.1', 'localhost') or u.username or u.password or u.path not in ('', '/'):
            raise SyncError('同步客户端只允许本机回环 HTTP 地址')
        self.url, self.headers = url.rstrip('/'), headers or {}
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))

    def call(self, path, body=None, timeout=60):
        req = urllib.request.Request(self.url+path, data=None if body is None else json.dumps(body).encode(),
                                     headers={**self.headers, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(req, timeout=timeout) as response: result = json.load(response)
        except urllib.error.HTTPError as ex:
            # 不向日志回显服务端请求头、响应体或 Key。
            if ex.code >= 500: raise UncertainExport(f'上游 HTTP {ex.code}，结果可能已提交') from None
            raise SyncError(f'接口拒绝 HTTP {ex.code}') from None
        except (OSError, ValueError, http.client.HTTPException):
            raise UncertainExport('接口响应未确认，请核对原平台任务后用 --resume 接续') from None
        if not isinstance(result, dict): raise SyncError('接口返回格式异常')
        if result.get('error') or result.get('success') is False or result.get('ok') is False or result.get('status') in ('error','failed') or ('code' in result and result['code'] not in (0,200,'200','0')):
            raise SyncError('接口返回失败（详细原因请在服务日志核对）')
        return result

    def upload(self, file, group):
        u = urllib.parse.urlsplit(self.url)
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=300)
        try:
            conn.putrequest('POST', '/api/v2/import')
            for k, v in {**self.headers, 'Content-Length': str(file.stat().st_size),
                         'X-Filename': urllib.parse.quote(file.name), 'X-Import-Group': group}.items():
                conn.putheader(k, v)
            conn.endheaders()
            with file.open('rb') as src:
                for part in iter(lambda: src.read(1 << 20), b''): conn.send(part)
            response = conn.getresponse(); result = json.load(response)
            if response.status != 200 or result.get('error'): raise SyncError('成本库上传失败')
            return result['jobs'][0]
        finally: conn.close()


@contextmanager
def process_lock(home):
    # 同一机器的手动补数和定时任务互斥；崩溃后由内核释放。
    import fcntl
    home.mkdir(parents=True, exist_ok=True)
    with (home/'sync.lock').open('a') as f:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise SyncError('已有同步运行中，请稍后查看 status') from None
        try: yield
        finally: fcntl.flock(f, fcntl.LOCK_UN)


def atomic_json(path, data):
    tmp = path.with_name(path.name+'.tmp')
    with tmp.open('w') as f:
        os.chmod(tmp, 0o600); json.dump(data, f, ensure_ascii=False, default=str); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


class Journal:
    def __init__(self, home):
        import duckdb
        self.home = home; home.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(home/'sync_runs.duckdb'))
        self.con.execute('CREATE TABLE IF NOT EXISTS runs(id VARCHAR PRIMARY KEY, started VARCHAR, finished VARCHAR, status VARCHAR, summary VARCHAR)')
        self.con.execute('CREATE TABLE IF NOT EXISTS days(run_id VARCHAR, day DATE, updated VARCHAR, status VARCHAR, payload VARCHAR, PRIMARY KEY(run_id,day))')

    def start(self, rid):
        self.con.execute('INSERT INTO runs VALUES (?,?,NULL,?,?)', [rid, now(), 'running', '同步进行中'])
        atomic_json(self.home/'sync_latest.json', {'runId': rid, 'started': now(), 'status': 'running', 'summary': '同步进行中'})

    def save(self, rid, day, record):
        self.con.execute('INSERT OR REPLACE INTO days VALUES (?,?,?,?,?)', [rid, day, now(), record['status'], json.dumps(record, ensure_ascii=False, default=str)])

    def previous(self, day):
        r = self.con.execute('SELECT payload FROM days WHERE day=? ORDER BY updated DESC LIMIT 1', [day]).fetchone()
        return json.loads(r[0]) if r else None

    def finish(self, rid, status, summary):
        self.con.execute('UPDATE runs SET finished=?,status=?,summary=? WHERE id=?', [now(), status, summary, rid])
        start = self.con.execute('SELECT started FROM runs WHERE id=?', [rid]).fetchone()[0]
        atomic_json(self.home/'sync_latest.json', {'runId':rid, 'started':start, 'finished':now(), 'status':status, 'summary':summary})

    def close(self): self.con.close()


def validate_xlsx(path):
    try:
        with zipfile.ZipFile(path) as z:
            required = ('[Content_Types].xml', '_rels/.rels', 'xl/workbook.xml', 'xl/_rels/workbook.xml.rels')
            if not set(required).issubset(z.namelist()) or z.testzip(): raise ValueError('invalid workbook')
            for name in required: ET.fromstring(z.read(name))
    except (OSError, ValueError, RuntimeError, NotImplementedError, ET.ParseError, zipfile.BadZipFile):
        raise SyncError('下载文件不是完整 Excel，请核对原平台下载结果') from None


def inspect_file(path, day, expected=EXPECTED):
    validate_xlsx(path)
    def read(rows):
        seen, count, total = set(), 0, Decimal(0)
        current, header = None, None
        added, missing, sheets, outside, bad_ids = set(), set(), 0, 0, 0
        lo = dt.datetime.combine(day, dt.time(12)); hi = lo + dt.timedelta(days=1)
        for sheet, line, row in rows:
            if sheet != current: current, header = sheet, None
            if header is None:
                clean = [str(v).strip() for v in row]
                if '运单号' not in clean: continue
                header = clean; sheets += 1
                if len([x for x in header if x]) != len(set(x for x in header if x)):
                    raise SyncError('存在重复表头，未导入')
                added.update(set(header)-set(expected)-{''}); missing.update(set(expected)-set(header))
                required = set(REQUIRED)-set(header)
                if required: raise SyncError('缺少必需字段：'+'、'.join(sorted(required)))
                continue
            if not any(row): continue
            record = dict(zip(header, list(row)+['']*max(0,len(header)-len(row))))
            count += 1; wb = record['运单号'].strip(); seen.add(wb)
            if not wb: bad_ids += 1
            try:
                fee = Decimal(record['总费用(元)'].strip().replace(',', ''))
                if not fee.is_finite(): raise InvalidOperation
                total += fee
            except (InvalidOperation, AttributeError): raise SyncError('总费用含无效金额，未导入') from None
            try:
                raw = record['交件时间'].strip()
                when = dt.datetime(1899,12,30) + dt.timedelta(days=float(raw)) if re.fullmatch(r'\d+(\.\d+)?',raw) else dt.datetime.fromisoformat(raw.replace('/','-'))
                if not lo <= when < hi: outside += 1
            except (ValueError, OverflowError, TypeError): outside += 1
        if not sheets: raise SyncError('未发现运单明细表头')
        return {'rows':count, 'unique':len(seen), 'fileTotalFee':str(total), 'addedColumns':sorted(added),
                'missingColumns':sorted(missing), 'outsideWindow':outside, 'emptyWaybills':bad_ids}
    try: return read(calamine_rows(path))
    except MemoryError: return read(streaming_rows(path))


EXPORT_PREFIX = '出港账单(包补收)-业务账单_网点业务账单运单明细_'
ASSOCIATION_VERSION = 'time-count-v1'


def count_value(value):
    try:
        if isinstance(value,bool): raise ValueError
        n=Decimal(str(value))
        if not n.is_finite() or n<0 or n!=n.to_integral_value(): raise ValueError
        return int(n)
    except (ValueError,InvalidOperation):
        raise SyncError('账单汇总票数无效') from None


def bill_summary(client,day):
    check=client.call('/api/data/outbound-bill/query',{**window(day),'pageSize':10})['data']
    n=count_value(check['billCount'])
    try: fee=Decimal(str(check['totalFee']))
    except InvalidOperation: raise SyncError('账单汇总金额无效') from None
    if not fee.is_finite(): raise SyncError('账单汇总金额无效')
    return n,fee


def association_hint(record):
    return f"T0={record['createStarted']}；billCount={record['expectedBillCount']}；runId={record.get('exportRunId','')}"


def matching_export(client, record):
    """完整分页按默认名称、固定创建时间窗和精确行数选唯一任务。"""
    started=dt.datetime.fromisoformat(record['createStarted'])
    lower=started-dt.timedelta(seconds=5);upper=started+dt.timedelta(seconds=120)
    query={'startCreateTime':lower.strftime('%Y-%m-%d %H:%M:%S'),
           'endCreateTime':(upper+dt.timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')}
    matches=[];seen=set();expected_total=None;read=0
    for page in range(1,101):
        result=client.call('/api/data/outbound-bill-export/fetchTaskApi',{
            'pageDTO':{'pageNum':page,'pageSize':100,'sortField':'createTime','sortType':'DESC','needTotalCount':True},
            'fetchTaskDTO':query})
        try:
            data=result['data'];rows=data['data'];total=data['total']
            if not isinstance(rows,list) or type(total) is not int or total<0:raise ValueError
            if expected_total is not None and total!=expected_total:raise ValueError
            expected_total=total
            for task in rows:
                task_id=task.get('id') or task.get('taskId')
                if not isinstance(task_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}',task_id):raise ValueError
                if task_id in seen:raise ValueError
                seen.add(task_id)
                name=task.get('taskName')
                if not isinstance(name,str) or not name.startswith(EXPORT_PREFIX):continue
                # total=null 表示尚未给出行数；不据 processedCount 或其他近似值匹配。
                try:
                    created=dt.datetime.fromisoformat(task['createTime'])
                    if created.tzinfo is None:created=created.replace(tzinfo=SHANGHAI)
                    n=count_value(task.get('total'))
                except (KeyError,TypeError,ValueError,SyncError):continue
                if lower<=created<=upper and n==record['expectedBillCount']:
                    matches.append({**task,'id':task_id})
            read+=len(rows)
            if read>total or (not rows and read<total):raise ValueError
        except (KeyError,TypeError,AttributeError,ValueError):
            raise UncertainExport('导出任务列表不完整或已变化，未关联；'+association_hint(record)) from None
        if len(matches)>1:
            raise UncertainExport('多条任务满足创建时刻和行数，未下载；'+association_hint(record))
        if read==total:return matches[0] if matches else None
    raise UncertainExport('导出任务分页超过安全上限，未关联；'+association_hint(record))


def export_file(client, day, record, save, run_id, sleep=None, download_root=None):
    sleep=sleep or time.sleep
    if not record.get('createStarted'):
        n,fee=bill_summary(client,day)
        # 先查询 N，再将创建时刻 T0 持久化；创建请求只发送一次。
        record.update(associationVersion=ASSOCIATION_VERSION,expectedBillCount=n,
                      billCountBefore=n,billTotalFeeBefore=str(fee),exportRunId=run_id,
                      createStarted=now(),status='exporting')
        save(record)
        try:
            client.call('/api/workflows/outbound-bill-export/create-export-task',window(day),timeout=120)
        except SyncError:
            raise UncertainExport('创建响应未确认，未重建；'+association_hint(record)+'；重跑将按原时间窗和行数查询') from None
    if record.get('associationVersion')!=ASSOCIATION_VERSION or 'expectedBillCount' not in record:
        raise UncertainExport('旧任务缺少创建前票数或关联口径，请人工核对后接续或指定 --restart-export 日期')
    started=dt.datetime.fromisoformat(record['createStarted'])
    for poll in range(181):
        task=matching_export(client,record)
        elapsed=(dt.datetime.fromisoformat(now())-started).total_seconds()
        if task is None:
            if elapsed>=300 or poll>=60:
                raise UncertainExport('5分钟内未找到满足创建时刻和行数的任务，未下载；'+association_hint(record))
        else:
            if record.get('taskId') and record['taskId']!=task['id']:
                raise UncertainExport('候选任务标识已变化，未下载；'+association_hint(record))
            record.update(taskId=task['id'],matchedTaskName=task['taskName'],
                          taskCreated=task['createTime'],taskStatus=task.get('status'),taskTotal=task['total'])
            save(record)
            status=task.get('status')
            if status in (4,'FAIL'):
                raise UncertainExport(f"原导出任务失败，未重建；taskId={task['id']}；"+association_hint(record))
            # 该接口实测 2 为执行中，3 为成功；只在成功后下载。
            if status==3:
                root=Path(download_root or Path.home()/'zto-exports').expanduser().resolve()
                source=root/f"sync-{task['id']}.xlsx"
                record.update(source=str(source),status='exported');save(record)
                try:download_task_file(client,task['id'],source)
                except SyncError:source=recover_download(client,task['id'],source,record,save,root,sleep)
                return source,task['id']
        if elapsed>=900:break
        if poll<180:sleep(5)
    raise UncertainExport('原导出任务等待超时，未重建；'+association_hint(record))


def download_task_file(client, task, target):
    """只刷新原任务的签名链接；下载请求不携带本机 API Key。"""
    result = client.call('/api/workflows/outbound-bill-export/fetch-download-link', {'taskId': task})
    try:
        url = result['data']['results']['fetchLinkApi']
        u = urllib.parse.urlsplit(url)
        if (not isinstance(url, str) or u.scheme != 'https' or
                u.hostname != 'file-storage.gw.zt-express.com' or u.port not in (None, 443) or
                u.username or u.password or u.fragment):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise SyncError('原任务下载链接无效或不是支持的文件服务') from None
    # 单独的无凭据 opener，拒绝跳转；签名 URL 不写日志或状态文件。
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.outbound-', suffix='.partial', delete=False) as out:
            tmp = Path(out.name)
            with opener.open(urllib.request.Request(url), timeout=300) as response:
                shutil.copyfileobj(response, out, 1 << 20)
            out.flush(); os.fsync(out.fileno())
        validate_xlsx(tmp)
        os.replace(tmp, target)
    except (OSError, ValueError, http.client.HTTPException):
        raise SyncError('原任务文件下载未完成') from None
    finally:
        if tmp is not None: tmp.unlink(missing_ok=True)


def recover_download(client, task, source, record, save, download_root=None, sleep=None):
    """坏文件最多额外重下五次，计数落盘；自动接续不重置预算。"""
    sleep = sleep or time.sleep
    root = Path(download_root or Path.home()/'zto-exports').expanduser().resolve()
    target = source.resolve()
    if not target.is_relative_to(root): target = root/f'sync-{task}.xlsx'
    record.update(source=str(target), downloadState='retrying'); save(record)
    for attempt in range(record.get('downloadAttempts', 0) + 1, 6):
        record['downloadAttempts'] = attempt; save(record)
        sleep(60)
        try: download_task_file(client, task, target)
        except SyncError:
            # 取链接超时也只允许继续取同一任务的链接，绝不重新创建导出。
            continue
        record.update(downloadState='recovered'); save(record)
        return target
    record.update(downloadState='exhausted'); save(record)
    raise UncertainExport(f'结果未确认：原任务重取链接重下 5 次仍未通过校验；taskId={task}；请核对后用 --resume 接续')


def archive(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == target.resolve(): return
    if target.exists():
        # 断电可能留下已移走的原件；不同内容不能覆盖留痕文件。
        import hashlib
        def digest(p):
            with p.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()
        if source.exists() and digest(source) != digest(target): raise SyncError('原件归档文件冲突')
        if source.exists(): source.unlink()
        return
    if not source.is_file(): raise SyncError('导出原件不存在，请核对任务下载状态')
    # 跨文件系统先复制到临时文件、fsync、rename，再删除下载区原件。
    tmp = target.with_name(target.name+'.partial')
    with source.open('rb') as src, tmp.open('wb') as out:
        os.chmod(tmp,0o600); shutil.copyfileobj(src,out,1<<20); out.flush(); os.fsync(out.fileno())
    os.replace(tmp,target); source.unlink()


def process_day(day, record, save, zto, cost, group, raw_root, resume=None, download_root=None, sleep=None, run_id=None):
    if resume:
        source, task = Path(resume[1]).expanduser(), resume[0]
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}',task): raise SyncError('接续任务标识无效')
        record.update(taskId=task, source=str(source), status='exported')
        record.pop('downloadAttempts', None); record.pop('downloadState', None)
    elif record.get('file') and Path(record['file']).is_file() and record.get('status') in ('exported','validated','importing','import_failed','uncertain'):
        source, task = Path(record['file']), record['taskId']
    elif record.get('source') and record.get('status') in ('exported','validated','importing','import_failed','uncertain'):
        source, task = Path(record['source']), record['taskId']
    else:
        if record.get('status') in ('exporting','uncertain') and not record.get('createStarted'):
            raise UncertainExport('上次导出结果未确认，请查原平台任务后用 --resume 接续')
        if not record.get('createStarted'):
            supersedes=record.get('supersedes');record.clear()
            if supersedes:record['supersedes']=supersedes
        record['status']='exporting'; save(record)
        source, task = export_file(zto,day,record,save,run_id or uuid.uuid4().hex,sleep,download_root)
        record.update(taskId=task,source=str(source),status='exported'); save(record)
    target = raw_root/'raw'/'outbound_detail'/day.strftime('%Y/%m')/f'{day}__{task}.xlsx'
    record.update(file=str(target),status='exported'); save(record)
    try: validate_xlsx(source if source.is_file() else target)
    except SyncError: source = recover_download(zto, task, source, record, save, download_root, sleep)
    archive(source,target)
    record.update(inspect_file(target,day)); save(record)
    bill_count,bill_fee=bill_summary(zto,day)
    record.update(billCount=bill_count,billTotalFee=str(bill_fee),feeGap=str(Decimal(record['fileTotalFee'])-bill_fee))
    save(record)
    if record['rows'] != record['unique'] or record['rows'] != bill_count or record['rows'] != record.get('expectedBillCount',record['rows']) or record['emptyWaybills'] or record['outsideWindow']:
        raise SyncError('行数、唯一运单数、账单票数或业务日窗口不一致，未导入')
    record['status']='validated'; save(record)
    jid = cost.upload(target,group); record.update(jobId=jid,status='importing'); save(record)
    deadline = time.monotonic()+3600
    while True:
        result = cost.call('/api/v2/jobs?id='+urllib.parse.quote(jid))
        if result['state']=='done': break
        if result['state']=='error': raise SyncError('成本库导入失败，请核对导入任务日志')
        if time.monotonic()>deadline: raise SyncError('成本库导入仍在运行，请先核对任务状态再重跑')
        time.sleep(1)
    stats = result['stats']
    if stats.get('conflict'): raise SyncError('导入出现待核冲突，请核对成本库')
    record.pop('error',None)
    record.update(status='done',new=max(0,stats['new']-stats.get('replaced',0)),replaced=stats.get('replaced',0),duplicate=stats['duplicate'])
    save(record)
    return record


def run(args):
    home, raw_root = Path(args.home).expanduser(), Path(args.raw_root).expanduser()
    end = dt.date.fromisoformat(args.end) if args.end else dt.datetime.now(SHANGHAI).date()-dt.timedelta(days=1)
    start = dt.date.fromisoformat(args.start) if args.start else end-dt.timedelta(days=args.recheck_days-1)
    if start>end or args.recheck_days<1: raise SyncError('日期范围或复核天数无效')
    if end >= dt.datetime.now(SHANGHAI).date(): raise SyncError('只能同步已结束的业务日')
    resumes = {dt.date.fromisoformat(d):(t,p) for d,t,p in args.resume}
    if any(d<start or d>end for d in resumes): raise SyncError('--resume 日期必须在本次范围内')
    restarts = {dt.date.fromisoformat(d) for d in getattr(args,'restart_export',[])}
    if any(d<start or d>end for d in restarts) or restarts.intersection(resumes):
        raise SyncError('--restart-export 日期必须在本次范围内，且不能同时 --resume 同一日期')
    with process_lock(home):
        key = json.loads(Path(args.key_file).expanduser().read_text())['apiKey']
        zto = HTTP(args.zto_url,{'X-API-Key':key}); cost = HTTP(args.cost_url)
        health = cost.call('/api/health')
        if not health.get('ok') or health.get('version') not in ('3.2.1','3.2.2','3.3.0'): raise SyncError('成本服务需为 3.2.1、3.2.2 或 3.3.0，请先核对版本')
        cost.headers['X-App-Token']=cost.call('/api/bootstrap')['token']
        journal=Journal(home); rid=uuid.uuid4().hex; journal.start(rid)
        group=None; started=time.monotonic(); records=[]; failed=False
        try:
            group=cost.call('/api/v2/batches/start',{'name':f'自动同步 {start}…{end}','conflictRule':'replace'})['id']
            for offset in range((end-start).days+1):
                day=start+dt.timedelta(days=offset); t=time.monotonic(); record=journal.previous(day) or {}
                # 完成过的日期需重新导出，才能发现后续补录/改价。
                if record.get('status')=='done': record={}
                try:
                    if day in restarts and record:
                        if record.get('status')=='importing':raise SyncError('原导入仍待核对，不能重新创建导出任务')
                        record={'supersedes':{k:record[k] for k in ('exportRunId','jobName','fileName','createStarted','expectedBillCount','associationVersion','taskId','status') if k in record}}
                    process_day(day,record,lambda r:journal.save(rid,day,r),zto,cost,group,raw_root,resumes.get(day),args.export_root,run_id=rid)
                except Exception as ex:
                    failed=True
                    record.update(status='uncertain' if isinstance(ex,UncertainExport) else 'import_failed' if record.get('status') in ('importing','validated') else 'failed', error=str(ex) if isinstance(ex,SyncError) else '处理异常，请核对服务状态和原始文件')
                record.update(day=str(day),elapsed=round(time.monotonic()-t,2)); journal.save(rid,day,record); records.append(record)
                if record['status']!='done':
                    # 上一天没有完整结束，不创建下一天的任务；不覆盖后续日期原有接续记录。
                    for rest in range(offset+1,(end-start).days+1):
                        records.append({'day':str(start+dt.timedelta(days=rest)),'status':'skipped','error':'上一日未完成，本日未创建导出'})
                    break
            cost.call('/api/v2/batches/finish',{'id':group,'partial':failed})
        except Exception:
            failed=True
            if group:
                try: cost.call('/api/v2/batches/finish',{'id':group,'partial':True})
                except Exception: pass
        finally:
            pieces=[]
            for r in records:
                if r['status']=='done':
                    gap=Decimal(r['feeGap']); p=f"{r['day'][5:]}：{r['rows']:,} 票 ✓ 票数对账一致；新增 {r['new']:,}、替换 {r['replaced']:,}、重复 {r['duplicate']:,}"
                    if gap: p+=f'；账单金额差额 {gap} 元'
                    if r['addedColumns'] or r['missingColumns']: p+='；字段有变化'
                else: p=f"{r['day'][5:]}：失败，{r.get('error','待核查')}"
                pieces.append(p)
            elapsed=int(time.monotonic()-started)
            summary='出港同步 '+('；'.join(pieces) or '未完成，请检查成本服务')+f'；用时 {elapsed//60} 分 {elapsed%60} 秒'+('；本轮有失败' if failed else '')
            journal.finish(rid,'failed' if failed else 'done',summary); journal.close()
        print(summary); return int(failed)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',default=os.environ.get('COST_DATA',str(Path.home()/'cost-studio-data')))
    parser.add_argument('--raw-root',default=os.environ.get('DATA_ROOT',str(Path.home()/'zto-data')))
    parser.add_argument('--cost-url',default='http://127.0.0.1:8888')
    parser.add_argument('--zto-url',default='http://127.0.0.1:9800')
    parser.add_argument('--export-root',default=str(Path.home()/'zto-exports'),help='ZTO-CLI 下载目录；自动返回的文件路径必须位于此目录')
    parser.add_argument('--key-file',default=str(Path.home()/'Library/Application Support/zto-cli/server.json'))
    sub=parser.add_subparsers(dest='command',required=True)
    go=sub.add_parser('run'); go.add_argument('--from',dest='start'); go.add_argument('--to',dest='end')
    go.add_argument('--recheck-days',type=int,default=3)
    go.add_argument('--resume',nargs=3,action='append',default=[],metavar=('DAY','TASK_ID','FILE'),help='核实原任务后接续已有文件，不重复创建导出')
    go.add_argument('--restart-export',action='append',default=[],metavar='DAY',help='人工确认旧任务不再使用后，对指定日期另建导出；旧日志保留')
    status=sub.add_parser('status');status.add_argument('--days',type=int,default=7)
    sub.add_parser('backup')
    args=parser.parse_args()
    try:
        if args.command=='run': return run(args)
        if args.command=='backup':
            client=HTTP(args.cost_url);client.headers['X-App-Token']=client.call('/api/bootstrap')['token']
            result=client.call('/api/v2/backup',{},timeout=600)
            print(f"出港备份完成：{result['file']}；数据版本 {result['dataVersion']}；SHA-256 {result['sha256']}");return 0
        home=Path(args.home).expanduser()
        if not (home/'sync_runs.duckdb').exists(): print('尚无同步记录');return 0
        with process_lock(home):
            journal=Journal(home)
            try:
                rows=journal.con.execute('SELECT payload FROM days WHERE day>=? ORDER BY day DESC,updated DESC',[(dt.datetime.now(SHANGHAI).date()-dt.timedelta(days=max(1,args.days))).isoformat()]).fetchall()
                print(json.dumps([json.loads(r[0]) for r in rows],ensure_ascii=False,indent=2))
            finally: journal.close()
        return 0
    except Exception as ex:
        print('出港任务失败：'+(str(ex) if isinstance(ex,SyncError) else '运行配置或接口异常，请检查本机服务与凭据文件'));return 1


if __name__=='__main__': sys.exit(main())
