"""V3.1 independent examples. Temporary warehouses only, including migration/undo."""
import copy, json, tempfile, unittest, zipfile, io
from pathlib import Path
from decimal import Decimal
from server import workbook,multi_workbook
from v2.store import Warehouse
from v2 import analysis as a, simulation as s, quotation as q, imports
from test_v2_engine import H,row

class Simulation(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name);self.wh=Warehouse(self.home/'v2');self.i=0
    def tearDown(self):self.wh.con.close();self.tmp.cleanup()
    def load(self,rows):
        self.i+=1;p=self.home/f'{self.i}.xlsx';p.write_bytes(workbook(H+['退件费','退件中转费','退件操作费'],rows));return self.wh.import_file(p)
    def normal(self,wb,**kw):return row(wb,2,**kw)+[0,0,0]
    def ret(self,wb,rb=1,**kw):return row(wb,3,product='拦截,退改',**kw)+[rb,1.1,.9]
    def call(self,**kw):return a.analysis(self.wh,{'start':'2026-09-01','end':'2026-09-30',**kw})
    def context(self):
        with self.wh.snapshot() as (c,v):return a.context(self.wh,{'start':'2026-09-01','end':'2026-09-30'},c,v)
    def test_defaults_migration_preserves_values_but_disables(self):
        cfg={'subsidyPolicies':[{'type':'一客一价','perTicket':.19,'end':None}],
             'adjustments':[{'id':'o','name':'操作成本','amount':.41,'sign':1,'enabled':True},
                            {'id':'m','name':'管理成本','amount':.07,'sign':1,'enabled':False},
                            {'id':'r','name':'返利','amount':.93,'sign':-1,'enabled':True}]}
        m=s.migrated(cfg);self.assertFalse(any(x['on'] for x in m.values()))
        self.assertEqual([m[k]['v'] for k in ('subsidy','operation','management','custom')],[.19,.41,.07,.93])
    def test_return_bill_import_backfill_and_one_time_migration(self):
        self.load([self.normal('n'),self.ret('r',rb=1.234)])
        self.wh.con.execute('ALTER TABLE ship DROP COLUMN return_bill')
        cfg=self.wh.config();cfg.pop('simulation');cfg['adjustments']=[{'id':'o','name':'操作成本','sign':1,'amount':.41,'enabled':True}]
        self.wh.con.execute("UPDATE meta SET value=? WHERE key='config'",[json.dumps(cfg)])
        v=self.wh.version();self.wh.con.close();self.wh=Warehouse(self.home/'v2')
        r=self.call()['aftersales'];self.assertEqual(r['rfee'],3);self.assertEqual(r['rbill'],1.234)
        self.assertAlmostEqual(r['total'],4.234);self.assertEqual(self.wh.config()['simulation']['operation'],{'on':False,'v':.41})
        self.assertEqual(self.wh.version(),v+1);self.assertIn('simulationLegacy',self.wh.config())
        self.wh.con.close();self.wh=Warehouse(self.home/'v2');self.assertEqual(self.wh.version(),v+1)
    def test_return_categories_denominators_and_service_separation(self):
        r2=row('r2',4,product='拦截')+[2,0,0];r3=row('r3',5,policy='首中心退')+[3,0,0]
        self.load([self.normal('n'),self.ret('r'),r2,r3,self.normal('special',policy='自发自签')])
        off=self.call(customers=['甲']);ret=off['aftersales']
        self.assertEqual((ret['n'],ret['rn'],ret['normalN']),(5,3,1));self.assertEqual([ret[k] for k in ('n1','n2','n3')],[1,1,1])
        self.assertEqual(ret['rate'],.6);self.assertEqual(ret['total'],18)
        service=self.call(customers=['甲'],simulation={'service':{'on':True,'v':2}})
        self.assertEqual(service['aftersales']['total'],24);self.assertEqual(service['overview']['fee'],2)
        addon=self.call(customers=['甲'],simulation={'service':{'on':True,'v':2},'addon':{'on':True}})
        self.assertEqual(addon['overview']['fee'],26)
    def test_all_off_ignores_legacy_rebate_and_manual_fees(self):
        self.load([self.normal('n',subsidy='一客一价')]);self.wh.con.execute('UPDATE ship SET rebate=.7')
        self.wh.save_config({**self.wh.config(),'deductRebate':True,'adjustments':[{'id':'o','name':'操作成本','amount':99,'sign':1}]})
        r=self.call();self.assertEqual(r['overview']['avg'],2);self.assertEqual(r['simulation']['count'],0)
        quote=q.preview(self.wh,{'customers':['甲'],'target':'甲'})
        self.assertFalse(any(x['on'] for x in quote['simulation'].values()))
    def test_weight_boundaries_and_subsidy_tag(self):
        weights=[1,1.0001,1.5,1.5001,3,3.0001]
        self.load([self.normal(str(i),weight=w,subsidy='一客一价' if i%2==0 else '') for i,w in enumerate(weights)])
        r=self.call(simulation={'subsidy':{'on':True,'v':.2},'w115':{'on':True,'v':.4}})
        self.assertAlmostEqual(r['simulation']['effects']['subsidy'],-.6)
        self.assertAlmostEqual(r['simulation']['effects']['w115'],-.8)
        self.assertAlmostEqual(r['overview']['fee'],10.6)
    def test_network_denominators_and_heavy_penalty_reward(self):
        self.load([self.normal('a',weight=1.2),self.normal('b',weight=2,cust='乙'),self.normal('c',weight=4),self.normal('d',weight=6,cust='乙')])
        sm={'w13':{'on':True,'base':1,'v':.4},'heavy':{'on':True,'base':12,'rate':2,'share':.4},'idle':{'on':True,'target':8,'v':.5}}
        r=self.call(customers=['甲'],simulation=sm);K=r['simulation']['constants']
        self.assertEqual((K['n'],K['n13'],K['n3'],K['w3']),(4,2,2,10))
        self.assertEqual(K['heavyAmount'],-4);self.assertEqual(K['heavy'],2);self.assertEqual(K['idle'],.5)
        self.assertAlmostEqual(r['simulation']['per'],1.4) # (-.2 +2 +1)/2
        sm['heavy']['base']=5;r=self.call(simulation=sm);self.assertAlmostEqual(r['simulation']['constants']['heavyAmount'],.4)
    def test_puhui_tier_and_natural_day_denominator(self):
        self.load([self.normal('a')]);ctx=self.context();sm=s.validate();ctx=copy.deepcopy(ctx)
        ctx['netTotal'][a.N]=2700000
        K=s.constants(ctx,sm);self.assertEqual(K['days'],30);self.assertEqual(K['tier']['value'],1.044);self.assertEqual(K['tier']['lo'],8)
        ctx['netTotal'][a.N]=600000;self.assertEqual(s.constants(ctx,sm)['tier']['ratio'],.31)
    def test_zero_data_and_return_only_no_infinity(self):
        r=self.call(simulation={k:{'on':True} for k in s.DEFAULT});self.assertIsNone(r['overview']['avg']);json.dumps(r,allow_nan=False)
        self.load([self.ret('r')]);r=self.call(customers=['甲'],simulation={'addon':{'on':True}})
        self.assertEqual(r['aftersales']['rn'],1);self.assertIsNone(r['aftersales']['per']);json.dumps(r,allow_nan=False)
        with self.assertRaises(ValueError):q.preview(self.wh,{'customers':['甲'],'includeAddon':True})
    def test_comparisons_invariant_and_cube_cache_reused(self):
        self.load([self.normal('a'+str(i),cust='甲') for i in range(40)]+[row('b'+str(i),3,cust='乙')+[0,0,0] for i in range(40)]+[self.ret('r')])
        plain=self.call(customers=['甲'],showAll=True);sm={'operation':{'on':True,'v':.35},'addon':{'on':True}}
        from unittest.mock import patch
        with patch.object(a,'read_cubes',side_effect=AssertionError('rescan')):on=self.call(customers=['甲'],showAll=True,simulation=sm)
        for k in ('gap','critical','eqSystem'):self.assertEqual(plain['overview'][k],on['overview'][k])
        self.assertEqual(plain['comparison'][0]['avg'],on['comparison'][0]['avg']);self.assertEqual(plain['comparison'][0]['tags'],on['comparison'][0]['tags'])
        for p,r in zip(plain['heat']['rows'][0]['cells'],on['heat']['rows'][0]['cells']):
            for k in ('badge','deviation','comparisonClass'):self.assertEqual(p[k],r[k])
        self.assertAlmostEqual(on['overview']['eq']-plain['overview']['eq'],.45)
    def test_cell_city_totals_match_rows_and_simulation_lines(self):
        self.load([self.normal('a'+str(i),subsidy='一客一价') for i in range(40)]+[self.ret('r')])
        sm={'subsidy':{'on':True},'operation':{'on':True},'service':{'on':True},'addon':{'on':True}}
        req={'customers':['甲'],'flow':'浙江省','band':{'lo':.5,'hi':1},'simulation':sm,'pageSize':100}
        r=a.cell(self.wh,req);self.assertAlmostEqual(r['total']['fee'],sum(x['actual'] for x in r['rows']))
        self.assertAlmostEqual(r['total']['avg'],2-.2+.35+6/40)
        self.assertEqual(len(r['simulationLines']),3);self.assertEqual(r['ref']['avg'],2)
        self.assertEqual(r['total']['fee'],sum(c['fee'] for c in r['cities']))
        city=a.cell(self.wh,{**req,'level':'city','flow':'浙江省/杭州市'});self.assertEqual(city['total'],r['total'])
    def test_quote_own_province_nation_use_source_weight_mix(self):
        self.load([self.normal('a'+str(i),weight=1.01,cust='甲') for i in range(30)]+[self.normal('b'+str(i),weight=1.4,cust='乙',prov='广东省') for i in range(30)]+[self.normal('s',weight=3.1,cust='甲')])
        sm={'w115':{'on':True,'v':.4},'operation':{'on':True,'v':.35}}
        r=q.preview(self.wh,{'customers':['甲'],'simulation':sm,'value':.1,'mode':'fixed'})
        z=next(x for x in r['rows'] if x['flow']=='浙江省');g=next(x for x in r['rows'] if x['flow']=='广东省')
        self.assertEqual(z['cells'][2]['source'],'own');self.assertEqual(g['cells'][2]['source'],'province')
        self.assertAlmostEqual(z['cells'][2]['cost'],1.95);self.assertEqual(z['cells'][2]['price'],2.1)
        self.assertIsNone(z['cells'][5]['price'])
    def test_quote_addon_once_and_service_is_not_direct_normal_cost(self):
        self.load([self.normal('a'+str(i)) for i in range(40)]+[self.ret('r')]);sm={'operation':{'on':True},'service':{'on':True},'addon':{'on':True}}
        req={'customers':['甲'],'target':'甲','simulation':sm,'mode':'fixed','value':0}
        no=q.preview(self.wh,req);yes=q.preview(self.wh,{**req,'includeAddon':True})
        self.assertAlmostEqual(no['rows'][0]['cells'][1]['cost'],2.35)
        self.assertAlmostEqual(yes['rows'][0]['cells'][1]['cost'],2.5)
        self.assertAlmostEqual(yes['addonPer'],.15)
        saved=q.save(self.wh,{'expectedVersion':yes['version'],'request':yes['request']})
        data=multi_workbook(q.export(self.wh,saved['id']))
        with zipfile.ZipFile(io.BytesIO(data)) as z:text=''.join(z.read(n).decode() for n in z.namelist() if n.endswith('.xml'))
        for term in ('成本','客服','操作','退件','simulation'):self.assertNotIn(term,text)
        cfg=self.wh.config();cfg['simulation']['operation']['on']=True;self.wh.save_config(cfg)
        self.assertEqual(q.read(self.wh,saved['id'])['result'],saved['result'])
    def test_simulation_config_persistence_and_validation(self):
        cfg=self.wh.config();cfg['simulation']['operation']={'on':True,'v':.37};self.wh.save_config(cfg)
        self.wh.con.close();self.wh=Warehouse(self.home/'v2');self.assertEqual(self.wh.config()['simulation']['operation']['v'],.37)
        for bad in ({'x':{'on':True}},{'heavy':{'share':1.1}},{'operation':{'v':float('nan')}},{'idle':{'target':-1}},{'operation':{'on':1}}):
            with self.assertRaises(ValueError):s.validate(bad)
    def test_legacy_supplement_backfill_and_old_journal_undo(self):
        self.wh.save_config({**self.wh.config(),'conflictRule':'review'})
        self.load([self.ret('r',rb=1)])
        extra=self.load([self.ret('r',rb=2)])
        rid=self.wh.cursor().execute('SELECT id FROM ship WHERE state=0').fetchone()[0]
        self.wh.resolve({'ids':[rid],'action':'append'})
        # Emulate pre-3.1 journal and schema, then migrate and undo that operation.
        for eid,rid,raw in self.wh.cursor().execute('SELECT * FROM import_before').fetchall():
            before=json.loads(raw);before.pop('return_bill',None)
            self.wh.con.execute('UPDATE import_before SET before_row=? WHERE event_id=? AND row_id=?',[json.dumps(before),eid,rid])
        self.wh.con.execute('ALTER TABLE ship DROP COLUMN return_bill');self.wh.con.close();self.wh=Warehouse(self.home/'v2')
        self.assertEqual(self.call()['aftersales']['rbill'],3)
        imports.undo(self.wh,{'id':extra['groupId'],'expectedVersion':self.wh.version()})
        self.assertEqual(self.call()['aftersales']['rbill'],1)

    def test_return_bill_replacement_and_undo(self):
        self.load([self.ret('r',rb=1)]);b=self.load([self.ret('r',rb=5)])
        self.assertEqual(self.call()['aftersales']['rbill'],5)
        imports.undo(self.wh,{'id':b['groupId'],'expectedVersion':self.wh.version()})
        self.assertEqual(self.call()['aftersales']['rbill'],1)
        self.assertEqual(self.wh.cursor().execute('SELECT count(*) FROM raw').fetchone()[0],2)

if __name__=='__main__':unittest.main()
