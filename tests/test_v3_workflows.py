"""P3 round-trip tests on disposable warehouses, including undo and quote privacy."""
import io,json,tempfile,unittest,zipfile
from pathlib import Path
from server import workbook,multi_workbook
from v2.store import Warehouse
from v2 import imports,quotation as q,analysis
from v2.analytics import VersionConflict
from test_v2_engine import H,row

class Workflows(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.wh=Warehouse(self.root/'v2');self.i=0
    def tearDown(self):self.wh.con.close();self.tmp.cleanup()
    def load(self,rows,g=None):
        self.i+=1;p=self.root/f'{self.i}.xlsx';p.write_bytes(workbook(H,rows));return self.wh.import_file(p,group_id=g)
    def cfg(self,**values):self.wh.save_config({**self.wh.config(),**values})
    def active(self):return self.wh.cursor().execute('SELECT waybill,fee FROM ship WHERE state=1 ORDER BY waybill').fetchall()
    def undo(self,g):return imports.undo(self.wh,{'id':g,'expectedVersion':self.wh.version()})
    def test_streaming_fallback_has_no_archived_dependency(self):
        from unittest.mock import patch
        with patch('v2.store.calamine_rows',side_effect=MemoryError('simulated low memory')):
            result=self.load([row('fallback',2)])
        self.assertEqual(result['state'],'done');self.assertEqual(len(self.active()),1)

    def test_latest_replaces_and_undo_restores_then_reimport(self):
        original=self.load([row('a',2)])
        new=self.load([row('a',3),row('b',4)])
        self.assertEqual([float(v) for k,v in self.active()],[3,4]);self.assertEqual(new['stats']['replaced'],1)
        self.undo(new['groupId']);self.assertEqual([(k,float(v)) for k,v in self.active()],[('a',2)])
        again=self.load([row('a',3),row('b',4)]);self.assertEqual(again['stats']['new'],2)
        with self.assertRaises(ValueError):self.undo(original['groupId'])
        self.undo(again['groupId']);self.undo(original['groupId']);self.assertEqual(self.active(),[])
    def test_group_multiple_files_duplicates_quantiles_and_atomic_undo(self):
        gid=imports.create(self.wh)['id'];rows=[row(str(i),2,send='2026-09-01' if i<100 else '2026-09-30') for i in range(200)]
        rows+=[row('early',2,send='2026-05-30'),row('late',2,send='2026-10-01')]
        self.load(rows,gid);self.load([row('z',3)],gid);imports.finish(self.wh,{'id':gid})
        result=imports.read(self.wh,gid);self.assertEqual((result['start'],result['end'],result['outside']),('2026-09-01','2026-09-30',2))
        self.assertEqual(len(result['files']),2);self.undo(gid);self.assertEqual(self.active(),[])
    def test_duplicate_operation_undo_does_not_remove_original(self):
        a=self.load([row('a',2)]);v=self.wh.version();b=self.load([row('a',2)])
        self.assertEqual(b['stats']['duplicate'],1);self.assertEqual(self.wh.version(),v);self.undo(b['groupId']);self.assertEqual(len(self.active()),1)
    def test_partial_overlap_members_include_preexisting_rows(self):
        self.load([row('a',2,send='2026-09-01')]);b=self.load([row('a',2,send='2026-09-01'),row('b',3,send='2026-09-30')]);r=imports.read(self.wh,b['groupId'])
        self.assertEqual(r['start'],'2026-09-01');self.assertEqual(r['end'],'2026-09-30');self.undo(b['groupId']);self.assertEqual(len(self.active()),1)
    def test_review_resolution_and_append_are_reversible(self):
        self.cfg(conflictRule='review');self.load([row('a',2)])
        b=self.load([row('a',.5)]);self.assertEqual(b['stats']['conflict'],1)
        rid=self.wh.pending({})['rows'][0]['id'];self.wh.resolve({'ids':[rid],'action':'append'})
        self.assertEqual(float(self.active()[0][1]),2.5);self.undo(b['groupId']);self.assertEqual(float(self.active()[0][1]),2)
    def test_unrelated_later_import_does_not_block_undo(self):
        a=self.load([row('a',2)]);self.load([row('b',3)]);self.undo(a['groupId']);self.assertEqual(self.active()[0][0],'b')
    def test_same_file_conflict_preserves_old_until_review(self):
        self.load([row('a',2)]);b=self.load([row('a',3),row('a',4)])
        self.assertEqual(self.wh.pending({})['total'],2);self.assertEqual(float(self.active()[0][1]),2);self.undo(b['groupId']);self.assertEqual(len(self.active()),1)
    def test_file_with_old_and_changed_variant_never_silently_selects(self):
        self.load([row('a',2)]);r=self.load([row('a',2),row('a',3)])
        self.assertEqual(r['stats']['conflict'],1);self.assertEqual(float(self.active()[0][1]),2)
        self.cfg(conflictRule='review');self.load([row('b',2),row('b',3)])
        pending=self.wh.pending({})['rows'];ids=[x['id'] for x in pending if x['waybill']=='b'];self.assertEqual(len(ids),2)
        with self.assertRaises(ValueError):self.wh.resolve({'ids':ids,'action':'replace'})
        self.assertFalse(any(k=='b' for k,v in self.active()))
    def test_stale_undo_config_and_invalid_settings_write_nothing(self):
        a=self.load([row('a',2)]);v=self.wh.version()
        with self.assertRaises(VersionConflict):imports.undo(self.wh,{'id':a['groupId'],'expectedVersion':v-1})
        with self.assertRaises(VersionConflict):self.wh.save_config({**self.wh.config(),'expectedVersion':v-1})
        with self.assertRaises(ValueError):self.cfg(conflictRule='anything')
        self.assertEqual(v,self.wh.version());self.assertEqual(len(self.active()),1)
    def quote_data(self):
        rows=[row('A'+str(i),2.05,cust='源甲',subsidy='一客一价') for i in range(30)]
        rows += [row('B'+str(i),4,cust='源乙',prov='广东省',weight=1.5) for i in range(40)]
        rows += [row('C'+str(i),10,cust='源甲',weight=49,w=1) for i in range(30)]
        rows += [row('s',999,cust='源甲',policy='自发自签'),row('d',999,cust='源甲',w=0),row('r',999,cust='源甲',policy='首中心后退')]
        self.load(rows)
    def preview(self,**kw):return q.preview(self.wh,{'customers':['源甲'],'target':'新客户','mode':'fixed','value':0,**kw})
    def test_quote_precision_target_subsidy_and_fallback(self):
        self.quote_data();r=self.preview();rows={r['flow']:r['cells'] for r in r['rows']}
        self.assertEqual(rows['浙江省'][1]['price'],2.1);self.assertAlmostEqual(rows['浙江省'][1]['cost'],2.05)
        self.assertEqual(rows['浙江省'][2]['source'],'nation');self.assertEqual(rows['广东省'][2]['source'],'province')
        self.assertIsNone(rows['浙江省'][0]['price']);self.assertEqual(r['summary']['n'],60)
        s=self.preview(subsidyType='一客一价');self.assertAlmostEqual(next(x for x in s['rows'] if x['flow']=='浙江省')['cells'][1]['cost'],1.85)
        self.assertEqual(s['rows'][0]['cells'][6]['price'],9.8)
    def test_city_drawer_scope_preserves_city_cost_and_province_fallback(self):
        self.load([row('h'+str(i),2,city='杭州',cust='甲') for i in range(30)]+[row('w'+str(i),8,city='示例市乙',cust='甲') for i in range(30)])
        r=q.preview(self.wh,{'customers':['甲'],'flows':['浙江省/杭州'],'mode':'fixed','value':0})
        self.assertEqual(len(r['rows']),1);self.assertEqual(r['rows'][0]['flow'],'浙江省/杭州');self.assertEqual(r['summary']['n'],30);self.assertEqual(r['rows'][0]['cells'][1]['cost'],2)
    def test_quote_policy_dates_adjustments_manual_and_save_export(self):
        self.quote_data();self.cfg(subsidyPolicies=[{'type':'一客一价','perTicket':.2,'start':'2026-10-01'}],adjustments=[{'id':'o','name':'操作','amount':.1,'sign':1},{'id':'r','name':'返利','amount':.05,'sign':-1}])
        r=self.preview(subsidyType='一客一价',simulation={'operation':{'on':True,'v':.1},'custom':{'on':True,'v':.05}},overrides={'浙江省|1':1.05});cell=next(x for x in r['rows'] if x['flow']=='浙江省')['cells'][1]
        self.assertAlmostEqual(cell['cost'],2.1);self.assertEqual(cell['price'],1.1);self.assertTrue(cell['loss']);self.assertEqual(r['summary']['editedCells'],1)
        saved=q.save(self.wh,{'expectedVersion':r['version'],'request':r['request']})
        before=q.read(self.wh,saved['id'])['result'];self.cfg(adjustments=[])
        self.assertEqual(before,q.read(self.wh,saved['id'])['result'])
        with self.assertRaises(VersionConflict):q.save(self.wh,{'expectedVersion':r['version'],'request':r['request']})
        data=multi_workbook(q.export(self.wh,saved['id']))
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml=''.join(z.read(n).decode() for n in z.namelist() if n.endswith('.xml'))
        for secret in ['成本','毛利','源甲','源乙','账号','<f>']:self.assertNotIn(secret,xml)
        self.assertIn('新客户',xml);self.assertEqual(len(q.listing(self.wh)['quotes']),1)
    def test_existing_subsidy_is_disabled_until_simulation_is_enabled(self):
        rows=[row('s'+str(i),2,subsidy='一客一价',cust='甲') for i in range(30)]
        rows += [row('n'+str(i),2,cust='甲') for i in range(30)]
        rows += [row('o'+str(i),2,cust='甲',subsidy='一客一价',send='2026-10-01') for i in range(30)]
        self.load(rows);self.cfg(subsidyPolicies=[{'type':'一客一价','perTicket':.2,'end':'2026-09-30'}])
        req={'customers':['甲'],'target':'甲','mode':'fixed','value':0}
        r=q.preview(self.wh,req);cell=r['rows'][0]['cells'][1]
        self.assertEqual(r['subsidyMode'],'target');self.assertAlmostEqual(cell['cost'],2)
        on=q.preview(self.wh,{**req,'simulation':{'subsidy':{'on':True,'v':.2}}});self.assertAlmostEqual(on['rows'][0]['cells'][1]['cost'],2-12/90)
        a=analysis.analysis(self.wh,{'customers':['甲']});self.assertAlmostEqual(a['overview']['avg'],cell['cost'])
        saved=q.save(self.wh,{'expectedVersion':r['version'],'request':r['request']})
        self.assertIn('无补贴',str(q.export(self.wh,saved['id'])))
        for change in [{'target':'新客户'},{'basis':'network'},{'customers':['甲','乙']}]:
            plain=q.preview(self.wh,{**req,**change});self.assertEqual(plain['subsidyMode'],'target');self.assertEqual(plain['rows'][0]['cells'][1]['cost'],2)
            with self.assertRaises(ValueError):q.preview(self.wh,{**req,**change,'subsidyMode':'existing'})
        explicit=q.preview(self.wh,{**req,'subsidyType':''});self.assertEqual(explicit['rows'][0]['cells'][1]['cost'],2)

    def test_v3_quote_save_holds_write_lock(self):
        import threading,time
        self.quote_data();r=self.preview();original=q._preview;started=threading.Event();changed=threading.Event();saved=[]
        def slow(*args):
            started.set();time.sleep(.1);self.assertFalse(changed.is_set());return original(*args)
        q._preview=slow
        try:
            t=threading.Thread(target=lambda:saved.append(q.save(self.wh,{'expectedVersion':r['version'],'request':r['request']})))
            t.start();self.assertTrue(started.wait(2))
            updater=threading.Thread(target=lambda:(self.cfg(deductRebate=True),changed.set()));updater.start()
            t.join(5);updater.join(5);self.assertFalse(t.is_alive());self.assertFalse(updater.is_alive())
        finally:q._preview=original
        self.assertEqual(saved[0]['result']['version'],r['version']);self.assertTrue(changed.is_set())

    def test_invalid_override_missing_scope_and_mandatory_version(self):
        self.quote_data()
        for args in ({'overrides':{'浙江省|0':2}},{'value':float('nan')},{'subsidyType':'未知'},{'flows':['不存在']}):
            with self.assertRaises(ValueError):self.preview(**args)
        with self.assertRaises(ValueError):q.save(self.wh,{'request':self.preview()['request']})

if __name__=='__main__':unittest.main()
