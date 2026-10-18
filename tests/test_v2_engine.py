import io, json, socket, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.request, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import workbook, multi_workbook
from v2 import analytics
from v2.store import Warehouse

H = ['运单号', '计费状态', '交件时间', '寄件时间', '省', '市', '区', '结算重量(kg)', '总费用(元)', '派费合计', '中转费合计', '面单加价(元)',
     '所属客户', '电子面单账号', '客户补贴类型', '政策操作类型', '增值类型', '揽件网点重量(KG)', '集包网点重量(KG)', '中心实重(KG)', '签收时间']


def row(wb, fee, weight=0.8, prov='浙江省', city='杭州市', subsidy='', status='计费成功', policy='', product='无增值', w=0.8,
        hand='2026-09-02 10:00:00', send='2026-09-01 10:00:00', cust='甲', parts=None, sign='2026-09-03 10:00:00'):
    d, t, m = parts or (fee / 2, fee / 2, 0)
    return [wb, status, hand, send, prov, city, '西湖区', weight, fee, d, t, m, cust, 'ACC-' + cust, subsidy, policy, product, w, 0, 0, sign]


BASE_ROWS = [
    row('N1', 2.0),                                   # normal, 0.5-1
    row('N2', 3.0, weight=1.5),                       # normal, 1-2
    row('S1', 2.4, subsidy='一客一价', cust='乙'),     # subsidy 0.2
    row('S2', 2.6, subsidy='一客一价', cust='乙'),
    row('G1', 2.2, prov='广东省', city='广州市'),
    row('X1', 0, status='计费要素缺失'),               # no_fee
    row('X2', 0.2, weight=49, w=0),                   # no_weight
    row('X3', 0.1, policy='自发自签'),                 # self_sign
    row('X4', 3.4, policy='首中心后退'),               # return
    row('X5', 2.0, prov=''),                          # no_dest
    row('M1', 2.0, parts=(1, 0.5, 0)),                # fee_mismatch (flag only)
    row('L1', 2.5, hand='2026-10-01 01:00:00', send='2026-09-30 22:00:00'),  # 寄件9月；10月1日凌晨交件仍属9月30业务日
]


