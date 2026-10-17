"""DuckDB warehouse: import, de-duplication, versions and pending review. Analytics live in analytics.py."""
import csv, datetime as dt, hashlib, json, shutil, tempfile, threading, uuid
from contextlib import contextmanager
from pathlib import Path

import duckdb

from . import rules, business_day

REQUIRED = ['运单号', '交件时间', '省', '结算重量(kg)', '总费用(元)']
# Volatile columns that change without changing the bill; excluded from the content fingerprint.
VOLATILE = {'签收时间'}

# ship column -> original column (None = derived after the copy).
ATTRS = {'policy_business': '政策业务类型', 'channel': '渠道类型', 'client_type': '客户类型', 'weight_adjust': '结算重量调整类型',
         'nature': '交件性质', 'center': '交件中心', 'send_site': '寄件网点', 'item': '物品类型',
         'return_province': '退/派省份', 'return_center': '退/派中心', 'product_norm': None, 'policy_norm': None}

STATE_ACTIVE, STATE_PENDING, STATE_SUPPLEMENT, STATE_RETIRED = 1, 0, 2, -1


def now():
    return dt.datetime.now().isoformat(timespec='seconds')


def q(name):
    return '"' + str(name).replace('"', '""') + '"'


def cell_text(v):
    if v is None:
        return ''
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=' ', timespec='seconds')
    if isinstance(v, (dt.date, dt.time)):
        return v.isoformat()
    return str(v)


def calamine_rows(path):
    """Fast reader (Rust). Loads one sheet at a time into memory: ~5GB for a 1M-row, 71-column sheet."""
    from python_calamine import CalamineWorkbook
    wb = CalamineWorkbook.from_path(str(path))
    for name in wb.sheet_names:
        for line, row in enumerate(wb.get_sheet_by_name(name).iter_rows(), start=1):
            yield name, line, [cell_text(v) for v in row]


def streaming_rows(path):
    """Slow, low-memory fallback (standard library streaming XML)."""
    from .xlsx import xlsx_rows
    yield from xlsx_rows(path)


def num(col):
    return 'TRY_CAST(NULLIF(trim(%s), \'\') AS DECIMAL(18,4))' % col


def day(col):
    return ("CASE WHEN regexp_full_match(trim({c}), '\\d+(\\.\\d+)?') THEN DATE '1899-12-30' + CAST(floor(CAST(trim({c}) AS DOUBLE)) AS INTEGER) "
            "ELSE TRY_CAST(substr(replace(trim({c}), '/', '-'), 1, 10) AS DATE) END").format(c=col)


