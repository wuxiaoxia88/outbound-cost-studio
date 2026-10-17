"""V3 independent arithmetic and scope regressions. All writes use temporary warehouses."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import workbook
from v2.store import Warehouse
from v2 import analysis as a
from test_v2_engine import H, row, BASE_ROWS


def vec(n, cost=2, dispatch=1, weight=.8):
    return [n, round(n*weight*10000), round(n*cost*10000),round(n*dispatch*10000),
            round(n*(cost-dispatch)*10000),0,round(n*cost*10000),0,0,0,0]


class V3Math(unittest.TestCase):
    def test_reference_weights_own_mix_not_network_mix(self):
        own={11:vec(90),19:vec(10)}
        net={11:vec(100,cost=1),19:vec(900,cost=3)}
        r=a.reference(own,net,range(11,21))
        self.assertAlmostEqual(r['avg'],1.2)
        self.assertNotAlmostEqual(r['avg'],2.8)

    def test_sparse_reference_uses_band_and_returns_null_without_network(self):
        r=a.reference({11:vec(4)},{11:vec(4,cost=10),13:vec(36,cost=2)},range(11,21))
        self.assertAlmostEqual(r['avg'],2.8)
        self.assertIsNone(a.reference({1:vec(1)}, {},[1])['avg'])

    def test_quarter_boundary_and_expanded_partition(self):
        d=[a.zero() for _ in range(121)]
        d[5]=vec(10);d[6]=vec(30);d[7]=vec(60);d[120]=vec(5)
        cfg=a.parameters({'baseWidth':.25,'tailShare':0})
        b=a.smart_bands(d,cfg)
        self.assertEqual(len(b),24)
        self.assertEqual(b[0]['n'],10)
        self.assertEqual(b[1]['n'],90)
        x=a.smart_bands(d,cfg,True)
        self.assertEqual(sum(y['n'] for y in x),105)
        self.assertEqual([y for y in x if y.get('fine')][0]['lo'],.25)
        self.assertIsNone(b[-1]['hi'])

    def test_smart_thresholds_and_tail_merges(self):
        d=[a.zero() for _ in range(121)]
        for q,n in ((1,1),(11,40),(21,10),(31,48),(120,1)):
            d[q]=vec(n)
        b=a.smart_bands(d,a.parameters())
        self.assertEqual(b[0]['merged'],'low')
        self.assertEqual(b[1]['cls'],'main')
        self.assertEqual(b[2]['cls'],'sub')
        self.assertEqual(b[-1]['merged'],'high')
        self.assertEqual(sum(y['n'] for y in b),100)

    def test_step_support_and_same_province(self):
        net={'甲':{10:vec(300,dispatch=1),11:vec(300,dispatch=1.2)},
             '乙':{10:vec(29,dispatch=1),11:vec(1000,dispatch=100)}}
        steps=a.detect_steps(net)
        self.assertEqual([(x['step'],x['jump'],x['support']) for x in steps],[(.5,.2,300)])
        net['甲'][10]=vec(299,dispatch=1)
        self.assertEqual(a.detect_steps(net),[])

    def test_critical_uses_system_not_subsidized_actual(self):
        low=vec(10,cost=1);high=vec(10,cost=2);high[a.FEE]=0
        cr=a.critical({'甲':{11:vec(3)}},{'甲':{5:low,6:high}},[{'step':.5,'jump':.2}])
        self.assertEqual(cr['n'],3);self.assertEqual(cr['save'],3)
        cr=a.critical({'乙':{11:vec(3)}},{},[{'step':.5,'jump':.2}])
        self.assertAlmostEqual(cr['save'],.6)

    def test_heat_classes_quantiles_and_sample_gate(self):
        cb={'甲':{1:vec(30,cost=1)},'乙':{1:vec(29,cost=99)},'丙':{1:vec(30,cost=3)}}
        net={'甲':{1:vec(300,cost=2)},'乙':{1:vec(300,cost=2)},'丙':{1:vec(300,cost=2)}}
        h=a.heat(cb,net,[{'units':[1]}],89,True)
        cells={r['flow']:r['cells'][0] for r in h['rows']}
        self.assertEqual(cells['甲']['comparisonClass'],'g2')
        self.assertEqual(cells['丙']['comparisonClass'],'r2')
        self.assertEqual(cells['乙']['costClass'],'small')
        self.assertIsNone(cells['乙']['badge'])
        self.assertEqual(h['cuts'],[1,1,3,3])

    def test_configuration_validation(self):
        for p in ({'baseWidth':.2},{'mainShare':.1,'secondaryShare':.4},{'minGroupN':30.5},
                  {'steps':[{'step':.25,'jump':.1}]},{'steps':[{'step':1,'jump':-.1}]}):
            with self.subTest(p=p),self.assertRaises(ValueError):
                a.parameters(p)


class V3Warehouse(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.wh=Warehouse(self.root/'v2');self.i=0
        self.load(BASE_ROWS)

    def tearDown(self):
        self.wh.con.close();self.tmp.cleanup()

    def load(self, rows):
        self.i+=1;p=self.root/('%d.xlsx'%self.i);p.write_bytes(workbook(H,rows))
        return self.wh.import_file(p)

    def call(self,**kw):
        return a.analysis(self.wh,{'start':'2026-09-01','end':'2026-09-30',**kw})

    def test_base_actual_cost_and_excluded_union(self):
        x=self.call();t=x['overview']
        self.assertEqual(t['n'],7);self.assertEqual(x['excluded']['n'],5)
        self.assertAlmostEqual(t['system'],16.7);self.assertAlmostEqual(t['fee'],16.7)
        self.assertEqual(t['subsidy'],0)
        self.assertEqual(sum(v['n'] for v in x['distribution']),t['n'])
        self.assertEqual(x['heat']['total']['total']['n'],t['n'])

    def test_date_basis_preserves_cross_month(self):
        self.assertEqual(self.call()['overview']['n'],7)
        self.assertEqual(self.call(dateBasis='handover')['overview']['n'],7)
        self.assertEqual(self.call(start='2026-10-01',end='2026-10-01',dateBasis='handover')['overview']['n'],0)

    def test_disable_exclusion_and_no_double_counting(self):
        x=self.call(excludeRules=[])
        self.assertEqual(x['overview']['n'],12);self.assertEqual(x['excluded']['n'],0)
        self.assertEqual(x['excluded']['rules']['no_weight'],1)
        y=self.call(excludeRules=['return']);self.assertEqual(y['overview']['n'],11)

    def test_customer_filter_does_not_filter_network(self):
        all_x=self.call();one=self.call(customers=['乙']);both=self.call(customers=['甲','乙'])
        self.assertEqual(one['overview']['n'],2)
        self.assertEqual(one['network'],all_x['network'])
        self.assertEqual(both['network'],one['network'])
        self.assertEqual(both['mode'],'multi')
        self.assertEqual(len(both['sameCells']),2)

    def test_similar_returns_only_rendered_fields(self):
        x=self.call(customers=['甲'],analysis={'minGroupN':1})
        self.assertTrue(x['similar'])
        for s in x['similar']:
            self.assertEqual(set(s),{'customer','similarity'})
            self.assertEqual(set(s['customer']),{'name','key','topProvinces','n','avg','gap','criticalN'})
        self.assertEqual(x['similar'][0]['customer']['n'],2)

    def test_multi_distribution_and_fixed_band_conserve(self):
        self.load([row('HEAVY',7,weight=49,cust='乙')])
        counts=[0,0]
        for i in range(12):
            x=self.call(customers=['甲','乙'],compareBand=i,analysis={'baseWidth':.25})
            self.assertEqual(x['sameBand'],i)
            self.assertEqual(len(x['sameBands']),12)
            self.assertIsNone(x['sameBands'][-1]['hi'])
            for j,(s,c) in enumerate(zip(x['comparison'],x['sameCells'])):
                self.assertEqual(len(s['distribution']),60)
                self.assertEqual(sum(v['n'] for v in s['distribution']),s['total']['n'])
                self.assertAlmostEqual(sum(v['fee'] for v in s['distribution']),s['total']['fee'])
                counts[j]+=c['heat']['total']['total']['n']
        self.assertEqual(counts,[5,3])
        self.assertEqual(x['sameCells'][1]['heat']['total']['total']['n'],1)
        with self.assertRaises(ValueError):self.call(compareBand=12)

    def test_base_bands_unchanged_by_expansion(self):
        self.load([row('F1',2,weight=.6),row('F2',2,weight=.9)])
        plain=self.call(customers=['甲']);fine=self.call(customers=['甲'],expand=True)
        self.assertEqual(plain['bands'],fine['baseBands'])
        self.assertGreater(len(fine['bands']),len(fine['baseBands']))

    def test_latest_import_dates_have_both_bases(self):
        m=a.meta(self.wh)['lastImport']
        self.assertEqual(m['end'],'2026-09-30')
        self.assertEqual(m['dateRanges']['handover']['end'],'2026-09-30')
        self.assertEqual(a.meta(self.wh,{'dateBasis':'handover'})['lastImport']['end'],'2026-09-30')
        self.assertEqual(set(m['customers']),{'甲','乙'})

    def test_money_policy_date_and_signed_adjustments(self):
        cfg={'subsidyPolicies':[{'type':'一客一价','perTicket':.25,'start':'2026-10-01','end':None}],
             'adjustments':[{'id':'o','name':'操作','amount':.10,'sign':1},{'id':'r','name':'返利','amount':.02,'sign':-1}]}
        x=self.call(simulation={'operation':{'on':True,'v':.10},'custom':{'on':True,'v':.02}})['overview']
        self.assertEqual(x['subsidy'],0);self.assertAlmostEqual(x['simulation'],.56)
        self.assertAlmostEqual(x['fee'],17.26)
        self.assertEqual(self.wh.config()['subsidyPolicies'][0]['perTicket'],.2)

    def test_subsidy_changes_actual_but_not_price_comparison(self):
        base=self.call(customers=['乙'],cost={'subsidyPolicies':[]})
        subsidized=self.call(customers=['乙'],simulation={'subsidy':{'on':True,'v':.2}})
        self.assertAlmostEqual(base['overview']['avg']-subsidized['overview']['avg'],.2)
        self.assertEqual(base['overview']['gap'],subsidized['overview']['gap'])
        self.assertEqual(base['overview']['critical'],subsidized['overview']['critical'])
        self.assertEqual(base['comparison'][0]['tags'],subsidized['comparison'][0]['tags'])

    def test_decomposition_conservation_and_adjustment(self):
        for b in ('甲','network'):
            x=a.decompose(self.wh,{'a':'乙','b':b})
            self.assertAlmostEqual(sum(x[k] for k in ('weight','flow','price','adjustment')),x['avgA']-x['avgB'],places=12)
            base=a.decompose(self.wh,{'a':'乙','b':b,'cost':{'subsidyPolicies':[]}})
            for k in ('weight','flow','price'):
                self.assertAlmostEqual(x[k],base[k],places=12)
        same=a.decompose(self.wh,{'a':'乙','b':'乙'})
        self.assertAlmostEqual(same['delta'],0)

    def test_cell_pagination_components_and_city(self):
        req={'customers':['甲'],'flow':'浙江省','band':{'lo':.5,'hi':1},'pageSize':1}
        x=a.cell(self.wh,req);y=a.cell(self.wh,dict(req,page=2))
        self.assertEqual(x['totalRows'],3);self.assertEqual(len(x['rows']),1)
        self.assertNotEqual(x['rows'][0]['id'],y['rows'][0]['id'])
        self.assertEqual(sum(c['n'] for c in x['cities']),3)
        self.assertEqual(self.wh.raw(x['rows'][0]['id'])['raw']['运单号'],x['rows'][0]['waybill'])
        city=a.cell(self.wh,dict(req,flow='浙江省/杭州市',level='city'))
        self.assertEqual(city['total'],x['total'])
        self.assertEqual(city['total']['w'],2.4)

    def test_empty_date_and_unknown_customer(self):
        x=self.call(start='2028-01-01',end='2028-01-31')
        self.assertEqual(x['overview']['n'],0);self.assertIsNone(x['overview']['avg'])
        self.assertEqual(x['clusters']['groups'],[])
        y=self.call(customers=['不存在']);self.assertEqual(y['overview']['n'],0)
        self.assertIsNone(y['overview']['eq'])
        json.dumps(x,allow_nan=False);json.dumps(y,allow_nan=False)

    def test_cache_invalidates_on_config_import_and_isolates_warehouses(self):
        x=self.call();cfg=self.wh.config();cfg['simulation']['operation']={'on':True,'v':.1};self.wh.save_config(cfg)
        y=self.call();self.assertNotEqual(x['version'],y['version'])
        self.assertAlmostEqual(y['overview']['fee']-x['overview']['fee'],.7)
        self.load([row('NEW',8,cust='丙')]);z=self.call();self.assertEqual(z['overview']['n'],8)
        other=Warehouse(self.root/'other')
        try:self.assertEqual(a.analysis(other,{})['overview']['n'],0)
        finally:other.con.close()

    def test_snapshot_and_atomic_config_version(self):
        before=self.wh.version();cfg=self.wh.config();cfg['subsidyPolicies']=[]
        with self.wh.snapshot() as (cur,v):
            self.wh.save_config(cfg)
            self.assertEqual(self.wh.version(cur),v)
            self.assertEqual(self.wh.config(cur)['subsidyPolicies'][0]['perTicket'],.2)
            old=a.context(self.wh,{},cur,v)
            self.assertAlmostEqual(a.public(old['netTotal'])['fee'],16.7)
        self.assertEqual(self.wh.version(),before+1)
        cfg=self.wh.config();cfg['deductRebate']=True
        with patch.object(self.wh,'bump',side_effect=RuntimeError('test rollback')):
            with self.assertRaises(RuntimeError):self.wh.save_config(cfg)
        self.assertFalse(self.wh.config()['deductRebate']);self.assertEqual(self.wh.version(),before+1)

    def test_meta_and_invalid_request_contract(self):
        m=a.meta(self.wh)
        self.assertTrue(m['features']['analysisV3']);self.assertEqual(m['normalN'],7)
        self.assertTrue(all('cluster' in c for c in m['customers']))
        for r in ({'customers':list('123456789')},{'customers':'甲'},{'level':'district'},
                  {'dateBasis':'invalid'},{'start':'2026-10-01','end':'2026-01-01'}):
            with self.subTest(r=r),self.assertRaises(ValueError):self.call(**r)

    def test_quarter_cells_exact_boundary_and_capped_tail(self):
        self.load([row('Q'+str(i),1,weight=w,cust='分界') for i,w in enumerate((.25,.2501,.3,.5,5.95,6,49))])
        x=self.call(customers=['分界'],analysis={'baseWidth':.25,'tailShare':0},showAll=True)
        self.assertEqual([b['n'] for b in x['bands'][:2]],[1,3])
        self.assertEqual(x['bands'][-1]['n'],3)
        cell=a.cell(self.wh,{'customers':['分界'],'flow':'浙江省','band':{'lo':.25,'hi':.5}})
        self.assertEqual(cell['totalRows'],3)
        tail=a.cell(self.wh,{'customers':['分界'],'flow':'浙江省','band':{'lo':5.75,'hi':None}})
        self.assertEqual(tail['totalRows'],3)
        self.assertEqual(len(tail['rows']),3)

    def test_manual_steps_and_group_threshold(self):
        x=self.call(analysis={'steps':[{'step':.7,'jump':.3}],'minGroupN':2})
        self.assertEqual(x['steps'][0]['source'],'manual')
        self.assertGreater(x['overview']['critical']['n'],0)
        self.assertEqual(x['clusters']['eligibleCustomers'],2)
        y=self.call(analysis={'steps':[]});self.assertEqual(y['overview']['critical']['n'],0)

    def test_rebate_adjustments_do_not_change_price_badges(self):
        self.load([row('P'+str(i),2,subsidy='一客一价',cust='补贴客户') for i in range(35)])
        self.wh.con.execute("UPDATE ship SET rebate=0.125 WHERE customer='补贴客户'")
        plain=self.call(customers=['补贴客户'],cost={'subsidyPolicies':[]},showAll=True)
        adjusted=self.call(customers=['补贴客户'],simulation={'custom':{'on':True,'v':.325},'operation':{'on':True,'v':.1}},showAll=True)
        self.assertAlmostEqual(plain['overview']['avg']-adjusted['overview']['avg'],.225)
        self.assertEqual(plain['overview']['gap'],adjusted['overview']['gap'])
        p=next(c for c in plain['heat']['rows'][0]['cells'] if c['n'])
        q=next(c for c in adjusted['heat']['rows'][0]['cells'] if c['n'])
        for k in ('deviation','badge','comparisonClass'):
            self.assertEqual(p[k],q[k])
        self.assertEqual(adjusted['overview']['rebate'],0)  # Source rebate no longer applies automatically.

    def test_changed_weight_width_reuses_aggregate_and_city_reference(self):
        before=self.call(customers=['甲'],level='city')
        with patch.object(a,'read_cubes',side_effect=AssertionError('unnecessary detail scan')):
            after=self.call(customers=['甲'],level='city',analysis={'baseWidth':.25})
        self.assertEqual(before['network'],after['network'])
        self.assertEqual(before['heat']['total']['total']['n'],after['heat']['total']['total']['n'])

    def test_flow_ties_use_network_volume_order(self):
        self.load([row('T1',2,prov='云南省',cust='两票'),row('T2',2,prov='浙江省',cust='两票')])
        x=self.call(customers=['两票'])
        self.assertEqual(x['comparison'][0]['flabel'],'浙江为主')


if __name__=='__main__':unittest.main()