class V2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.wh = Warehouse(self.root / 'v2')
        self.file = self.root / 'a.xlsx'
        self.file.write_bytes(workbook(H, BASE_ROWS))
        job = self.wh.import_file(self.file)
        self.assertEqual(job['state'], 'done')

    def tearDown(self):
        self.wh.con.close()
        self.tmp.cleanup()

    def matrix(self, **kw):
        return analytics.matrix(self.wh, kw)

    def test_import_dedup_and_versions(self):
        self.wh.save_config({**self.wh.config(),'conflictRule':'review'})
        stats = self.wh.import_file(self.file)
        self.assertIn('未重复累计', stats['message'])
        # Same rows in another file (different signing time only) are duplicates; a changed fee is pending.
        changed = [list(r) for r in BASE_ROWS]
        changed[0][-1] = '2026-09-09 09:00:00'
        changed[1][8] = 3.3
        f2 = self.root / 'b.xlsx'
        f2.write_bytes(workbook(H, changed))
        s = self.wh.import_file(f2)['stats']
        self.assertEqual((s['new'], s['conflict'], s['duplicate']), (0, 1, len(BASE_ROWS) - 1))
        p = self.wh.pending({})
        self.assertEqual(p['total'], 1)
        self.assertEqual(p['rows'][0]['waybill'], 'N2')
        before = self.matrix()['totals']['total']['system']
        self.wh.resolve({'ids': [p['rows'][0]['id']], 'action': 'replace'})
        self.assertAlmostEqual(self.matrix()['totals']['total']['system'], before + 0.3, places=4)

    def test_append_supplement(self):
        self.wh.save_config({**self.wh.config(),'conflictRule':'review'})
        sup = [row('N1', 0.5)]
        f2 = self.root / 'c.xlsx'
        f2.write_bytes(workbook(H, sup))
        self.wh.import_file(f2)
        rid = self.wh.pending({})['rows'][0]['id']
        self.wh.resolve({'ids': [rid], 'action': 'append'})
        r = analytics.rows(self.wh, {'flow': '浙江省', 'band': 1})
        n1 = next(x for x in r['rows'] if x['waybill'] == 'N1')
        self.assertEqual(n1['system'], 2.5)
        self.assertEqual(self.matrix()['totals']['total']['n'], 7)  # still one ticket

    def test_exclusions_subsidy_and_actual_cost(self):
        m = self.matrix(filters={'start': '2026-09-01', 'end': '2026-09-30'})
        t = m['totals']['total']
        # N1 N2 S1 S2 G1 M1 L1 survive; X1..X5 excluded.
        self.assertEqual(t['n'], 7)
        self.assertEqual(m['excluded']['n'], 5)
        self.assertEqual(t['system'], 2.0 + 3.0 + 2.4 + 2.6 + 2.2 + 2.0 + 2.5)
        self.assertEqual(t['subsidy'], 0.4)
        self.assertEqual(t['actual'], round(t['system'] - 0.4, 4))
        zj = next(r for r in m['rows'] if r['flow'] == '浙江省')
        self.assertEqual(zj['cells'][1]['n'], 5)          # 0.5-1kg: N1 S1 S2 M1 L1
        self.assertEqual(zj['cells'][2]['n'], 1)          # 1-2kg: N2
        self.assertEqual(zj['cells'][1]['share'], round(5 / 6, 4))
        # Policy amount is configurable per request.
        m2 = self.matrix(cost={'subsidyPolicies': [{'type': '一客一价', 'perTicket': 0.5}]})
        self.assertEqual(m2['totals']['total']['subsidy'], 1.0)

    def test_excluded_includes_outliers_and_empty_scope(self):
        m = self.matrix(filters={'outlier': {'enabled': True, 'lowPct': 10, 'highPct': 90}})
        ex = m['excluded']
        self.assertEqual(ex['rules']['n'], 5)
        self.assertGreater(ex['outlier']['n'], 0)
        self.assertEqual(ex['n'], ex['rules']['n'] + ex['outlier']['n'])
        self.assertEqual(m['totals']['total']['n'] + ex['outlier']['n'], 7)
        empty = self.matrix(filters={'start': '2030-01-01', 'end': '2030-01-31'})
        self.assertIsNone(empty['totals']['total'])
        self.assertEqual(empty['rows'], [])

    def test_date_basis(self):
        send = self.matrix(filters={'dateBasis': 'send', 'start': '2026-09-01', 'end': '2026-09-30'})['totals']['total']['n']
        hand = self.matrix(filters={'dateBasis': 'handover', 'start': '2026-09-01', 'end': '2026-09-30'})['totals']['total']['n']
        self.assertEqual((send, hand), (7, 7))

    def test_bands_flows_and_split(self):
        m = self.matrix(bands=[{'lo': 0, 'hi': 1}, {'lo': 1, 'hi': 9999}], flows={'groups': [{'name': '江浙沪', 'members': ['浙江省', '江苏省']}], 'drop': ['广东省']})
        self.assertEqual([r['flow'] for r in m['rows']], ['江浙沪'])
        self.assertEqual([c['n'] for c in m['rows'][0]['cells']], [5, 1])
        city = self.matrix(flows={'level': 'city', 'only': ['浙江省/杭州市']})
        self.assertEqual([r['flow'] for r in city['rows']], ['浙江省/杭州市'])
        split = self.matrix(splitBy='subsidy', flows={'only': ['浙江省']})
        by = {r['split']: r['total'] for r in split['rows']}
        self.assertEqual(by['一客一价']['avgActual'], 2.3)
        self.assertEqual(by['']['n'], 4)
        with self.assertRaises(ValueError):
            self.matrix(bands=[{'lo': 0, 'hi': 1}, {'lo': 0.5, 'hi': 2}])



    def test_adjustments_and_products(self):
        adj = [{'id': 'op', 'name': '操作', 'amount': 0.1, 'sign': 1}, {'id': 'rb', 'name': '返利', 'amount': 0.05, 'sign': -1},
               {'id': 'off', 'name': '停用', 'amount': 1, 'sign': 1, 'enabled': False}]
        base = self.matrix()['totals']['total']
        t = self.matrix(cost={'adjustments': adj})['totals']['total']
        self.assertEqual(t['adjustment'], round(0.05 * t['n'], 4))
        self.assertEqual(t['actual'], round(base['actual'] + 0.05 * t['n'], 4))
        with self.assertRaises(ValueError):
            self.matrix(cost={'adjustments': [{'id': 'x', 'name': '', 'amount': 1, 'sign': 1}]})
        self.assertEqual(self.matrix(filters={'products': ['无增值']})['totals']['total']['n'], 7)
        self.assertIsNone(self.matrix(filters={'products': ['好快']})['totals']['total'])
        m = analytics.meta(self.wh)
        self.assertTrue(m['features']['analysisV3']);self.assertNotIn('layeredQuotes',m['features'])
        self.assertEqual(m['products'][0]['name'], '无增值')




