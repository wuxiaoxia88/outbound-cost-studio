"""Independent filtered-warehouse comparison, quote isolation and immutable exports."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import io
import xml.etree.ElementTree as ET
from test_v2_engine import row,H
from server import workbook,multi_workbook
from v2.store import Warehouse
from v2 import analysis,quotation,regions,reports,analytics

class RegionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name);self.full=Warehouse(self.home/'full');self.sub=Warehouse(self.home/'sub')
        rows=[]
        # Deliberately different day, cost, weight and return structures by region.
        for cust in ['甲','乙','丙']:
            for month in [8,9]:
                for prov,fee,day in [('浙江省',2,1),('江苏省',3,2),('广东省',4,3),('青海省',8,4),('新疆维吾尔自治区',12,5)]:
                    for i in range(43):
                        rows.append(row(f'{cust}-{month}-{prov}-{i}',fee+i%3,weight=.5+(i%4)*.05,prov=prov,cust=cust,send=f'2026-{month:02d}-{day:02d} 12:30:00',hand=f'2026-{month:02d}-{day:02d} 13:00:00',policy='首中心后退' if i==42 else '',subsidy='一客一价'))
        for wh,rs in [(self.full,rows),(self.sub,[r for r in rows if r[4] not in ('青海省','新疆维吾尔自治区')])]:
            p=wh.home/'sample.xlsx';p.write_bytes(workbook(H,rs));wh.import_file(p)
        self.req={'start':'2026-09-01','end':'2026-09-30','dateBasis':'send','comparePrevious':True,'excludeZones':['四区','五区']}
    def tearDown(self):
        self.full.con.close();self.sub.con.close();self.tmp.cleanup()
    def calls(self,**kw):
        return analysis.analysis(self.full,{**self.req,**kw}),analysis.analysis(self.sub,{**self.req,**kw,'excludeZones':[]})
    def test_scope_all_statistics_equal_filtered_warehouse(self):
        for customers in ['all',['甲'],['甲','乙']]:
            for level in ['province','city','zone']:
                with self.subTest(customers=customers,level=level):
                    a,b=self.calls(customers=customers,level=level)
                    for key in ['overview','distribution','networkDistribution','network','excluded','comparison','similar','cluster','ranking','clusters','aftersales','previous','history','baseBands','bands','heat','sameCells','sameNetwork','decomposition']:
                        self.assertEqual(a.get(key),b.get(key),key)
                    self.assertEqual(a['scope']['excludeZones'],['四区','五区'])
                    self.assertGreater(a['scope']['zoneExcluded']['selected']['n'],0)
    def test_simulation_and_detail_decomposition_scope(self):
        sim=copy.deepcopy(self.full.config()['simulation']);sim['operation'].update(on=True,v=.35)
        sim['addon']['on']=True
        a,b=self.calls(customers=['甲'],simulation=sim)
        for key in ['overview','aftersales','heat','distribution','network','comparison']:self.assertEqual(a[key],b[key],key)
        req={**self.req,'customers':['甲'],'flow':'浙江省','level':'province','band':{'lo':0,'hi':1},'simulation':sim}
        a=analysis.cell(self.full,req);b=analysis.cell(self.sub,{**req,'excludeZones':[]})
        for key in ['total','ref','gap','simulationLines','cities','totalRows']:self.assertEqual(a[key],b[key],key)
        x=analysis.decompose(self.full,{**self.req,'a':'甲','b':'乙'});y=analysis.decompose(self.sub,{**self.req,'excludeZones':[],'a':'甲','b':'乙'})
        x.pop('scope');y.pop('scope');self.assertEqual(x,y)
    def test_zone_partition_alias_and_normalization(self):
        x=analysis.analysis(self.full,{**self.req,'excludeZones':[],'level':'zone'})
        self.assertEqual(sum(z['n'] for z in x['zones']['rows']),x['overview']['n'])
        for z in x['zones']['rows']:self.assertEqual(len(z['distribution']),12);self.assertEqual(sum(z['distribution']),z['n'])
        for z in x['heat']['rows']:
            for i,c in enumerate(z['cells']):self.assertEqual(sum(p['cells'][i]['n'] for p in z['provinces']),c['n'])
        self.assertEqual(regions.short('广西壮族自治区'),'广西');self.assertEqual(regions.short('上海市'),'上海')
        self.assertEqual(len({p for g in regions.DEFAULT_GROUPS for p in g['provinces']}),31)
        with self.assertRaises(ValueError):analysis.analysis(self.full,{**self.req,'excludeZones':['不存在']})
    def test_config_and_ungrouped_empty_scope(self):
        cfg=self.full.config();cfg['regionGroups']['schemes']['default']['groups'][0]['provinces'].remove('浙江')
        self.full.save_config(cfg)
        x=analysis.analysis(self.full,{**self.req,'excludeZones':[g['name'] for g in regions.DEFAULT_GROUPS],'level':'zone'})
        self.assertEqual(x['overview']['n'],126);self.assertEqual(x['heat']['rows'],[])
        bad=copy.deepcopy(cfg);bad['regionGroups']['schemes']['default']['groups'][1]['provinces'].append('新疆')
        with self.assertRaises(ValueError):self.full.save_config(bad)
    def test_quote_remote_full_context_and_main_filtered(self):
        req={**self.req,'customers':['甲'],'target':'甲','subsidyMode':'existing','includeAddon':True,'mode':'fixed','value':.35}
        x=quotation.preview(self.full,req);allq=quotation.preview(self.full,{**req,'excludeZones':[]});filtered=quotation.preview(self.sub,{**req,'excludeZones':[]})
        self.assertEqual(x['rows'],filtered['rows']);self.assertEqual(x['summary'],filtered['summary'])
        self.assertEqual(x['remoteRows'],[r for r in allq['rows'] if r['zone'] in ['四区','五区']])
        cell=next(c for r in x['remoteRows'] for c in r['cells'] if c['price'] is not None)
        edited=quotation.preview(self.full,{**req,'overrides':{cell['key']:19.5}})
        self.assertEqual(x['summary'],edited['summary']);self.assertTrue(any(c['price']==19.5 for r in edited['remoteRows'] for c in r['cells']))
    def test_customer_export_remote_section_no_cost_and_version_gate(self):
        req={**self.req,'customers':['甲'],'target':'甲','subsidyMode':'existing'}
        result=quotation.preview(self.full,req)
        saved=quotation.save(self.full,{'request':result['request'],'expectedVersion':result['version']})
        sheets=quotation.export(self.full,saved['id']);text=json.dumps(sheets,ensure_ascii=False)
        self.assertIn('偏远区域',text)
        self.assertNotIn('成本',text);self.assertNotIn('毛利',text);self.assertNotIn('乙',text)
        self.assertEqual(len([r for r in sheets[0][2] if len(r)==8]),5)
        self.full.save_config(self.full.config())
        self.assertEqual(sheets,quotation.export(self.full,saved['id']))
        with self.assertRaises(Exception):quotation.save(self.full,{'request':result['request'],'expectedVersion':result['version']})

    def test_report_snapshot_not_recomputed_or_mutated(self):
        x=analysis.analysis(self.full,self.req);sid=x['snapshotId'];x['overview']['n']=-1
        sheets=reports.sheets(self.full,{'snapshotId':sid,'modules':['overview','zones','heat','history','aftersales'],'flowRows':'all'})
        self.assertEqual(sheets[0][2][0][1],378)
        cfg=self.full.config();self.full.save_config(cfg)
        self.assertEqual(sheets,reports.sheets(self.full,{'snapshotId':sid,'modules':['overview','zones','heat','history','aftersales'],'flowRows':'all'}))
        book=multi_workbook(sheets);ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(io.BytesIO(book)) as z:
            doc=ET.fromstring(z.read('xl/worksheets/sheet1.xml'));self.assertIsNotNone(doc.find('.//s:pane[@state="frozen"]',ns));self.assertEqual(doc.find('.//s:c[@r="B2"]/s:v',ns).text,'378')
        with self.assertRaises(ValueError):reports.sheets(self.full,{'snapshotId':'bad','modules':['overview']})
    def test_empty_excluded_remains_empty_across_cache_changes(self):
        none={**self.req,'excludeZones':[g['name'] for g in regions.DEFAULT_GROUPS]}
        self.assertEqual(analysis.analysis(self.full,none)['overview']['n'],0)
        self.assertEqual(analysis.analysis(self.full,self.req)['overview']['n'],378)
        self.assertEqual(analysis.analysis(self.full,none)['overview']['n'],0)

if __name__=='__main__':unittest.main()
