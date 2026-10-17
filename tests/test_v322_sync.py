"""唯一导出任务关联；全部为合成接口响应，不访问真实服务或正式库。"""
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch, call

from scripts import daily_sync as sync


class NamedExport(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.day=dt.date(2026,10,12);self.rid='12345678'+'a'*24
        self.record={};self.saved=[];self.sleep=Mock();self.client=Mock()
    def tearDown(self):self.tmp.cleanup()
    def save(self,r):self.saved.append(dict(r))
    def rows(self,*rows,total=None):return {'data':{'total':len(rows) if total is None else total,'data':list(rows)}}
    def target(self,**fields):return {'id':'own-task','taskName':self.record['fileName']+'_网点业务账单运单明细_261017140000.xlsx','status':3,**fields}
    def export(self):return sync.export_file(self.client,self.day,self.record,self.save,self.rid,self.sleep,self.root)
    def seed(self):self.record.update(jobName=f'出港账单(包补收)-业务账单-成本同步-{self.day}-{self.rid[:8]}',createStarted=sync.now());self.record['fileName']=self.record['jobName']

    def test_newer_unrelated_tasks_and_creation_return_id_are_ignored(self):
        def api(path,body,**kwargs):
            if 'create-export-task' in path:
                self.assertEqual(self.saved[-1]['jobName'],body['jobName'])
                self.assertEqual(body,{**sync.window(self.day),'jobName':f'出港账单(包补收)-业务账单-成本同步-{self.day}-12345678','fileName':self.record['fileName']})
                return {'data':{'results':{'createJob':{'taskId':'wrong-latest-task'}}}}
            self.assertIn('fetchTaskApi',path)
            return self.rows({'id':'newer-other-task','taskName':'别人的任务','status':3},
                             self.target(),{'id':'prefix-only','taskName':self.record['fileName']+'-other_file.xlsx','status':3})
        self.client.call.side_effect=api
        with patch.object(sync,'download_task_file') as download:
            path,task=self.export()
        self.assertEqual(task,'own-task');download.assert_called_once_with(self.client,task,self.root.resolve()/'sync-own-task.xlsx')
        self.assertEqual(self.record['matchedFileName'],self.record['fileName']);self.sleep.assert_not_called()
        self.assertEqual(self.record['exportRunId'],self.rid)
        self.assertFalse(any('one-click' in c.args[0] for c in self.client.call.call_args_list))

    def test_filename_prefix_requires_start_and_immediate_underscore(self):
        self.seed();name=self.record['fileName']
        self.client.call.return_value=self.rows(
            self.target(id='wrong-1',taskName='prefix-'+name+'_file.xlsx'),
            self.target(id='wrong-2',taskName=name+'-other_file.xlsx'),
            self.target(id='wrong-3',taskName=name),
            self.target(id='wrong-4',taskName=None,jobName=name),
            self.target(id='own-task',taskName=name+'_file.xlsx'))
        self.assertEqual(sync.matching_export(self.client,self.record)['id'],'own-task')

    def test_unique_match_on_later_page_and_duplicate_on_later_page(self):
        self.seed()
        first=self.rows({'id':'other','taskName':'other','status':3},total=2)
        self.client.call.side_effect=[first,self.rows(self.target(),total=2)]
        self.assertEqual(sync.matching_export(self.client,self.record)['id'],'own-task')
        self.assertEqual([c.args[1]['pageDTO']['pageNum'] for c in self.client.call.call_args_list],[1,2])
        self.client.call.side_effect=[self.rows(self.target(),total=2),self.rows(self.target(id='duplicate-name'),total=2)]
        with self.assertRaisesRegex(sync.UncertainExport,'多条同名'):sync.matching_export(self.client,self.record)

    def test_repeated_or_incomplete_pagination_fails_closed(self):
        self.seed()
        for responses in ([self.rows(self.target(),total=2),self.rows(self.target(),total=2)],
                          [self.rows(self.target(),total=2),self.rows(total=2)],
                          [self.rows(self.target(),total=2),self.rows(self.target(id='other'),total=3)],
                          [{'data':{'total':1,'data':None}}]):
            self.client.call.side_effect=responses
            with self.assertRaisesRegex(sync.UncertainExport,'列表不完整'):sync.matching_export(self.client,self.record)

    def test_visibility_delay_and_pending_wait_then_success(self):
        self.seed();self.client.call.side_effect=[self.rows(),self.rows(self.target(status=2)),self.rows(self.target())]
        with patch.object(sync,'download_task_file'):self.export()
        self.assertEqual(self.sleep.call_args_list,[call(5)]*2)
        self.assertEqual(self.client.call.call_count,3)

    def test_missing_name_fails_without_download_or_recreation(self):
        self.seed();self.client.call.return_value=self.rows({'id':'latest','taskName':'other','status':3})
        with patch.object(sync,'download_task_file') as download:
            with self.assertRaisesRegex(sync.UncertainExport,'找不到完整同名'):self.export()
            download.assert_not_called()
        self.assertEqual(self.client.call.call_count,13);self.assertEqual(self.sleep.call_count,12)
        self.assertTrue(all('fetchTaskApi' in c.args[0] for c in self.client.call.call_args_list))

    def test_duplicate_failed_changed_id_and_unknown_status_do_not_download(self):
        self.seed()
        cases=[(self.rows(self.target(),self.target(id='second')),'多条同名'),
               (self.rows(self.target(status=4)),'原导出任务失败'),
               (self.rows(self.target(status=1)),'等待超时')]
        for response,reason in cases:
            self.record.pop('taskId',None);self.client.call.return_value=response
            with patch.object(sync,'download_task_file') as download:
                with self.assertRaisesRegex(sync.UncertainExport,reason):self.export()
                download.assert_not_called()
        self.record['taskId']='pinned-original';self.client.call.return_value=self.rows(self.target())
        with self.assertRaisesRegex(sync.UncertainExport,'标识已变化'):self.export()

    def test_creation_timeout_is_persisted_and_next_run_resumes_original_name(self):
        self.client.call.side_effect=sync.UncertainExport('timeout')
        with self.assertRaisesRegex(sync.UncertainExport,'创建响应未确认'):self.export()
        self.assertEqual(self.client.call.call_count,1);name=self.record['jobName']
        self.client.call.reset_mock();self.client.call.side_effect=None;self.client.call.return_value=self.rows(self.target())
        with patch.object(sync,'download_task_file'):
            sync.export_file(self.client,self.day,self.record,self.save,'different-run-id',self.sleep,self.root)
        self.assertEqual(self.record['jobName'],name)
        self.assertEqual(self.client.call.call_count,1);self.assertIn('fetchTaskApi',self.client.call.call_args.args[0])

    def test_legacy_jobname_without_filename_does_not_create_or_guess(self):
        self.seed();self.record.pop('fileName')
        with self.assertRaisesRegex(sync.UncertainExport,'旧任务没有唯一 fileName'):self.export()
        self.client.call.assert_not_called()

    def test_business_days_and_runs_have_different_names(self):
        self.client.call.side_effect=sync.UncertainExport('timeout');names=[]
        for day,rid in ((self.day,self.rid),(self.day+dt.timedelta(days=1),self.rid),(self.day,'87654321'+'b'*24)):
            record={}
            with self.assertRaises(sync.UncertainExport):sync.export_file(self.client,day,record,self.save,rid,self.sleep,self.root)
            names.append(record['jobName'])
        self.assertEqual(len(set(names)),3)

    def test_matching_failure_reaches_run_summary_journal_and_exit_code(self):
        import io
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        key=self.root/'key.json';key.write_text(json.dumps({'apiKey':'synthetic-key'}))
        args=SimpleNamespace(home=str(self.root/'sync'),raw_root=str(self.root/'raw'),start=str(self.day),end=str(self.day),recheck_days=3,resume=[],key_file=str(key),zto_url='http://127.0.0.1:9800',cost_url='http://127.0.0.1:8888',export_root=str(self.root))
        cost=Mock();cost.headers={}
        cost.call.side_effect=lambda p,*a,**k:{'/api/health':{'ok':True,'version':'3.2.2'},'/api/bootstrap':{'token':'test'},'/api/v2/batches/start':{'id':'g'},'/api/v2/batches/finish':{}}[p]
        def api(path,body,**kwargs):
            if 'create-export-task' in path:
                self.record.update(jobName=body['jobName'],fileName=body['fileName']);return {}
            return self.rows(self.target(),self.target(id='ambiguous-second'))
        self.client.call.side_effect=api
        with patch.object(sync,'HTTP',side_effect=[self.client,cost]*3),patch.object(sync.time,'sleep'):
            out=io.StringIO()
            with redirect_stdout(out):self.assertEqual(sync.run(args),1)
            first_name=self.record['jobName']
            with redirect_stdout(out):self.assertEqual(sync.run(args),1)
            self.assertEqual(sum('create-export-task' in c.args[0] for c in self.client.call.call_args_list),1)
            args.restart_export=[str(self.day)]
            with redirect_stdout(out):self.assertEqual(sync.run(args),1)
            self.assertNotEqual(self.record['jobName'],first_name)
            self.assertEqual(sum('create-export-task' in c.args[0] for c in self.client.call.call_args_list),2)
            journal=sync.Journal(self.root/'sync')
            try:
                self.assertEqual(journal.previous(self.day)['supersedes']['jobName'],first_name)
                self.assertEqual(journal.con.execute('SELECT count(*) FROM days').fetchone()[0],3)
            finally:journal.close()
        self.assertIn('多条同名',out.getvalue());self.assertIn('jobName=',out.getvalue());cost.upload.assert_not_called()
        last=json.loads((self.root/'sync/sync_latest.json').read_text());self.assertEqual(last['status'],'failed')
        self.assertIn('多条同名',last['summary']);self.assertNotIn('synthetic-key',out.getvalue())

if __name__=='__main__':unittest.main()