class V2HTTPTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data = Path(cls.tmp.name) / 'data'
        cls.file = Path(cls.tmp.name) / 'a.xlsx'
        cls.file.write_bytes(workbook(H, BASE_ROWS))
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        cls.url = 'http://127.0.0.1:%d' % port
        cls.proc = subprocess.Popen([sys.executable, str(ROOT / 'server.py'), '--data', str(cls.data), '--port', str(port), '--no-browser'], stdout=subprocess.DEVNULL)
        for _ in range(60):
            try:
                with urllib.request.urlopen(cls.url + '/api/bootstrap', timeout=1) as r:
                    cls.token = json.load(r)['token']
                break
            except Exception:
                time.sleep(.1)
        else:
            raise RuntimeError('server failed')

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        cls.tmp.cleanup()

    def call(self, path, body=None, raw=None, headers=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = {'X-App-Token': self.token, **(headers or {})}
        with urllib.request.urlopen(urllib.request.Request(self.url + '/api/v2/' + path, data, h), timeout=60) as r:
            return json.load(r)

    def test_current_entry_and_removed_routes(self):
        with urllib.request.urlopen(self.url+'/') as r:
            html=r.read().decode();self.assertIn('OUTBOUND COST STUDIO',html);self.assertIn('/v34.bundle.js?v=3.4.0',html)
        with urllib.request.urlopen(self.url+'/api/identity') as r:
            self.assertEqual(json.load(r)['app'],'outbound-cost-studio')
        for path in ['strata','strata/rows','anomalies','snapshots','compare']:
            with self.assertRaises(urllib.error.HTTPError) as e:self.call(path,{})
            self.assertEqual(e.exception.code,404)
        for path in ['/api/meta','/api/backup','/v2.js','/app.js','/archive/v1/engine.py']:
            with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(self.url+path)
            self.assertEqual(e.exception.code,404)
        for route,req in [('quotes',{}),('quote/preview',{})]:
            with self.assertRaises(urllib.error.HTTPError) as e:self.call(route,req)
            self.assertEqual(e.exception.code,404)
        self.assertFalse((self.data/'ledger.sqlite3').exists())

    def test_gzip_static_version_cache_and_concurrent_bundle(self):
        import gzip
        from concurrent.futures import ThreadPoolExecutor
        from v2.version import VERSION
        def fetch(path, encoding='gzip'):
            with urllib.request.urlopen(urllib.request.Request(self.url+path,headers={'Accept-Encoding':encoding}),timeout=20) as r:
                return dict(r.headers),r.read()
        headers,body=fetch('/v34.bundle.js?v='+VERSION)
        self.assertEqual(headers.get('Content-Encoding'),'gzip')
        self.assertIn('immutable',headers['Cache-Control'])
        self.assertIn(b'function renderAppearance',gzip.decompress(body))
        headers,plain=fetch('/v34.bundle.js','gzip;q=0')
        self.assertNotIn('Content-Encoding',headers);self.assertEqual(plain,gzip.decompress(body));self.assertEqual(headers['Cache-Control'],'no-cache')
        headers,data=fetch('/api/v2/meta')
        self.assertEqual(headers['Cache-Control'],'no-store');self.assertIn('dataVersion',json.loads(gzip.decompress(data)))
        with ThreadPoolExecutor(30) as pool:
            results=list(pool.map(lambda _:fetch('/v34.bundle.js?v='+VERSION)[1],range(30)))
        self.assertTrue(all(x==body for x in results))

    def test_flow(self):
        job = self.call('import', raw=self.file.read_bytes(), headers={'X-Filename': '%E4%B9%9D%E6%9C%88.xlsx'})['jobs'][0]
        for _ in range(100):
            j = self.call('jobs?id=' + job)
            if j['state'] in ('done', 'error'):
                break
            time.sleep(.1)
        self.assertEqual(j['state'], 'done', j)
        meta = self.call('meta')
        self.assertEqual(meta['counts']['active'], 12)
        self.assertEqual(meta['batches'][0]['name'], '九月.xlsx')
        m = self.call('matrix', {})
        self.assertEqual(m['totals']['total']['n'], 7)
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(self.url + '/api/v2/matrix', b'{}', {}), timeout=10)
        self.assertEqual(e.exception.code, 400)


if __name__ == '__main__':
    unittest.main()
