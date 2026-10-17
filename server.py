"""Outbound Cost Studio server. Business data lives in data/v2."""
import argparse, hashlib, io, json, secrets, signal, threading, webbrowser, zipfile
from pathlib import Path
from v2.version import VERSION
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from xml.sax.saxutils import escape
from v2 import routes

BASE = Path(__file__).resolve().parent
APP_ID = 'outbound-cost-studio'
def enc(data):
    return json.dumps(data, ensure_ascii=False, default=str)

def data_identity(data_dir):
    return hashlib.sha256(str((Path(data_dir)/'v2').resolve()).encode()).hexdigest()

def workbook(headers,rows):
    """Small output-only XLSX writer, no formulas from user-controlled text."""
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="分析结果" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        parts=['<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="30" width="20" customWidth="1"/></cols><sheetData>']
        for i,row in enumerate([headers]+rows,1):
            parts.append('<row r="%s">'%i)
            for j,v in enumerate(row,1):
                col='';n=j
                while n:n,r=divmod(n-1,26);col=chr(65+r)+col
                ref=col+str(i)
                if isinstance(v,(int,float)):parts.append('<c r="%s"><v>%s</v></c>'%(ref,v))
                else:parts.append('<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'%(ref,escape(str(v if v is not None else ''))))
            parts.append('</row>')
        parts.append('</sheetData></worksheet>');z.writestr('xl/worksheets/sheet1.xml',''.join(parts))
    return out.getvalue()

def multi_workbook(sheets):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        overrides='';sheet_tags='';rels=''
        for i,(name,headers,rows) in enumerate(sheets,1):
            with zipfile.ZipFile(io.BytesIO(workbook(headers,rows))) as one:
                z.writestr('xl/worksheets/sheet%d.xml'%i,one.read('xl/worksheets/sheet1.xml'))
                if i==1:z.writestr('_rels/.rels',one.read('_rels/.rels'))
            overrides+='<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'%i
            sheet_tags+='<sheet name="%s" sheetId="%d" r:id="rId%d"/>'%(escape(name),i,i)
            rels+='<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>'%(i,i)
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'+overrides+'</Types>')
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+sheet_tags+'</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+rels+'</Relationships>')
    return out.getvalue()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,data,status=200,typ='application/json; charset=utf-8',filename=None):
        if not isinstance(data,bytes):data=enc(data).encode()
        self.send_response(status);self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        if filename:self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
        self.end_headers();self.wfile.write(data)
    def reply_file(self,path,filename):
        self.send_response(200);self.send_header('Content-Type','application/zip');self.send_header('Content-Length',str(path.stat().st_size));self.send_header('Content-Disposition','attachment; filename="'+filename+'"');self.send_header('Cache-Control','no-store');self.end_headers()
        with path.open('rb') as f:
            while True:
                b=f.read(1024*1024)
                if not b:break
                self.wfile.write(b)
    def safe(self):
        host=self.headers.get('Host','')
        allowed=getattr(self.server,'allowed_hosts',{'127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)})
        if len(self.headers.get_all('Host',[]))!=1 or host.lower() not in allowed:raise ValueError('访问地址不在允许列表中')
        origin=self.headers.get('Origin')
        if self.command=='POST' and origin and urlparse(origin).netloc.lower()!=host.lower():raise ValueError('请求来源不匹配')
        if self.command=='POST' and self.headers.get('X-App-Token')!=self.server.token:raise ValueError('页面已失效，请刷新后重试')
    def do_GET(self):
        if routes.handle(self, 'GET', self.server.data_dir):
            return
        try:
            self.safe()
            path = urlparse(self.path).path
            if path == '/api/identity':
                return self.reply({'app': APP_ID, 'version': VERSION, 'data': data_identity(self.server.data_dir)})
            if path == '/api/health':
                from v2.operations import health
                return self.reply(health(routes.warehouse(self.server.data_dir)))
            if path == '/api/bootstrap':
                return self.reply({'token': self.server.token})
            # Old bookmarks lead to the current UI; retired assets and APIs are unavailable.
            if path in ('/v2.html', '/legacy.html', '/v3.html', '/v31.html'):
                self.send_response(302); self.send_header('Location','/'); self.send_header('Content-Length','0'); self.end_headers(); return
            name = 'v32.html' if path in ('/', '/index.html') else path.lstrip('/')
            file = BASE/'web'/name
            if file.parent != BASE/'web' or not file.is_file():
                return self.reply({'error':'未找到'},404)
            typ={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8'}.get(file.suffix,'application/octet-stream')
            return self.reply(file.read_bytes(),typ=typ)
        except Exception as ex:
            self.reply({'error':str(ex)},400)

    def do_POST(self):
        if not routes.handle(self,'POST',self.server.data_dir):
            self.reply({'error':'未找到'},404)


def main():
    ap=argparse.ArgumentParser(description='出港成本分析')
    ap.add_argument('--port',type=int,default=8888)
    ap.add_argument('--host',default='127.0.0.1')
    ap.add_argument('--allow-host',action='append',default=[],help='允许的 Host（可重复，不含协议和路径）')
    ap.add_argument('--data',default=str(BASE/'data'))
    ap.add_argument('--no-browser',action='store_true')
    args=ap.parse_args()
    for host in args.allow_host:
        parsed=urlparse('//'+host)
        if not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or '*' in host:
            ap.error('--allow-host 必须为明确的主机名或主机名:端口')
    url='http://127.0.0.1:'+str(args.port)
    try:
        import urllib.request
        with urllib.request.urlopen(url+'/api/identity',timeout=2) as r: existing=json.load(r)
        if not args.no_browser and not args.allow_host and existing.get('version')==VERSION and existing.get('app')==APP_ID and existing.get('data')==data_identity(args.data):
            print('出港成本分析已运行 '+url,flush=True)
            if not args.no_browser: webbrowser.open(url)
            return
    except Exception: pass
    server=ThreadingHTTPServer((args.host,args.port),Handler)
    server.allowed_hosts={h.lower() for h in args.allow_host} if args.allow_host else {'127.0.0.1:'+str(server.server_port),'localhost:'+str(server.server_port)}
    server.data_dir=args.data; server.token=secrets.token_urlsafe(32)
    try:
        warehouse=routes.warehouse(args.data)
    except Exception:
        server.server_close(); raise
    url='http://127.0.0.1:'+str(server.server_port)
    print('出港成本分析 '+url,flush=True)
    if not args.no_browser: threading.Timer(.6,lambda:webbrowser.open(url)).start()
    def stop(*_): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.server_close()
        warehouse.con.close()

if __name__=='__main__': main()