class Warehouse:
    def __init__(self, home):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / 'originals').mkdir(exist_ok=True)
        self.path = self.home / 'warehouse.duckdb'
        self.con = duckdb.connect(str(self.path))
        self.wlock = threading.Lock()
        self.jobs = {}
        self.con.execute('''
        CREATE TABLE IF NOT EXISTS meta(key VARCHAR PRIMARY KEY, value VARCHAR);
        CREATE TABLE IF NOT EXISTS batches(id VARCHAR PRIMARY KEY, name VARCHAR, sha256 VARCHAR UNIQUE, created VARCHAR, stats VARCHAR);
        CREATE SEQUENCE IF NOT EXISTS ship_id;
        CREATE TABLE IF NOT EXISTS raw(id BIGINT, batch VARCHAR, sheet VARCHAR, line INTEGER);
        CREATE TABLE IF NOT EXISTS ship(
          id BIGINT PRIMARY KEY, batch VARCHAR, hash VARCHAR, waybill VARCHAR, day DATE, send_day DATE,
          province VARCHAR, city VARCHAR, district VARCHAR, customer VARCHAR, account VARCHAR, subsidy VARCHAR,
          pack VARCHAR, shift VARCHAR, policy_op VARCHAR, product VARCHAR, status VARCHAR,
          weight DECIMAL(18,4), fee DECIMAL(18,4), dispatch DECIMAL(18,4), transit DECIMAL(18,4), waybill_fee DECIMAL(18,4),
          return_fee DECIMAL(18,4), rebate DECIMAL(18,4), w_site DECIMAL(18,4), w_pack DECIMAL(18,4), w_center DECIMAL(18,4),
          flags INTEGER, state TINYINT, issue VARCHAR);
        CREATE TABLE IF NOT EXISTS snapshots(id VARCHAR PRIMARY KEY, name VARCHAR, created VARCHAR, version BIGINT, request VARCHAR, result VARCHAR);
        CREATE TABLE IF NOT EXISTS audit(created VARCHAR, action VARCHAR, payload VARCHAR);
        CREATE TABLE IF NOT EXISTS quotes(id VARCHAR PRIMARY KEY, name VARCHAR, target VARCHAR, created VARCHAR, version BIGINT, request VARCHAR, result VARCHAR);
        ''')
        self.con.execute("INSERT OR IGNORE INTO meta VALUES ('version', '0')")
        self.con.execute("INSERT OR IGNORE INTO meta VALUES ('config', ?)", [json.dumps(rules.DEFAULT_CONFIG, ensure_ascii=False)])
        self._migrate()
        from . import imports
        imports.migrate(self)
        self._migrate_simulation()
        business_day.migrate(self)

    def _migrate(self):
        """Policy attributes used by layering and quoting; added in place and backfilled from raw."""
        have = {r[0] for r in self.con.execute('DESCRIBE ship').fetchall()}
        missing = [k for k in ATTRS if k not in have]
        for k in missing:
            self.con.execute('ALTER TABLE ship ADD COLUMN %s VARCHAR' % k)
        if missing and self.con.execute('SELECT count(*) FROM ship').fetchone()[0]:
            c = self.cursor()
            c.execute('BEGIN')
            self._fill_attrs(c)
            c.execute('COMMIT')

    def _migrate_simulation(self):
        from . import simulation
        c=self.cursor();c.execute('BEGIN')
        try:
            have={r[0] for r in c.execute('DESCRIBE ship').fetchall()}
            if 'return_bill' not in have:
                c.execute('ALTER TABLE ship ADD COLUMN return_bill DECIMAL(18,4) DEFAULT 0')
                self._fill_return_bill(c)
            cfg=json.loads(c.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])
            if 'simulation' not in cfg:
                cfg['simulation']=simulation.migrated(cfg)
                cfg['simulationLegacy']={k:cfg.get(k) for k in ('subsidyPolicies','adjustments','deductRebate')}
                c.execute("UPDATE meta SET value=? WHERE key='config'",[json.dumps(cfg,ensure_ascii=False)])
                if c.execute('SELECT count(*) FROM ship').fetchone()[0]:
                    self.bump(c);self.audit(c,'成本模拟迁移',{'allDisabled':True,'returnBillBackfilled':True})
            c.execute('COMMIT')
        except Exception:c.execute('ROLLBACK');raise
        finally:c.close()

    def _fill_return_bill(self,c,ids_sql=None):
        raw_cols={r[0] for r in c.execute('DESCRIBE raw').fetchall()}
        if '退件费' not in raw_cols:return
        scope=(' AND ship.id IN ('+ids_sql+')') if ids_sql else ''
        c.execute('UPDATE ship SET return_bill=coalesce('+num('r.'+q('退件费'))+',0) FROM raw r WHERE r.id=ship.id'+scope)
        # Older incremental supplements have already been merged into the bill fee.
        c.execute('UPDATE ship SET return_bill=ship.return_bill+s.amount FROM '
                  '(SELECT waybill,sum(return_bill) AS amount FROM ship WHERE state=2 GROUP BY waybill) s '
                  'WHERE ship.state=1 AND ship.waybill=s.waybill'+scope)

    def _fill_attrs(self, c, batch=None):
        raw_cols = {r[0] for r in c.execute('DESCRIBE raw').fetchall()}
        sets = ', '.join("%s = coalesce(trim(%s), '')" % (k, 'r.' + q(src) if src in raw_cols else "''")
                         for k, src in ATTRS.items() if src)
        c.execute('UPDATE ship SET %s FROM raw r WHERE r.id = ship.id%s' % (sets, ' AND ship.batch = ?' if batch else ''), [batch] if batch else [])
        # Scene tags (拦截/退改/自发自签/首中心退…) are stripped so a policy layer is the same with or without them.
        c.execute('''UPDATE ship SET
            product_norm = coalesce(NULLIF(array_to_string(list_sort(list_filter(string_split(product, ','), x -> trim(x) NOT IN ('', %s))), ','), ''), '无增值'),
            policy_norm = coalesce(array_to_string(list_sort(list_filter(string_split(policy_op, ','), x -> trim(x) NOT IN ('', %s))), ','), '')%s''' % (
            ', '.join("'%s'" % t for t in rules.SCENE_TAGS['product']), ', '.join("'%s'" % t for t in rules.SCENE_TAGS['policy']),
            ' WHERE batch = ?' if batch else ''), [batch] if batch else [])

    def cursor(self):
        return self.con.cursor()

    @contextmanager
    def snapshot(self):
        """Read-only transaction: every query and the version number come from the same committed state."""
        c = self.cursor()
        c.execute('BEGIN TRANSACTION')
        try:
            yield c, self.version(c)
        finally:
            c.execute('ROLLBACK')
            c.close()

    def version(self, c=None):
        return int((c or self.cursor()).execute("SELECT value FROM meta WHERE key='version'").fetchone()[0])

    def bump(self, c):
        c.execute("UPDATE meta SET value = CAST(CAST(value AS BIGINT) + 1 AS VARCHAR) WHERE key='version'")

    def audit(self, c, action, payload):
        c.execute('INSERT INTO audit VALUES (?, ?, ?)', [now(), action, json.dumps(payload, ensure_ascii=False, default=str)])

    # ---------- config ----------
    def config(self, c=None):
        cfg = dict(rules.DEFAULT_CONFIG)
        cfg.update(json.loads((c or self.cursor()).execute("SELECT value FROM meta WHERE key='config'").fetchone()[0]))
        from .regions import validate
        cfg['regionGroups'] = validate(cfg.get('regionGroups'))
        return cfg

    def validate_config(self, cfg):
        out = dict(rules.DEFAULT_CONFIG)
        out.update(cfg or {})
        out['bands'] = rules.bands(out['bands'])
        out['bandPresets'] = [{'name': str(p['name']).strip(), 'bands': rules.bands(p['bands'])} for p in out.get('bandPresets') or [] if str(p.get('name', '')).strip()]
        out['subsidyPolicies'] = rules.policies(out['subsidyPolicies'])
        rules.flow_sql({'groups': out.get('groups') or []})
        rules.exclude_mask(out.get('excludeRules') or [])
        out['deductRebate'] = bool(out.get('deductRebate'))
        out['adjustments'] = rules.adjustments(out.get('adjustments'))
        from . import simulation
        out['simulation']=simulation.validate(out.get('simulation'))
        from .regions import validate
        out['regionGroups']=validate(out.get('regionGroups'))
        rules.date_col(out)
        if out.get('conflictRule') not in ('replace','review'):
            raise ValueError('冲突规则只能是最新导入优先或逐条核对')
        if 'analysis' in out:
            from .analysis import parameters
            out['analysis'] = parameters(out['analysis'])
        return out

    def save_config(self, cfg):
        expected=cfg.get('expectedVersion')
        cfg = self.validate_config({k:v for k,v in cfg.items() if k!='expectedVersion'})
        with self.wlock:
            c = self.cursor()
            c.execute('BEGIN TRANSACTION')
            try:
                if expected is not None and int(expected)!=self.version(c):
                    from .analytics import VersionConflict
                    raise VersionConflict('配置已更新，请刷新后再保存')
                c.execute("UPDATE meta SET value=? WHERE key='config'", [json.dumps(cfg, ensure_ascii=False)])
                self.audit(c, '保存配置', cfg)
                self.bump(c)
                version = self.version(c)
                c.execute('COMMIT')
            except Exception:
                c.execute('ROLLBACK')
                raise
            finally:
                c.close()
        return {'ok': True, 'version': version}

    # ---------- import ----------
    def start_import(self, path, name=None, cleanup=False, group_id=None):
        jid = uuid.uuid4().hex
        self.jobs[jid] = {'state': 'queued', 'phase': '排队', 'rows': 0, 'message': '', 'groupId':group_id}

        def work():
            try:
                self.import_file(path, name, self.jobs[jid], group_id=group_id)
            except Exception as ex:
                self.jobs[jid].update(state='error', message=str(ex))
            finally:
                if cleanup:
                    Path(path).unlink(missing_ok=True)
        threading.Thread(target=work, daemon=True).start()
        return {'jobs': [jid]}

    def import_file(self, path, name=None, job=None, group_id=None):
        from . import imports
        job = job if job is not None else {}
        path=Path(path);name=name or path.name
        auto=group_id is None
        gid=group_id or imports.create(self)['id']
        job.update(state='running',phase='校验文件',rows=0,groupId=gid)
        try:
            sha=hashlib.sha256()
            with path.open('rb') as f:
                for b in iter(lambda:f.read(8<<20),b''):sha.update(b)
            digest=sha.hexdigest()
            def duplicate():
                c=self.cursor();imports.require_open(c,gid)
                old=c.execute("SELECT b.id,b.stats FROM batches b WHERE (b.sha256=? OR starts_with(b.sha256,?)) AND EXISTS (SELECT 1 FROM import_files f JOIN import_groups g ON g.id=f.group_id WHERE f.batch_id=b.id AND g.status<>'undone') ORDER BY b.created DESC LIMIT 1",[digest,digest+'#']).fetchone()
                if not old:return None
                stats=json.loads(old[1]);stats.update(new=0,replaced=0,conflict=0,duplicate=stats['rows'],system=0)
                imports.file_link(c,gid,old[0],name,stats)
                return stats
            with self.wlock:stats=duplicate()
            if stats is None:
                bid=uuid.uuid4().hex
                with tempfile.TemporaryDirectory(dir=self.home) as td:
                    files,headers=self._to_tsv(path,Path(td),job)
                    if not files:raise ValueError('未找到包含“运单号”的明细工作表')
                    missing=[k for k in REQUIRED if not all(k in h for h in headers)]
                    if missing:raise ValueError('缺少字段：'+'、'.join(missing))
                    job.update(phase='写入数据库')
                    with self.wlock:
                        stats=duplicate()
                        if stats is None:
                            dest=self.home/'originals'/(bid+path.suffix)
                            shutil.copy2(path,dest)
                            stored=digest if not self.cursor().execute('SELECT 1 FROM batches WHERE sha256=?',[digest]).fetchone() else digest+'#'+bid
                            try:stats=self._load(bid,name,stored,files,headers,gid)
                            except Exception:dest.unlink(missing_ok=True);raise
            job.update(state='done',phase='完成',message='该文件已导入过，未重复累计' if stats['duplicate']==stats['rows'] else '导入完成',rows=stats['rows'],stats=stats)
            if auto:imports.finish(self,{'id':gid})
            return job
        except Exception:
            if auto:
                with self.wlock:self.cursor().execute("UPDATE import_groups SET status='partial' WHERE id=?",[gid])
            raise

    def _to_tsv(self, path, folder, job):
        try:
            return self._write_tsv(calamine_rows(path), folder, job)
        except MemoryError:
            for f in folder.glob('*.tsv'):
                f.unlink()
            job.update(message='内存不足，改用低内存模式读取（较慢）')
            return self._write_tsv(streaming_rows(path), folder, job)

    def _write_tsv(self, rows, folder, job):
        """Each sheet becomes one TSV; rows before the header line (containing 运单号) are skipped."""
        files, headers, total = [], [], 0
        job.update(phase='读取 Excel')
        current, header, keep, f, w = None, None, None, None, None
        try:
            for sheet, line, row in rows:
                if sheet != current:
                    current, header = sheet, None
                if header is None:
                    cleaned = [str(v).strip() for v in row]
                    if '运单号' not in cleaned:
                        continue
                    header = cleaned
                    keep = [i for i, h in enumerate(header) if h]
                    if f:
                        f.close()
                    out = folder / ('%d.tsv' % len(files))
                    f = out.open('w', newline='', encoding='utf-8')
                    w = csv.writer(f, delimiter='\t', quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator='\n')
                    w.writerow(['_sheet', '_line'] + [header[i] for i in keep])
                    files.append(str(out))
                    headers.append([header[i] for i in keep])
                    continue
                if not any(row):
                    continue
                row = list(row) + [''] * (len(header) - len(row))
                w.writerow([sheet, line] + [row[i] for i in keep])
                total += 1
                if total % 100000 == 0:
                    job.update(rows=total, message='已读取 %d 行' % total)
        finally:
            if f:
                f.close()
        job.update(rows=total)
        return files, headers

    def _load(self, bid, name, digest, files, headers, group_id):
        from . import imports
        c = self.cursor()
        c.execute('BEGIN')
        try:
            conflict_rule=imports.require_open(c,group_id)
            c.execute("CREATE OR REPLACE TEMP TABLE stage AS SELECT * FROM read_csv(?, delim='\t', quote='\"', escape='\"', header=true, all_varchar=true, union_by_name=true)", [files])
            cols = [r[0] for r in c.execute('DESCRIBE stage').fetchall() if r[0] not in ('_sheet', '_line')]
            raw_cols = {r[0] for r in c.execute('DESCRIBE raw').fetchall()}
            for col in cols:
                if col not in raw_cols:
                    c.execute('ALTER TABLE raw ADD COLUMN %s VARCHAR' % q(col))
            hash_cols = sorted(x for x in cols if x not in VOLATILE)
            fingerprint = 'md5(concat_ws(chr(31), %s))' % ', '.join("coalesce(%s, '')" % q(x) for x in hash_cols)
            col = lambda n: q(n) if n in cols else 'NULL'
            text = lambda n: "coalesce(trim(%s), '')" % col(n)
            rows = c.execute('SELECT count(*) FROM stage').fetchone()[0]
            c.execute('''CREATE OR REPLACE TEMP TABLE staged AS
              WITH h AS (SELECT *, {fp} AS _hash, CAST(_line AS INTEGER) AS _ln FROM stage),
              d AS (SELECT * FROM h QUALIFY row_number() OVER (PARTITION BY _hash ORDER BY _sheet, _ln) = 1)
              SELECT nextval('ship_id') AS _id, d.* FROM d WHERE _hash NOT IN (SELECT hash FROM ship WHERE state IN (0,1,2))'''.format(fp=fingerprint))
            c.execute('CREATE OR REPLACE TEMP TABLE ambiguous_waybills AS SELECT '+text('运单号')+' AS waybill FROM stage GROUP BY 1 HAVING count(DISTINCT '+fingerprint+')>1')
            new = c.execute('SELECT count(*) FROM staged').fetchone()[0]
            imports.event(c,group_id,bid,'SELECT id FROM ship WHERE waybill IN (SELECT '+text('运单号')+' FROM staged)')
            replacements=c.execute('SELECT count(DISTINCT waybill) FROM ship WHERE state=1 AND waybill IN (SELECT '+text('运单号')+' FROM staged)').fetchone()[0]
            c.execute('INSERT INTO raw BY NAME SELECT _id AS id, ? AS batch, _sheet AS sheet, _ln AS line, %s FROM staged' % ', '.join(q(x) for x in cols), [bid])
            customer = "coalesce(NULLIF(%s, ''), NULLIF(%s, ''), NULLIF(%s, ''), '未归属客户')" % (text('所属客户'), text('电子面单名称'), text('电子面单账号'))
            c.execute('''INSERT INTO ship (id, batch, hash, waybill, day, send_day, province, city, district, customer, account, subsidy,
                pack, shift, policy_op, product, status, weight, fee, dispatch, transit, waybill_fee, return_fee, rebate, w_site, w_pack, w_center, flags, state, issue)
              SELECT _id, ?, _hash, {waybill}, {day}, {send_day}, {prov}, {city}, {dist}, {customer}, {account}, {subsidy},
                {pack}, {shift}, {policy}, {product}, {status},
                {weight}, {fee}, {dispatch}, {transit}, {wfee}, coalesce({rf1}, 0) + coalesce({rf2}, 0), coalesce({rb1}, 0) + coalesce({rb2}, 0),
                coalesce({ws}, 0), coalesce({wp}, 0), coalesce({wc}, 0), 0,
                CASE WHEN {waybill} IN (SELECT waybill FROM ship WHERE state IN (0, 1, 2))
                       OR row_number() OVER (PARTITION BY {waybill} ORDER BY _sheet, _ln) > 1 THEN 0 ELSE 1 END, ''
              FROM staged'''.format(
                waybill=text('运单号'), day=business_day.day_sql(col('交件时间')), send_day=day(col('寄件时间')), prov=text('省'), city=text('市'), dist=text('区'),
                customer=customer, account=text('电子面单账号'), subsidy=text('客户补贴类型'), pack=text('建包类型'), shift=text('班次类型'),
                policy=text('政策操作类型'), product=text('增值类型'), status=text('计费状态'),
                weight=num(col('结算重量(kg)')), fee=num(col('总费用(元)')), dispatch=num(col('派费合计')), transit=num(col('中转费合计')),
                wfee=num(col('面单加价(元)')), rf1=num(col('退件中转费')), rf2=num(col('退件操作费')),
                rb1=num(col('中转返利金额(元)')), rb2=num(col('二级中转返利金额(元)')),
                ws=num(col('揽件网点重量(KG)')), wp=num(col('集包网点重量(KG)')), wc=num(col('中心实重(KG)'))), [bid])
            if '退件费' in cols:
                c.execute('UPDATE ship SET return_bill=coalesce('+num('r.'+q('退件费'))+',0) FROM raw r WHERE r.id=ship.id AND ship.batch=?',[bid])
            self._fill_attrs(c, bid)
            c.execute("UPDATE ship SET flags = %s, issue = CASE WHEN state = 0 THEN '同运单号内容不同：新版本或补收待确认' ELSE '' END WHERE batch = ?" % rules.FLAGS_SQL, [bid])
            if conflict_rule=='replace':
                c.execute("UPDATE ship SET state=-1,issue='已被最新导入替换' WHERE batch<>? AND waybill IN (SELECT waybill FROM ship WHERE batch=?) AND waybill NOT IN (SELECT waybill FROM ambiguous_waybills) AND state IN (0,1,2)",[bid,bid])
                c.execute("UPDATE ship SET state=1,issue='' WHERE batch=? AND waybill NOT IN (SELECT waybill FROM ambiguous_waybills)",[bid])
            c.execute("UPDATE ship SET state=0,issue='同文件存在多个版本，请逐条核对' WHERE batch=? AND waybill IN (SELECT waybill FROM ambiguous_waybills)",[bid])
            s = c.execute('''SELECT count(*) FILTER (WHERE state=1), count(*) FILTER (WHERE state=0), coalesce(sum(fee) FILTER (WHERE state=1), 0),
                min(day), max(day), min(send_day), max(send_day) FROM ship WHERE batch=?''', [bid]).fetchone()
            stats = {'rows': rows, 'new': s[0], 'conflict': s[1], 'duplicate': rows - new, 'system': float(s[2]),
                     'replaced': replacements if conflict_rule=='replace' else 0, 'minDay': str(s[3]) if s[3] else None, 'maxDay': str(s[4]) if s[4] else None,
                     'minSendDay': str(s[5]) if s[5] else None, 'maxSendDay': str(s[6]) if s[6] else None}
            c.execute('INSERT INTO batches VALUES (?, ?, ?, ?, ?)', [bid, name, digest, now(), json.dumps(stats, ensure_ascii=False)])
            c.execute('INSERT INTO import_members SELECT ?,s.id FROM ship s WHERE hash IN (SELECT '+fingerprint+' FROM stage) QUALIFY row_number() OVER (PARTITION BY hash ORDER BY id DESC)=1',[bid])
            imports.file_link(c,group_id,bid,name,stats)
            self.audit(c, '导入账单', {'batch': bid, 'name': name, 'stats': stats})
            if new:self.bump(c)
            c.execute('COMMIT')
        except Exception:
            c.execute('ROLLBACK')
            raise
        finally:
            c.execute('DROP TABLE IF EXISTS stage')
            c.execute('DROP TABLE IF EXISTS staged')
            c.execute('DROP TABLE IF EXISTS ambiguous_waybills')
            c.close()
        return stats

    # ---------- pending review ----------
    def pending(self, req):
        offset, limit = max(0, int(req.get('offset', 0))), min(200, max(1, int(req.get('limit', 50))))
        c = self.cursor()
        total = c.execute('SELECT count(*) FROM ship WHERE state=0').fetchone()[0]
        rows = c.execute('''SELECT id, waybill, day, send_day, province, customer, weight, fee, issue, batch FROM ship
            WHERE state=0 ORDER BY id LIMIT ? OFFSET ?''', [limit, offset]).fetchall()
        out = []
        for r in rows:
            existing = c.execute('SELECT id, state, fee, batch FROM ship WHERE waybill=? AND state IN (1, 2) ORDER BY id', [r[1]]).fetchall()
            out.append({'id': r[0], 'waybill': r[1], 'day': str(r[2]) if r[2] else None, 'sendDay': str(r[3]) if r[3] else None,
                        'province': r[4], 'customer': r[5], 'weight': float(r[6] or 0), 'system': float(r[7] or 0), 'issue': r[8], 'batch': r[9],
                        'existing': [{'id': e[0], 'state': e[1], 'system': float(e[2] or 0), 'batch': e[3]} for e in existing]})
        return {'total': total, 'rows': out}

    def resolve(self, req):
        action, ids = req.get('action'), [int(i) for i in req.get('ids') or []]
        if action not in ('replace', 'append', 'ignore'):
            raise ValueError('处理方式只能是替换、增量补收或排除')
        if not ids:
            raise ValueError('请选择待核记录')
        with self.wlock:
            c = self.cursor()
            c.execute('BEGIN')
            try:
                if req.get('expectedVersion') is not None and int(req['expectedVersion'])!=self.version(c):
                    from .analytics import VersionConflict
                    raise VersionConflict('待核记录已更新，请刷新后再处理')
                if action=='replace':
                    selected=c.execute('SELECT waybill,count(*) FROM ship WHERE id IN ('+','.join('?'*len(ids))+') GROUP BY 1 HAVING count(*)>1',ids).fetchall()
                    if selected:raise ValueError('同一运单请只选择一个版本进行替换')
                for rid in dict.fromkeys(ids):
                    r = c.execute('SELECT waybill, state FROM ship WHERE id=?', [rid]).fetchone()
                    if not r or r[1] != STATE_PENDING:
                        raise ValueError('记录 %d 不在待核队列' % rid)
                    from . import imports
                    gid=imports.group_for_row(c,rid)
                    imports.event(c,gid,ids_sql='SELECT id FROM ship WHERE waybill=?',args=[r[0]])
                    if action == 'replace':
                        c.execute('UPDATE ship SET state=-1, issue=\'已被新版本替换\' WHERE waybill=? AND state IN (1, 2)', [r[0]])
                        c.execute("UPDATE ship SET state=1, issue='已人工确认替换旧版本' WHERE id=?", [rid])
                    elif action == 'append':
                        base = c.execute('SELECT id FROM ship WHERE waybill=? AND state=1', [r[0]]).fetchone()
                        if not base:
                            raise ValueError('运单 %s 没有有效原记录，不能作为补收' % r[0])
                        # Supplements add money to the original shipment; weight, destination and policy stay the original's.
                        c.execute('''UPDATE ship SET fee=ship.fee+coalesce(s.fee,0), dispatch=ship.dispatch+coalesce(s.dispatch,0), transit=ship.transit+coalesce(s.transit,0),
                            waybill_fee=ship.waybill_fee+coalesce(s.waybill_fee,0), return_fee=ship.return_fee+s.return_fee, return_bill=ship.return_bill+coalesce(s.return_bill,0), rebate=ship.rebate+s.rebate
                            FROM (SELECT * FROM ship WHERE id=?) s WHERE ship.id=?''', [rid, base[0]])
                        c.execute('UPDATE ship SET flags = %s WHERE id=?' % rules.FLAGS_SQL, [base[0]])
                        c.execute("UPDATE ship SET state=2, issue='已人工确认增量补收，金额已并入原运单' WHERE id=?", [rid])
                    else:
                        c.execute("UPDATE ship SET state=-1, issue='已人工排除' WHERE id=?", [rid])
                self.audit(c, '处理待核', {'action': action, 'ids': ids})
                self.bump(c)
                c.execute('COMMIT')
            except Exception:
                c.execute('ROLLBACK')
                raise
        return {'ok': True, 'version': self.version()}

    def raw(self, rid):
        c = self.cursor()
        cur = c.execute('SELECT * FROM raw WHERE id=?', [int(rid)])
        names = [d[0] for d in cur.description]
        r = cur.fetchone()
        if not r:
            raise ValueError('记录不存在')
        d = dict(zip(names, r))
        b = c.execute('SELECT name FROM batches WHERE id=?', [d['batch']]).fetchone()
        source = {'batch': d.pop('batch'), 'file': b[0] if b else None, 'sheet': d.pop('sheet'), 'line': d.pop('line')}
        d.pop('id')
        return {'raw': {k: v for k, v in d.items() if v is not None}, 'source': source}
