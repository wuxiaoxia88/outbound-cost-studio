"""HTTP dispatch for /api/v2/*. Shared v3 runtime, retaining the /api/v2 URL namespace."""
import json
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import analytics, analysis, quotation, imports, responses
from .store import Warehouse

_warehouse = None


def warehouse(base):
    global _warehouse
    if _warehouse is None:
        _warehouse = Warehouse(Path(base) / 'v2')
    return _warehouse


def handle(h, method, data_dir):
    """Returns True when the request was a v2 request (handled, including errors)."""
    u = urlparse(h.path)
    if not u.path.startswith('/api/v2/'):
        return False
    route = u.path[len('/api/v2/'):]
    try:
        h.safe()
        allowed = {'GET': {'meta','analysis/default','revision','jobs','raw','batches','quotes','quotes/export'}, 'POST': {'backup','import','batches/start','batches/finish','batches/undo','quote/preview','quotes','matrix','rows','analysis','analysis/decompose','analysis/cell','analysis/export','analysis/details','config','pending','pending/resolve','export'}}
        if route not in allowed.get(method,set()):
            return h.reply({'error': '未找到'},404) or True
        wh = warehouse(data_dir)
        if method == 'GET':
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if route == 'meta':
                return h.reply(responses.service(wh).get('meta',q)) or True
            if route == 'analysis/default':
                svc=responses.service(wh)
                return h.reply(svc.get('analysis',svc.default_request())) or True
            if route == 'revision':
                return h.reply({'revision':responses.service(wh).current(),'dataVersion':wh.version()}) or True
            if route == 'jobs':
                return h.reply(wh.jobs.get(q.get('id'), {'state': 'error', 'message': '任务不存在'})) or True
            if route == 'raw':
                return h.reply(wh.raw(q['id'])) or True
            if route == 'batches':
                return h.reply(imports.listing(wh)) or True
            if route == 'quotes' and q.get('schema')=='v3':
                return h.reply(quotation.read(wh,q['id']) if q.get('id') else quotation.listing(wh)) or True
            if route == 'quotes/export' and q.get('schema')=='v3':
                from server import multi_workbook
                return h.reply(multi_workbook(quotation.export(wh,q['id'])),typ='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',filename='customer-quote.xlsx') or True
            return h.reply({'error': '未找到'}, 404) or True
        size = int(h.headers.get('Content-Length', '0'))
        if route == 'import':
            if size > 2 * 1024 ** 3:
                raise ValueError('单文件上限 2GB')
            folder = wh.home / 'uploads'
            folder.mkdir(exist_ok=True)
            name = unquote(h.headers.get('X-Filename', '账单.xlsx'))
            target = folder / (__import__('uuid').uuid4().hex + (Path(name).suffix or '.xlsx'))
            with target.open('wb') as f:
                remain = size
                while remain:
                    b = h.rfile.read(min(remain, 1 << 20))
                    if not b:
                        raise ValueError('上传中断')
                    f.write(b)
                    remain -= len(b)
            return h.reply(wh.start_import(target, name, cleanup=True, group_id=h.headers.get('X-Import-Group'))) or True
        if size > 10 * 1024 * 1024:
            raise ValueError('请求过大')
        req = json.loads(h.rfile.read(size) or b'{}')
        if route=='backup':
            from .operations import backup
            return h.reply(backup(wh)) or True
        if route=='batches/start':return h.reply(imports.create(wh,req)) or True
        if route=='batches/finish':return h.reply(imports.finish(wh,req)) or True
        if route=='batches/undo':return h.reply(imports.undo(wh,req)) or True
        if route in ('quote/preview','quotes') and req.get('schema')=='v3':
            return h.reply((quotation.preview if route=='quote/preview' else quotation.save)(wh,req)) or True
        if route=='analysis':return h.reply(responses.service(wh).get('analysis',req)) or True
        if route=='analysis/details':return h.reply(responses.details(wh,req)) or True
        simple = {'matrix': analytics.matrix, 'rows': analytics.rows,
                  'analysis': analysis.analysis, 'analysis/decompose': analysis.decompose, 'analysis/cell': analysis.cell}
        if route in simple:
            return h.reply(simple[route](wh, req)) or True
        if route=='analysis/export':
            from .reports import sheets
            from server import multi_workbook
            return h.reply(multi_workbook(sheets(wh,req)),typ='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',filename='internal-analysis.xlsx') or True
        if route == 'config':
            return h.reply(wh.save_config(req)) or True
        if route == 'pending':
            return h.reply(wh.pending(req)) or True
        if route == 'pending/resolve':
            return h.reply(wh.resolve(req)) or True
        if route == 'export':
            from server import multi_workbook
            body = multi_workbook(analytics.export_sheets(wh, req))
            name = 'quote.xlsx' if req.get('kind') == 'quote' else 'cost-matrix.xlsx'
            return h.reply(body, typ='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename=name) or True
        return h.reply({'error': '未找到'}, 404) or True
    except analytics.VersionConflict as ex:
        h.reply({'error': str(ex)}, 409)
    except (KeyError, TypeError) as ex:
        h.reply({'error': '请求参数不完整：%s' % ex}, 400)
    except Exception as ex:
        h.reply({'error': str(ex)}, 400)
    return True
