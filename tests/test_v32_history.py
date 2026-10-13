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
        r=self.call(customers=['客户A'],comparePrevious=False);self.assertFalse(r['previous']['enabled'])
    def test_meta_version_is_not_data_version(self):
        m=a.meta(self.wh);self.assertEqual(m['version'],'3.2.0');self.assertIsInstance(m['dataVersion'],int)

if __name__=='__main__':unittest.main()
