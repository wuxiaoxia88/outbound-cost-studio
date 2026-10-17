"""Synthetic regression fixtures, independent month arithmetic and cache isolation."""
import tempfile, unittest
from pathlib import Path
from server import workbook
from v2.store import Warehouse
from v2 import analysis as a, history as h
from test_v2_engine import H,row

class CalendarMath(unittest.TestCase):
    def test_full_month_leap_year_and_year_boundary(self):
        r=h.bounds('2024-03-01','2024-03-31')
        self.assertEqual((r['previousStart'],r['previousEnd'],r['days']),('2024-02-01','2024-02-29',31))
        self.assertEqual(h.bounds('2026-01-01','2026-01-31')['previousStart'],'2025-12-01')
    def test_partial_equal_length_and_union(self):
        r=h.bounds('2026-09-05','2026-09-11')
        self.assertEqual((r['previousStart'],r['previousEnd']),('2026-08-29','2026-09-04'))
        d=h.daily(12,['客户A','客户B'],{'客户A':{1,2},'客户B':{2,3}},30)
        self.assertEqual((d['daily'],d['activeDays']),(4,3))
        self.assertEqual(h.daily(12,['客户A'],{'客户A':{1}},30,True)['daily'],.4)
    def test_strict_300_threshold(self):
        for n in (0,1,299):self.assertIsNone(h.change({'n':600},{'n':n}))
        for n in (300,352):self.assertAlmostEqual(h.change({'n':600},{'n':n})['n'],600/n-1)
    def test_sparse_month_threshold_and_zero_rows(self):
        months=[{'month':f'2026-{m:02d}','n':n} for m,n in [(3,0),(4,99),(5,100),(6,101),(8,7),(9,1000)]]
        visible,hidden=h.visible_months(months,months[-1],months[-2])
        self.assertEqual([x['month'] for x in visible],['2026-05','2026-06','2026-08','2026-09'])
        self.assertEqual(hidden,1)
        self.assertIsNone(h.change(months[-1],months[-2]))
    def test_previous_calendar_month_kept_below_ten_percent(self):
        current={'month':'2026-09','n':4000};previous={'month':'2026-08','n':352}
        self.assertEqual(h.visible_months([previous,current],current,previous),([previous,current],0))
        self.assertIsNotNone(h.change(current,previous))
        # Empty anchors are never inserted; a missing prior month is not skipped.
        empty={'month':'2026-08','n':0}
        self.assertEqual(h.visible_months([empty,current],current,empty),([current],0))

class WarehouseHistory(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name);self.wh=Warehouse(self.home/'v2')
        rows=[]
        for name,n in [('客户A',352),('客户B',299),('客户C',300)]:
            rows.extend(row(f'{name}-{i}',2,weight=.8,cust=name,send='2026-08-%02d 10:00:00'%(i%2+1)) for i in range(n))
        rows += [row('new'+str(i),3,weight=1.05,cust='客户A',send='2026-09-01 12:00:00') for i in range(10)]
        rows += [row('b',4,weight=2,cust='客户B',send='2026-09-02 12:00:00'),row('c',5,cust='客户C',send='2026-09-02 14:00:00'),row('bad',8,cust='客户A',product='拦截',send='2026-09-03 12:00:00')]
        path=self.home/'synthetic.xlsx';path.write_bytes(workbook(H,rows));self.wh.import_file(path)
    def tearDown(self):self.wh.con.close();self.tmp.cleanup()
    def call(self,**kw):return a.analysis(self.wh,{'start':'2026-09-01','end':'2026-09-30','analysis':{'steps':[{'step':1,'jump':.5}]},'comparePrevious':True,**kw})
    def test_daily_bill_only_history_and_simulation_isolation(self):
        x=self.call(customers=['客户A']);y=self.call(customers=['客户A'],simulation={'operation':{'on':True,'v':.35}})
        self.assertEqual(x['overview']['daily'],10)
        self.assertEqual(x['overview']['activeDays'],1)
        self.assertEqual(x['history'],y['history']);self.assertEqual(x['previous'],y['previous'])
        self.assertEqual(x['history']['previous']['n'],352);self.assertAlmostEqual(x['previous']['delta']['avg'],1)
        self.assertEqual(x['history']['current']['criticalN'],10)
        self.assertEqual(x['overview']['avg'],3);self.assertEqual(y['overview']['avg'],3.35)
    def test_multi_union_and_city_weights(self):
        x=self.call(customers=['客户A','客户B'],level='city')
        self.assertEqual((x['overview']['activeDays'],x['overview']['daily']),(2,5.5))
        self.assertAlmostEqual(x['heat']['total']['total']['avgW'],12.5/11)
        for b,c in zip(x['bands'],x['heat']['total']['cells']):self.assertEqual(b['avgW'],c['avgW'])
        self.assertAlmostEqual(self.call()['overview']['daily'],12/30)
    def test_ranking_gate_and_custom_period(self):
        x=self.call();rows={s['name']:s for s in x['ranking']}
        self.assertIsNone(rows['客户B']['previous']['delta'])
        self.assertIsNotNone(rows['客户C']['previous']['delta'])
        r=self.call(start='2026-09-01',end='2026-09-02')['previous']['range']
        self.assertEqual((r['previousStart'],r['previousEnd']),('2026-08-30','2026-08-31'))
    def test_no_history_or_empty_customer(self):
        r=self.call(customers=['不存在的客户']);self.assertIsNone(r['overview']['daily']);self.assertEqual(r['history']['current']['n'],0)
        self.assertEqual(r['history']['months'],[])
        r=self.call(customers=['客户A'],comparePrevious=False);self.assertFalse(r['previous']['enabled'])
    def test_sparse_history_uses_selected_normal_volume_and_six_candidates(self):
        rows=[]
        for month,n in [(2,200),(3,1),(5,11),(6,10),(7,9),(9,90),(10,20)]:
            rows.extend(row(f'history-{month}-{i}',2,cust='客户A',send=f'2026-{month:02d}-01 10:00:00') for i in range(n))
        rows.append(row('return-only-month',2,cust='客户A',product='拦截',send='2026-04-01 10:00:00'))
        path=self.home/'history.xlsx';path.write_bytes(workbook(H,rows));self.wh.import_file(path)
        for selection,expected,hidden in [(['客户A'],['2026-05','2026-06','2026-08','2026-09'],2),
                                           ('all',['2026-05','2026-08','2026-09'],3),
                                           (['客户A','客户B'],['2026-05','2026-08','2026-09'],3)]:
            with self.subTest(selection=selection):
                history=self.call(customers=selection)['history']
                self.assertEqual([x['month'] for x in history['months']],expected)
                self.assertEqual(history['hiddenMonthCount'],hidden)
                self.assertTrue(all(x['n']>0 for x in history['months']))
    def test_meta_version_is_not_data_version(self):
        m=a.meta(self.wh);self.assertTrue(m['features']['handoverBusinessDay']);self.assertEqual(m['version'],'3.2.1');self.assertIsInstance(m['dataVersion'],int)

if __name__=='__main__':unittest.main()
