"""创建时刻+行数关联的合成测试；不访问真实平台或正式库。"""
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch, call

from scripts import daily_sync as sync


class ExportAssociation(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.day=dt.date(2026,10,12);self.rid='12345678'+'a'*24
        self.record={};self.saved=[];self.sleep=Mock();self.client=Mock()
        self.base=dt.datetime.now(sync.SHANGHAI).replace(microsecond=0)
        self.clock=patch.object(sync,'now',return_value=self.base.isoformat());self.clock.start()
    def tearDown(self):self.clock.stop();self.tmp.cleanup()
    def save(self,r):self.saved.append(dict(r))
    def rows(self,*rows,total=None):return {'data':{'total':len(rows) if total is None else total,'data':list(rows)}}
    def target(self,**fields):
        return {'id':'own-task','taskName':sync.EXPORT_PREFIX+'261017140000.xlsx','status':3,
                'createTime':self.base.strftime('%Y-%m-%d %H:%M:%S'),'total':100,**fields}
    def export(self):return sync.export_file(self.client,self.day,self.record,self.save,self.rid,self.sleep,self.root)
    def seed(self):self.record.update(associationVersion=sync.ASSOCIATION_VERSION,expectedBillCount=100,createStarted=self.base.isoformat(),exportRunId=self.rid)

    def test_count_then_t0_then_create_ignores_other_newer_tasks_and_response_id(self):
        def api(path,body,**kwargs):
            if 'outbound-bill/query' in path:
                self.assertFalse(self.record.get('createStarted'));return {'data':{'billCount':'100','totalFee':'201.234'}}
            if 'create-export-task' in path:
                self.assertEqual(self.saved[-1]['expectedBillCount'],100)
                self.assertEqual(self.saved[-1]['createStarted'],self.base.isoformat())
                self.assertEqual(body,sync.window(self.day))  # 不传自定义名称。
                return {'data':{'results':{'createJob':{'taskId':'wrong-latest-task'}}}}
            return self.rows(self.target(id='newer-other',total=101),self.target(),
                             self.target(id='previous-day',createTime=(self.base-dt.timedelta(seconds=6)).isoformat()))
        self.client.call.side_effect=api
        with patch.object(sync,'download_task_file') as download:path,task=self.export()
        self.assertEqual(task,'own-task');download.assert_called_once_with(self.client,task,self.root.resolve()/'sync-own-task.xlsx')
        self.assertEqual(self.record['taskTotal'],100);self.assertEqual(self.record['billTotalFeeBefore'],'201.234')
        self.assertFalse(any('one-click' in c.args[0] for c in self.client.call.call_args_list))
        self.assertEqual(self.record['exportRunId'],self.rid);self.sleep.assert_not_called()

    def test_time_window_inclusive_prefix_and_exact_count(self):
        self.seed()
        others=[self.target(id='wrong-prefix',taskName='other_'+sync.EXPORT_PREFIX),
                self.target(id='short-prefix',taskName='出港账单(包补收)-业务账单'),
                self.target(id='early',createTime=(self.base-dt.timedelta(seconds=5,microseconds=1)).isoformat()),
                self.target(id='late',createTime=(self.base+dt.timedelta(seconds=120,microseconds=1)).isoformat()),
                self.target(id='other-count',total=99),self.target(id='not-ready',total=None),
                self.target(id='invalid-time',createTime='invalid'),self.target(id='bool-count',total=True)]
        for seconds in (-5,0,120):
            self.client.call.return_value=self.rows(*others,self.target(createTime=(self.base+dt.timedelta(seconds=seconds)).isoformat(),total='100'))
            self.assertEqual(sync.matching_export(self.client,self.record)['id'],'own-task')
        # 同一时刻的 UTC 表达也必须按北京时间正确比较。
        self.client.call.return_value=self.rows(self.target(createTime=self.base.astimezone(dt.timezone.utc).isoformat()))
        self.assertEqual(sync.matching_export(self.client,self.record)['id'],'own-task')

    def test_unique_match_and_ambiguity_across_pages(self):
        self.seed();first=self.rows(self.target(id='other',total=2),total=2)
        self.client.call.side_effect=[first,self.rows(self.target(),total=2)]
        self.assertEqual(sync.matching_export(self.client,self.record)['id'],'own-task')
        calls=self.client.call.call_args_list
        self.assertEqual([c.args[1]['pageDTO']['pageNum'] for c in calls],[1,2])
        self.assertEqual(calls[0].args[1]['fetchTaskDTO'],calls[1].args[1]['fetchTaskDTO'])
        self.client.call.side_effect=[self.rows(self.target(),total=2),self.rows(self.target(id='second'),total=2)]
        with self.assertRaisesRegex(sync.UncertainExport,'多条任务满足'):sync.matching_export(self.client,self.record)

    def test_repeated_incomplete_or_changed_pagination_fails_closed(self):
        self.seed()
        for responses in ([self.rows(self.target(),total=2),self.rows(self.target(),total=2)],
                          [self.rows(self.target(),total=2),self.rows(total=2)],
                          [self.rows(self.target(),total=2),self.rows(self.target(id='other'),total=3)],
                          [{'data':{'total':1,'data':None}}]):
            self.client.call.side_effect=responses
            with self.assertRaisesRegex(sync.UncertainExport,'列表不完整'):sync.matching_export(self.client,self.record)

    def test_total_pending_then_status_two_then_success(self):
        self.seed();self.client.call.side_effect=[self.rows(self.target(status=2,total=None)),self.rows(self.target(status=2)),self.rows(self.target())]
        with patch.object(sync,'download_task_file') as download:self.export()
        download.assert_called_once();self.assertEqual(self.sleep.call_args_list,[call(5)]*2)

    def test_zero_candidates_waits_five_minutes_and_never_downloads(self):
        self.seed();self.client.call.return_value=self.rows(self.target(total=101))
        with patch.object(sync,'download_task_file') as download:
            with self.assertRaisesRegex(sync.UncertainExport,'5分钟内未找到'):self.export()
            download.assert_not_called()
        self.assertEqual(self.client.call.call_count,61);self.assertEqual(self.sleep.call_args_list,[call(5)]*60)
        self.assertTrue(all('fetchTaskApi' in c.args[0] for c in self.client.call.call_args_list))
        # 接续不重新获得5分钟预算，也不再次创建。
        self.client.call.reset_mock();self.sleep.reset_mock()
        with patch.object(sync,'now',return_value=(self.base+dt.timedelta(minutes=6)).isoformat()):
            with self.assertRaisesRegex(sync.UncertainExport,'5分钟内未找到'):self.export()
        self.assertEqual(self.client.call.call_count,1);self.sleep.assert_not_called()

    def test_ambiguous_failed_changed_id_and_unknown_status_do_not_download(self):
        self.seed()
        cases=[(self.rows(self.target(),self.target(id='second')),'多条任务满足'),
               (self.rows(self.target(status=4)),'原导出任务失败'),
               (self.rows(self.target(status=1)),'等待超时')]
        for response,reason in cases:
            self.record.pop('taskId',None);self.client.call.return_value=response
            with patch.object(sync,'download_task_file') as download:
                with self.assertRaisesRegex(sync.UncertainExport,reason):self.export()
                download.assert_not_called()
        self.record['taskId']='pinned-original';self.client.call.return_value=self.rows(self.target())
        with self.assertRaisesRegex(sync.UncertainExport,'标识已变化'):self.export()

    def test_creation_timeout_resumes_original_window_and_count_without_requery_or_create(self):
        self.client.call.side_effect=[{'data':{'billCount':100,'totalFee':200}},sync.UncertainExport('timeout')]
        with self.assertRaisesRegex(sync.UncertainExport,'创建响应未确认'):self.export()
        self.assertEqual(self.client.call.call_count,2);t0=self.record['createStarted']
        self.client.call.reset_mock();self.client.call.side_effect=None;self.client.call.return_value=self.rows(self.target())
        with patch.object(sync,'now',return_value=(self.base+dt.timedelta(minutes=8)).isoformat()),patch.object(sync,'download_task_file'):
            sync.export_file(self.client,self.day,self.record,self.save,'different-run',self.sleep,self.root)
        self.assertEqual(self.record['createStarted'],t0);self.assertEqual(self.record['expectedBillCount'],100)
        self.assertEqual(self.client.call.call_count,1);self.assertIn('fetchTaskApi',self.client.call.call_args.args[0])

    def test_invalid_count_fails_before_creating_and_legacy_cannot_reassociate(self):
        for n in (None,True,-1,'NaN','Infinity','1.5'):
            self.record.clear();self.client.call.return_value={'data':{'billCount':n,'totalFee':2}}
            with self.assertRaises(sync.SyncError):self.export()
            self.assertNotIn('createStarted',self.record)
        self.client.call.reset_mock();self.record.update(createStarted=self.base.isoformat(),jobName='old')
        with self.assertRaisesRegex(sync.UncertainExport,'旧任务缺少创建前票数'):self.export()
        self.client.call.assert_not_called()

    def test_failure_summary_restart_history_and_stop_next_day(self):
        import io
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        key=self.root/'key.json';key.write_text(json.dumps({'apiKey':'synthetic-key'}))
        args=SimpleNamespace(home=str(self.root/'sync'),raw_root=str(self.root/'raw'),start=str(self.day),end=str(self.day+dt.timedelta(days=1)),recheck_days=3,resume=[],key_file=str(key),zto_url='http://127.0.0.1:9800',cost_url='http://127.0.0.1:8888',export_root=str(self.root))
        cost=Mock();cost.headers={}
        cost.call.side_effect=lambda p,*a,**k:{'/api/health':{'ok':True,'version':'3.2.2'},'/api/bootstrap':{'token':'test'},'/api/v2/batches/start':{'id':'g'},'/api/v2/batches/finish':{}}[p]
        def api(path,body,**kwargs):
            if 'outbound-bill/query' in path:return {'data':{'billCount':100,'totalFee':200}}
            if 'create-export-task' in path:return {}
            return self.rows(self.target(),self.target(id='ambiguous-second'))
        self.client.call.side_effect=api
        with patch.object(sync,'now',side_effect=lambda:dt.datetime.now(sync.SHANGHAI).isoformat()),patch.object(sync,'HTTP',side_effect=[self.client,cost]*3),patch.object(sync.time,'sleep'):
            out=io.StringIO()
            with redirect_stdout(out):self.assertEqual(sync.run(args),1)
            with redirect_stdout(out):self.assertEqual(sync.run(args),1)
            self.assertEqual(sum('create-export-task' in c.args[0] for c in self.client.call.call_args_list),1)
            args.restart_export=[str(self.day)]
            with redirect_stdout(out):self.assertEqual(sync.run(args),1)
            self.assertEqual(sum('create-export-task' in c.args[0] for c in self.client.call.call_args_list),2)
            journal=sync.Journal(self.root/'sync')
            try:
                self.assertEqual(journal.previous(self.day)['supersedes']['expectedBillCount'],100)
                self.assertEqual(journal.con.execute('SELECT count(*) FROM days').fetchone()[0],3)
                self.assertIsNone(journal.previous(self.day+dt.timedelta(days=1)))
            finally:journal.close()
        self.assertIn('多条任务满足',out.getvalue());self.assertIn('T0=',out.getvalue());self.assertIn('本日未创建导出',out.getvalue());cost.upload.assert_not_called()
        last=json.loads((self.root/'sync/sync_latest.json').read_text());self.assertEqual(last['status'],'failed')
        self.assertNotIn('synthetic-key',out.getvalue())

if __name__=='__main__':unittest.main()
