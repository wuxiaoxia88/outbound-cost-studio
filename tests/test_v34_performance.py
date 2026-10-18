"""Response correctness, invalidation, compression and province-identity exclusion."""
import copy,gzip,json,tempfile,unittest,concurrent.futures,time
from pathlib import Path
from unittest.mock import patch
from v2 import analysis,quotation,regions,reports,responses,imports
from v2.store import Warehouse
from server import workbook
from test_v2_engine import row,H

class V34Test(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.wh=Warehouse(self.root/'full');self.sub=Warehouse(self.root/'sub')
        rows=[]
        for month in (8,9):
            for cust in ('甲','乙'):
                for prov in ('浙江省','新疆维吾尔自治区','香港特别行政区','澳门特别行政区','台湾省'):
                    for i in range(40):rows.append(row(f'{month}-{cust}-{prov}-{i}',2+i%3,prov=prov,cust=cust,policy='首中心后退' if i==39 else '',send=f'2026-{month:02d}-01 12:00:00'))
        self.load(self.wh,rows);self.load(self.sub,[r for r in rows if regions.short(r[4]) not in regions.HMT])
        self.req={'start':'2026-09-01','end':'2026-09-30','dateBasis':'send','customers':'all','comparePrevious':True}
    def tearDown(self):
        for wh in (self.wh,self.sub):
            if hasattr(wh,'_responses'):wh._responses.close()
            wh.con.close()
        self.tmp.cleanup()
    def load(self,wh,rows):
        p=wh.home/'test.xlsx';p.write_bytes(workbook(H,rows));return wh.import_file(p)
    def decoded(self,kind='analysis',req=None):return json.loads(responses.service(self.wh).get(kind,req or self.req).raw)
    def test_off_equals_physically_filtered_all_metrics_and_quotes(self):
        for customers in ('all',['甲'],['甲','乙']):
            for level in ('province','city','zone'):
                req={**self.req,'customers':customers,'level':level,'excludeZones':['五区']}
                a=analysis.analysis(self.wh,req);b=analysis.analysis(self.sub,req)
                for k in ('overview','network','heat','ranking','comparison','history','previous','distribution','aftersales','zones','decomposition','sameCells'):
                    self.assertEqual(a.get(k),b.get(k),k)
        req={**self.req,'customers':['甲'],'target':'甲','excludeZones':['五区'],'includeAddon':True,'subsidyMode':'existing'}
        a=quotation.preview(self.wh,req);b=quotation.preview(self.sub,req)
        for k in ('rows','remoteRows','summary','aftersales'):self.assertEqual(a[k],b[k],k)
        self.assertFalse(any(regions.short(x['flow']) in regions.HMT for x in a['rows']+a['remoteRows']))
    def test_on_off_renaming_moving_and_restoring(self):
        off=self.decoded();cfg=self.wh.config();cfg['regionGroups']['showHMT']=True;self.wh.save_config(cfg)
        on=self.decoded();self.assertEqual(on['overview']['n'],off['overview']['n']*2.5)
        self.assertIn('港澳台',[g['name'] for g in on['zones']['rows']])
        cfg['regionGroups']['showHMT']=False;groups=cfg['regionGroups']['schemes']['default']['groups'];groups[-1]['name']='自定义名称';groups[-1]['provinces'].remove('香港');groups[0]['provinces'].append('香港');self.wh.save_config(cfg)
        moved=self.decoded();self.assertEqual(moved['overview'],off['overview']);self.assertNotIn('自定义名称',[g['name'] for g in moved['zones']['rows']]);self.assertNotIn('香港',moved['zones']['rows'][0]['provinces'])
        for g in groups:
            if g['name']=='自定义名称':g['provinces']=[]
        self.assertNotIn('自定义名称',[g['name'] for g in regions.visible_groups(cfg['regionGroups'])])
        self.assertEqual(regions.short('香港特别行政区'),'香港')
        old=copy.deepcopy(regions.DEFAULT);old.pop('showHMT');old['schemes']['default']['groups'].pop();up=regions.validate(old)
        self.assertFalse(up['showHMT']);self.assertEqual(len(up['schemes']['default']['groups']),6);self.assertEqual(regions.validate(up),up)
    def test_cached_and_compact_values_snapshot_and_lru(self):
        svc=responses.Responses(self.wh,limit=2)
        req={**self.req,'compact':True};raw=svc.get('analysis',req);a=json.loads(raw.raw)
        full=json.loads(svc.get('analysis',self.req).raw)
        self.assertEqual(a['overview'],full['overview']);self.assertEqual(a['heat'],full['heat']);self.assertEqual(a['snapshotId'],full['snapshotId'])
        self.assertEqual(json.loads(gzip.decompress(raw.gzip)),a)
        self.assertEqual(a['ranking'],[responses.rank_public(x) for x in reports.ranking_rows(full['ranking'],{})])
        self.assertTrue(all('members' not in x for x in a['clusters']['groups']))
        detail=responses.details(self.wh,{'snapshotId':a['snapshotId'],'part':'ranking'});self.assertEqual(len(detail['ranking']),len(full['ranking']))
        old=reports.sheets(self.wh,{'snapshotId':a['snapshotId'],'modules':['overview']})
        self.wh.save_config(self.wh.config());b=json.loads(svc.get('analysis',req).raw);self.assertGreater(b['version'],a['version'])
        self.assertEqual(old,reports.sheets(self.wh,{'snapshotId':a['snapshotId'],'modules':['overview']}))
        for c in ('甲','乙'):svc.get('analysis',{**req,'customers':[c]})
        self.assertLessEqual(len(svc.entries),2)
    def test_import_undo_finish_and_config_invalidation(self):
        a=self.decoded();svc=responses.service(self.wh)
        meta=json.loads(svc.get('meta').raw)
        self.load(self.wh,[row('new-test',10)])
        b=self.decoded();self.assertEqual(b['overview']['n'],a['overview']['n']+1)
        self.assertEqual(json.loads(svc.get('meta').raw)['counts']['active'],meta['counts']['active']+1)
        batch=imports.latest(self.wh.con);imports.undo(self.wh,{'id':batch['id'],'expectedVersion':self.wh.version()});self.assertEqual(self.decoded()['overview']['n'],a['overview']['n'])
        cfg=self.wh.config();cfg['simulation']['operation'].update(on=True,v=.35);self.wh.save_config(cfg)
        self.assertAlmostEqual(self.decoded()['overview']['avg'],a['overview']['avg']+.35)
        gid=imports.create(self.wh)['id'];opened=json.loads(svc.get('meta').raw);self.assertEqual(opened['importGroups'][0]['status'],'open')
        imports.finish(self.wh,{'id':gid});self.assertEqual(json.loads(svc.get('meta').raw)['importGroups'][0]['status'],'done')
    def test_single_flight_and_default_prewarm(self):
        svc=responses.service(self.wh);original=analysis.analysis
        with patch.object(analysis,'analysis',wraps=original) as call:
            with concurrent.futures.ThreadPoolExecutor(8) as pool:results=list(pool.map(lambda _:svc.get('analysis',self.req).raw,range(8)))
            self.assertEqual(call.call_count,1);self.assertTrue(all(x==results[0] for x in results))
        svc.start();deadline=time.monotonic()+10
        while time.monotonic()<deadline and len(svc.entries)<3:time.sleep(.02)
        self.assertIsNone(svc.error);self.assertGreaterEqual(len(svc.entries),3)
        self.wh.save_config(self.wh.config());deadline=time.monotonic()+10
        while time.monotonic()<deadline and svc.revision[0]!=self.wh.version():time.sleep(.02)
        self.assertEqual(svc.revision[0],self.wh.version())
    def test_equivalent_parameter_defaults_share_serialized_cache(self):
        svc=responses.service(self.wh);a=svc.get('analysis',self.req)
        cfg=self.wh.config()
        b=svc.get('analysis',{**self.req,'level':'province','expand':False,'showAll':False,'excludeZones':[],
                              'analysis':analysis.parameters(cfg.get('analysis')),'simulation':cfg['simulation']})
        self.assertIs(a,b)

    def test_gzip_quality_and_bundle(self):
        for h in ('gzip','br, gzip;q=0.8','*;q=1'):self.assertTrue(responses.accepts_gzip(h))
        for h in ('','br','gzip;q=0','gzip;q=0, *;q=1','gzip;q=garbage'):self.assertFalse(responses.accepts_gzip(h))
        from scripts.build_web import bundle,ROOT
        self.assertEqual((ROOT/'web/v34.bundle.js').read_text(),bundle())
        css=(ROOT/'web/v34.css').read_text();self.assertNotIn('portrait',css)

if __name__=='__main__':unittest.main()
