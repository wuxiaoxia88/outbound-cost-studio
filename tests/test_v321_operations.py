"""业务日边界、迁移、同步、备份及部署材料的隔离测试（全部为合成数据）。"""
import datetime as dt
from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
import duckdb

from server import workbook,Handler,ThreadingHTTPServer
from v2 import business_day,imports,operations,routes
from v2.store import Warehouse
from test_v2_engine import H,row,ROOT
from scripts import daily_sync as sync,watchdog,prepare_232


class Operations(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.wh=Warehouse(self.root/'v2');self.i=0
    def tearDown(self):self.wh.con.close();self.tmp.cleanup()
    def load(self,rows,group=None):
        self.i+=1;p=self.root/f'{self.i}.xlsx';p.write_bytes(workbook(H,rows));return self.wh.import_file(p,group_id=group)
    def test_boundaries_excel_serial_missing_invalid(self):
        values=['2026-10-15 11:59:59','2026-10-15 12:00:00','2026-10-16 11:59:59','2026-10-16 12:00:00',
                '2026/10/15 12:00:00','2026-10-15T12:00:00',str((dt.datetime(2026,10,15,12)-dt.datetime(1899,12,30)).total_seconds()/86400),'','invalid','999999999999999999999']
        query='SELECT '+business_day.day_sql('v')+' FROM (SELECT unnest(?) v)'
        got=[str(x[0]) if x[0] else None for x in self.wh.con.execute(query,[values]).fetchall()]
        self.assertEqual(got,['2026-10-14','2026-10-15','2026-10-15','2026-10-16','2026-10-15','2026-10-15','2026-10-15',None,None,None])
    def test_migration_once_undo_old_snapshot_and_config_preserved(self):
        first=self.load([row('synthetic-1',2,hand='2026-10-01 01:00:00')]);second=self.load([row('synthetic-1',3,hand='2026-10-01 01:00:00')])
        c=self.wh.con;c.execute("UPDATE ship SET day='2026-10-01'")
        old=c.execute('SELECT event_id,row_id,before_row FROM import_before').fetchall()
        for eid,rid,raw in old:
            before=json.loads(raw);before['day']='2026-10-01';c.execute('UPDATE import_before SET before_row=? WHERE event_id=? AND row_id=?',[json.dumps(before),eid,rid])
        c.execute('DELETE FROM meta WHERE key=?',[business_day.MARKER]);v=self.wh.version();cfg=self.wh.config();send=c.execute('SELECT send_day,fee,state FROM ship ORDER BY id').fetchall()
        business_day.migrate(self.wh)
        self.assertEqual(self.wh.version(),v+1);self.assertEqual(self.wh.config(),cfg)
        self.assertEqual(send,c.execute('SELECT send_day,fee,state FROM ship ORDER BY id').fetchall())
        self.assertEqual(c.execute('SELECT DISTINCT day FROM ship').fetchall(),[(dt.date(2026,9,30),)])
        self.assertEqual(imports.read(self.wh,second['groupId'])['dateRanges']['handover']['end'],'2026-09-30')
        business_day.migrate(self.wh);self.assertEqual(self.wh.version(),v+1)
        imports.undo(self.wh,{'id':second['groupId'],'expectedVersion':v+1})
        self.assertEqual(c.execute('SELECT day,fee FROM ship WHERE state=1').fetchone(),(dt.date(2026,9,30),Decimal(2)))
    def test_failed_migration_is_atomic(self):
        self.load([row('synthetic-1',2)]);c=self.wh.con;c.execute('DELETE FROM meta WHERE key=?',[business_day.MARKER]);c.execute('DELETE FROM raw')
        v=self.wh.version();before=c.execute('SELECT day FROM ship').fetchall()
        with self.assertRaises(ValueError):business_day.migrate(self.wh)
        self.assertEqual(self.wh.version(),v);self.assertEqual(c.execute('SELECT day FROM ship').fetchall(),before)
        self.assertIsNone(c.execute('SELECT 1 FROM meta WHERE key=?',[business_day.MARKER]).fetchone())
    def test_group_replace_override_does_not_change_config(self):
        self.wh.save_config({**self.wh.config(),'conflictRule':'review'});self.load([row('synthetic-1',2)])
        gid=imports.create(self.wh,{'name':'自动同步 合成日期','conflictRule':'replace'})['id'];self.load([row('synthetic-1',3)],gid);imports.finish(self.wh,{'id':gid})
        self.assertEqual(self.wh.config()['conflictRule'],'review');self.assertEqual(self.wh.pending({})['total'],0)
        self.assertEqual(imports.read(self.wh,gid)['name'],'自动同步 合成日期')
        with self.assertRaises(ValueError):imports.create(self.wh,{'conflictRule':'bad'})
    def test_backup_restore_retention_version_and_lock(self):
        self.load([row('synthetic-1',2)]);v=self.wh.version();results=[]
        self.wh.wlock.acquire()
        thread=threading.Thread(target=lambda:results.append(operations.backup(self.wh,dt.date(2026,1,1))));thread.start();time.sleep(.05)
        self.assertTrue(thread.is_alive());self.wh.wlock.release();thread.join(10);self.assertFalse(thread.is_alive())
        for d in (2,3,4):operations.backup(self.wh,dt.date(2026,1,d))
        self.assertEqual(sorted(p.name for p in (self.root/'backups').iterdir()),['20260102.duckdb','20260103.duckdb','20260104.duckdb'])
        self.assertEqual(self.wh.version(),v)
        with duckdb.connect(str(self.root/'backups/20260104.duckdb'),read_only=True) as c:self.assertEqual(c.execute('SELECT count(*) FROM ship').fetchone()[0],1)
    def test_saved_quote_keeps_original_handover_definition(self):
        from v2 import quotation
        self.load([row('q'+str(i),2) for i in range(30)])
        saved=quotation.save(self.wh,{'expectedVersion':self.wh.version(),'request':{'target':'合成报价','dateBasis':'handover'}})
        current=quotation.export(self.wh,saved['id'])[1][2]
        self.assertIn(['日期口径','交件（业务日 12:00–次日 12:00）'],current)
        result=saved['result'];result.pop('handoverBusinessDay')
        self.wh.con.execute('UPDATE quotes SET result=? WHERE id=?',[json.dumps(result),saved['id']])
        legacy=quotation.export(self.wh,saved['id'])[1][2]
        self.assertIn(['日期口径','交件日期（历史自然日口径）'],legacy)

    def test_hosts_origins_tokens_and_health(self):
        old=routes._warehouse;routes._warehouse=self.wh
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.data_dir=self.root;server.token='synthetic-test-token';server.allowed_hosts={'cost.example.test','127.0.0.1:'+str(server.server_port)}
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url='http://127.0.0.1:'+str(server.server_port)
            def call(host,path='/api/health',body=None,token=None,origin=None):
                h={'Host':host}
                if token:h['X-App-Token']=token
                if origin:h['Origin']=origin
                return urllib.request.urlopen(urllib.request.Request(url+path,data=body,headers=h))
            with call('cost.example.test') as response:self.assertEqual(json.load(response)['version'],'3.2.1')
            with self.assertRaises(urllib.error.HTTPError):call('evil.example')
            with self.assertRaises(urllib.error.HTTPError):call('cost.example.test','/api/v2/backup',b'{}')
            with self.assertRaises(urllib.error.HTTPError):call('cost.example.test','/api/v2/backup',b'{}',server.token,'https://evil.example')
            with call('cost.example.test','/api/v2/backup',b'{}',server.token,'https://cost.example.test') as response:self.assertTrue(json.load(response)['ok'])
            sync.atomic_json(self.root/'sync_latest.json',{'status':'failed','summary':'测试','key':'不允许返回'})
            self.assertEqual(operations.health(self.wh)['lastSync'],{'status':'failed','summary':'测试'})
        finally:server.shutdown();server.server_close();thread.join();routes._warehouse=old


@unittest.skipIf(os.name=='nt','每日同步和 launchd 部署仅在目标 Mac 运行')
class SyncTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.day=dt.date(2026,10,15)
    def tearDown(self):self.tmp.cleanup()
    def file(self,rows,headers=H,name='raw.xlsx'):
        path=self.root/name;path.write_bytes(workbook(headers,rows));return path
    def test_error_image_never_reaches_import(self):
        from unittest.mock import Mock
        f=self.root/'error.xlsx';f.write_bytes(b'\xff\xd8\xfffake-error-image')
        cost=Mock();record={}
        with self.assertRaises(sync.UncertainExport):
            sync.process_day(self.day,record,lambda r:None,Mock(),cost,'g',self.root/'raw',('test-task',str(f)))
        cost.upload.assert_not_called();self.assertTrue(f.exists());self.assertEqual(record['taskId'],'test-task')

    def test_rows_headers_and_reconciliation(self):
        file=self.file([row('test-1',2,hand='2026-10-15 12:00:00'),row('test-2',3,hand='2026-10-16 11:59:59')])
        r=sync.inspect_file(file,self.day);self.assertEqual((r['rows'],r['unique'],r['outsideWindow'],r['fileTotalFee']),(2,2,0,'5'))
        self.assertIn('退件费',r['missingColumns'])
        f=self.file([row('test-3',2,hand='2026-10-16 12:00:00')],name='bad.xlsx');self.assertEqual(sync.inspect_file(f,self.day)['outsideWindow'],1)
        missing=self.file([['test-4']],['运单号'],'missing.xlsx')
        with self.assertRaises(sync.SyncError):sync.inspect_file(missing,self.day)
    def test_export_retry_and_uncertain_is_not_resubmitted(self):
        from unittest.mock import Mock
        client=Mock();client.call.side_effect=[sync.SyncError('fail'),sync.SyncError('fail'),{'data':{'results':{'createJob':{'filePath':'/tmp/test.xlsx','taskId':'test-task'}}}}]
        sleep=Mock();self.assertEqual(sync.export_file(client,self.day,sleep,download_root='/tmp')[1],'test-task');self.assertEqual(sleep.call_count,2)
        client.call.side_effect=sync.UncertainExport('unknown');client.call.reset_mock()
        with self.assertRaises(sync.UncertainExport):sync.export_file(client,self.day,sleep)
        self.assertEqual(client.call.call_count,1)
    def test_archive_move_idempotent_and_conflict(self):
        src=self.root/'source.xlsx';src.write_bytes(b'first');dest=self.root/'target.xlsx'
        sync.archive(src,dest);self.assertFalse(src.exists());self.assertEqual(dest.read_bytes(),b'first')
        sync.archive(src,dest);src.write_bytes(b'changed')
        with self.assertRaises(sync.SyncError):sync.archive(src,dest)
        self.assertEqual(src.read_bytes(),b'changed')
    def test_lock_and_journal_no_warehouse(self):
        with sync.process_lock(self.root):
            with self.assertRaises(sync.SyncError):
                with sync.process_lock(self.root):pass
        j=sync.Journal(self.root);j.start('test');j.save('test',self.day,{'status':'failed','taskId':'test-task','rows':2});j.finish('test','failed','出港同步失败')
        self.assertEqual(j.previous(self.day)['rows'],2);j.close()
        self.assertFalse((self.root/'v2').exists());self.assertEqual(json.loads((self.root/'sync_latest.json').read_text())['status'],'failed')
    def test_process_import_dedup_and_replace_end_to_end(self):
        wh=Warehouse(self.root/'cost/v2');old=routes._warehouse;routes._warehouse=wh
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.data_dir=self.root/'cost';server.token='test-token'
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        cost=sync.HTTP('http://127.0.0.1:'+str(server.server_port),{'X-App-Token':server.token})
        class Zto:
            def __init__(self,outer):self.outer=outer;self.calls=0;self.fee=2;self.count=1
            def call(self,path,body,timeout=60):
                if 'one-click' in path:
                    self.calls+=1
                    f=self.outer.file([row('test-1',self.fee,hand='2026-10-16 01:00:00')],name=f'export{self.calls}.xlsx')
                    return {'data':{'results':{'createJob':{'taskId':f'task{self.calls}','filePath':str(f)}}}}
                return {'data':{'billCount':self.count,'totalFee':str(self.fee+Decimal('.01'))}}
        zto=Zto(self)
        try:
            versions=[]
            for fee in (2,2,3):
                zto.fee=fee;gid=cost.call('/api/v2/batches/start',{'conflictRule':'replace','name':'合成同步'})['id'];saved=[]
                record=sync.process_day(self.day,{},lambda r:saved.append(dict(r)),zto,cost,gid,self.root/'raw',download_root=self.root)
                cost.call('/api/v2/batches/finish',{'id':gid});versions.append(wh.version())
                self.assertEqual(record['status'],'done');self.assertEqual(record['feeGap'],'-0.01')
                self.assertEqual(wh.con.execute('SELECT count(*) FROM ship WHERE state=1').fetchone()[0],1)
            self.assertEqual(versions,[1,1,2]);self.assertEqual(record['replaced'],1);self.assertEqual(record['new'],0)
            self.assertEqual(wh.con.execute('SELECT day FROM ship WHERE state=1').fetchone()[0],self.day)
            gid=cost.call('/api/v2/batches/start',{})['id'];zto.count=2
            with self.assertRaises(sync.SyncError):sync.process_day(self.day,{},lambda r:None,zto,cost,gid,self.root/'raw',download_root=self.root)
            self.assertEqual(wh.version(),2)
        finally:server.shutdown();server.server_close();thread.join();routes._warehouse=old;wh.con.close()
    def test_watchdog_cooldown_and_recovery(self):
        calls=[];restart=lambda:calls.append(True) or True;s={}
        for t in (1000,1060):s=watchdog.tick(s,False,t,restart)
        self.assertFalse(calls);s=watchdog.tick(s,False,1120,restart);self.assertEqual(len(calls),1)
        for t in (1180,1240,1300,1360):s=watchdog.tick(s,False,t,restart)
        self.assertEqual(len(calls),1);s=watchdog.tick(s,True,1420,restart);self.assertEqual(s['failures'],0)
        for t in (1480,1540,1600):s=watchdog.tick(s,False,t,restart)
        self.assertEqual(len(calls),2)
    def test_plists_are_explicit_and_guard_root_code(self):
        main,guard=prepare_232.render(Path('/Users/test'), 'test','192.0.2.2','cost.example.test')
        self.assertEqual(main['ThrottleInterval'],10);self.assertTrue(main['KeepAlive']);self.assertEqual(main['UserName'],'test')
        self.assertEqual(main['ProgramArguments'].count('--allow-host'),4);self.assertEqual(guard['StartInterval'],60)
        self.assertIn('/Library/Application Support/CostStudio/watchdog.py',guard['ProgramArguments']);self.assertNotIn('UserName',guard)
    def test_full_run_logs_summary_exit_code_and_health(self):
        from types import SimpleNamespace
        from http.server import BaseHTTPRequestHandler
        from contextlib import redirect_stdout
        import io
        wh=Warehouse(self.root/'cost/v2');old=routes._warehouse;routes._warehouse=wh
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.data_dir=self.root/'cost';server.token='test-token'
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        outer=self
        class FakeZto(BaseHTTPRequestHandler):
            sequence=0; mismatch=False
            def log_message(self,*args):pass
            def do_POST(self):
                self.sequence+=1
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.server.requests.append(payload)
                assert self.headers.get('X-API-Key')=='synthetic-key'
                if 'one-click' in self.path:
                    day=payload['startTime'][:10];f=outer.file([row('daily-'+day,2,hand=payload['startTime'])],name='job-'+str(len(self.server.requests))+'.xlsx')
                    data={'results':{'createJob':{'ok':True,'taskId':'task-'+str(len(self.server.requests)),'filePath':str(f)}}}
                else:data={'billCount':2 if self.mismatch else 1,'totalFee':2}
                body=json.dumps({'code':200,'status':'success','data':data}).encode();self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        upstream=ThreadingHTTPServer(('127.0.0.1',0),FakeZto);upstream.requests=[]
        t=threading.Thread(target=upstream.serve_forever,daemon=True);t.start()
        key=self.root/'key.json';key.write_text(json.dumps({'apiKey':'synthetic-key'}))
        day=sync.dt.datetime.now(sync.SHANGHAI).date()-dt.timedelta(days=1)
        args=SimpleNamespace(export_root=str(self.root),home=str(self.root/'cost'),raw_root=str(self.root/'archive'),start=str(day-dt.timedelta(days=2)),end=str(day),recheck_days=3,resume=[],key_file=str(key),zto_url='http://127.0.0.1:'+str(upstream.server_port),cost_url='http://127.0.0.1:'+str(server.server_port))
        try:
            for i in range(2):
                output=io.StringIO()
                with redirect_stdout(output):code=sync.run(args)
                self.assertEqual(code,0);self.assertEqual(len(output.getvalue().splitlines()),1);self.assertEqual(wh.version(),3)
                self.assertNotIn('synthetic-key',output.getvalue());self.assertEqual(operations.health(wh)['lastSync']['status'],'done')
            self.assertEqual(wh.con.execute('SELECT count(*) FROM ship WHERE state=1').fetchone()[0],3)
            FakeZto.mismatch=True
            with redirect_stdout(io.StringIO()):self.assertEqual(sync.run(args),1)
            self.assertEqual(operations.health(wh)['lastSync']['status'],'failed');self.assertEqual(wh.version(),3)
            self.assertTrue(all(x['queryDateType']==2 and x['startTime'].endswith('12:00:00') for x in upstream.requests))
        finally:
            upstream.shutdown();upstream.server_close();t.join();server.shutdown();server.server_close();thread.join();routes._warehouse=old;wh.con.close()

    def test_private_key_never_sent_to_nonlocal_address(self):
        for url in ('https://public.example','http://192.0.2.2:9800','http://user:secret@127.0.0.1:9800'):
            with self.assertRaises(sync.SyncError):sync.HTTP(url,{'X-API-Key':'test'})

if __name__=='__main__':unittest.main()

@unittest.skipIf(os.name=='nt','launchd 仅在 Mac 运行')
class DeploymentRollback(unittest.TestCase):
    def test_failed_health_restores_link_database_and_wal(self):
        from scripts import activate_release as deploy
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();base=root/'app';data=root/'data';(data/'v2').mkdir(parents=True)
            old=base/'releases/v3.2.0';new=base/'releases/v3.2.1';old.mkdir(parents=True);new.mkdir()
            (base/'current').symlink_to(old);db=data/'v2/warehouse.duckdb';db.write_bytes(b'old database');wal=data/'v2/warehouse.duckdb.wal';wal.write_bytes(b'old wal')
            calls=[]
            def cmd(*args):
                calls.append([str(x) for x in args])
                if 'bootstrap' in args and (base/'current').resolve()==new:db.write_bytes(b'migrated');wal.unlink(missing_ok=True)
            with patch.object(deploy,'command',side_effect=cmd),patch.object(deploy,'wait_health',side_effect=RuntimeError('health failed')),patch.object(deploy.subprocess,'run'),patch.object(deploy,'wait_port_free'):
                with self.assertRaises(RuntimeError):deploy.activate(base,data,new,root/'main.plist',root/'watch.plist')
            self.assertEqual((base/'current').resolve(),old);self.assertEqual(db.read_bytes(),b'old database');self.assertEqual(wal.read_bytes(),b'old wal')
            self.assertEqual(calls[-1][-1],str(root/'watch.plist'))
