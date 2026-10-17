"""Shared matrix, detail and export query helpers over the DuckDB warehouse."""
import json, time, uuid
from decimal import Decimal, ROUND_HALF_UP

from . import rules
from .store import now

D = Decimal
SMALL = 30
SUMS = ['weight', 'fee', 'dispatch', 'transit', 'waybill_fee', 'subsidy_amt', 'rebate', 'adj']


def r4(v):
    return float(D(v).quantize(D('0.0001'), rounding=ROUND_HALF_UP))


def r1(v):
    return float(D(v).quantize(D('0.1'), rounding=ROUND_HALF_UP))


class VersionConflict(ValueError):
    pass


def settings(wh, req, cur=None):
    """Merge a request with saved config. Request values are temporary overrides, never saved."""
    cfg = wh.config(cur)
    f = dict(req.get('filters') or {})
    f.setdefault('dateBasis', cfg.get('dateBasis', 'send'))
    if 'excludeRules' not in f:
        f['excludeRules'] = cfg.get('excludeRules', rules.DEFAULT_EXCLUDE)
    from .regions import scope
    f.update(scope(cfg['regionGroups'],req.get('excludeZones',f.get('excludeZones'))))
    cost = dict(req.get('cost') or {})
    flows = dict(req.get('flows') or {})
    if 'groups' not in flows:
        flows['groups'] = cfg.get('groups') or []
    return {
        'filters': f,
        'bands': rules.bands(req.get('bands') or cfg['bands']),
        'flows': flows,
        'policies': rules.policies(cost['subsidyPolicies'] if 'subsidyPolicies' in cost else cfg['subsidyPolicies']),
        'deductRebate': bool(cost['deductRebate'] if 'deductRebate' in cost else cfg.get('deductRebate')),
        'adjustments': rules.adjustments(cost['adjustments'] if 'adjustments' in cost else cfg.get('adjustments')),
        'splitBy': req.get('splitBy'),
    }


def scoped(s, apply_mask=True, rule=None):
    """Build the CTE chain b (scoped rows with flow/band/split/subsidy) -> s (after outlier handling).
    Returns (sql, args). The final relation is named s."""
    if s['splitBy'] not in (None, 'subsidy'):
        raise ValueError('拆分维度只支持客户补贴类型')
    flow_expr, flow_args, fwhere, fargs = rules.flow_sql(s['flows'])
    sub_sql, sub_args = rules.subsidy_sql(s['policies'])
    where, wargs = rules.filter_sql(s['filters'])
    where, wargs = where + fwhere, wargs + fargs
    mask = rules.exclude_mask(s['filters'].get('excludeRules') or [])
    if rule and rule != 'outlier':
        where.append('(flags & %d) <> 0' % rules.BITS[rule])
    elif apply_mask and mask:
        where.append('(flags & %d) = 0' % mask)
    split = 'subsidy' if s['splitBy'] == 'subsidy' else 'CAST(NULL AS VARCHAR)'
    actual = 'fee - subsidy_amt' + (' - rebate' if s['deductRebate'] else '') + ' + adj'
    sql = ('WITH b0 AS (SELECT *, {flow} AS flow, {band} AS band, {split} AS split, CAST({sub} AS DECIMAL(18,4)) AS subsidy_amt, '
           'CAST({adj!r} AS DECIMAL(18,4)) AS adj FROM ship WHERE {where}), '
           'b AS (SELECT *, {actual} AS actual FROM b0)').format(
        flow=flow_expr, band=rules.band_case(s['bands']), split=split, sub=sub_sql, adj=rules.adjustment_total(s['adjustments']),
        where=' AND '.join(where), actual=actual)
    args = flow_args + sub_args + wargs
    enabled, lo, hi = rules.outlier_opts(s['filters'])
    if rule == 'outlier' or enabled:
        sql += (', q AS (SELECT province, band, quantile_cont(fee, ?) AS qlo, quantile_cont(fee, ?) AS qhi FROM b WHERE band >= 0 GROUP BY ALL), '
                'bq AS (SELECT b.*, (b.fee < q.qlo OR b.fee > q.qhi) AS is_outlier FROM b JOIN q USING (province, band))')
        args += [lo, hi]
        cond = 'is_outlier' if rule == 'outlier' else 'NOT is_outlier'
        sql += ', s AS (SELECT * FROM bq WHERE %s)' % cond
    else:
        sql += ', s AS (SELECT * FROM b)'
    return sql, args


def cell(row, deduct):
    n = row['n']
    if not n:
        return None
    w, fee, disp, tran, wb, sub, reb, adj = (D(row[k] or 0) for k in SUMS)
    actual = fee - sub - (reb if deduct else 0) + adj
    return {'n': n, 'weightKg': r4(w), 'avgWeight': r4(w / n), 'system': r4(fee), 'dispatch': r4(disp), 'transit': r4(tran),
            'waybillFee': r4(wb), 'subsidy': r4(sub), 'rebate': r4(reb), 'adjustment': r4(adj), 'actual': r4(actual),
            'avgSystem': r4(fee / n), 'avgActual': r4(actual / n), '_avgActual': actual / n, 'small': n < SMALL}


def add(acc, row):
    acc['n'] += row['n']
    for k in SUMS:
        acc[k] += D(row[k] or 0)


def empty():
    return {'n': 0, **{k: D(0) for k in SUMS}}


def public(c):
    return None if c is None else {k: v for k, v in c.items() if not k.startswith('_')}


def compute_matrix(wh, req):
    with wh.snapshot() as (cur, version):
        return _compute_matrix(wh, req, cur, version)


def _compute_matrix(wh, req, cur, version):
    t0 = time.perf_counter()
    s = settings(wh, req, cur)
    sql, args = scoped(s)
    agg = cur.execute(sql + ' SELECT flow, split, band, count(*) AS n, ' + ', '.join('sum(%s) AS %s' % (k, k) for k in SUMS) +
                      ' FROM s WHERE band >= 0 GROUP BY ALL', args)
    names = [d[0] for d in agg.description]
    data = [dict(zip(names, r)) for r in agg.fetchall()]
    uncovered = cur.execute(sql + ' SELECT count(*) FROM b WHERE band < 0', args).fetchone()[0]
    # Excluded tickets in the same scope, for the "剔除了多少" banner.
    xs = dict(s, filters=dict(s['filters'], excludeRules=[], outlier={}))
    xsql, xargs = scoped(xs)
    mask = rules.exclude_mask(s['filters'].get('excludeRules') or [])
    ex = cur.execute(xsql + ' SELECT count(*), coalesce(sum(fee), 0) FROM s WHERE (flags & %d) <> 0' % mask, xargs).fetchone() if mask else (0, 0)
    # Outliers are measured on rows that survive the business rules, so the two parts never overlap.
    out = (0, 0)
    if rules.outlier_opts(s['filters'])[0]:
        osql, oargs = scoped(dict(s, filters=dict(s['filters'], outlier=dict(s['filters']['outlier'], enabled=False))), rule='outlier')
        out = cur.execute(osql + ' SELECT count(*), coalesce(sum(fee), 0) FROM s', oargs).fetchone()

    nb = len(s['bands'])
    rows, col_tot, row_tot, grand = {}, {}, {}, {}
    for r in data:
        key = (r['flow'], r['split'])
        rows.setdefault(key, [empty() for _ in range(nb)])
        add(rows[key][r['band']], r)
        row_tot.setdefault(key, empty())
        add(row_tot[key], r)
        col_tot.setdefault(r['split'], [empty() for _ in range(nb)])
        add(col_tot[r['split']][r['band']], r)
        grand.setdefault(r['split'], empty())
        add(grand[r['split']], r)
    deduct = s['deductRebate']

    def with_share(c, base):
        if c is not None:
            c['share'] = r4(D(c['n']) / base['n']) if base['n'] else None
        return c

    out_rows = []
    for (flow, split), cells in rows.items():
        out_rows.append({'flow': flow, 'split': split,
                         'cells': [public(with_share(cell(c, deduct), col_tot[split][i])) for i, c in enumerate(cells)],
                         'total': public(with_share(cell(row_tot[(flow, split)], deduct), grand[split]))})
    out_rows.sort(key=lambda x: (-(x['total']['n'] if x['total'] else 0), x['flow'], x['split'] or ''))

    def totals(split):
        return {'cells': [public(with_share(cell(c, deduct), c)) for c in col_tot[split]], 'total': public(with_share(cell(grand[split], deduct), grand[split]))}
    all_cols = [empty() for _ in range(nb)]
    all_grand = empty()
    for split in col_tot:
        for i, c in enumerate(col_tot[split]):
            add(all_cols[i], c)
        add(all_grand, grand[split])
    result = {
        'version': version, 'bands': s['bands'], 'rows': out_rows,
        'totals': {'cells': [public(with_share(cell(c, deduct), c)) for c in all_cols], 'total': public(with_share(cell(all_grand, deduct), all_grand))},
        'splitTotals': {(k if k is not None else ''): totals(k) for k in col_tot} if s['splitBy'] else None,
        'uncovered': {'n': uncovered}, 'excluded': {'n': ex[0] + out[0], 'system': r4(D(ex[1]) + D(out[1])), 'rules': {'n': ex[0], 'system': r4(ex[1])}, 'outlier': {'n': out[0], 'system': r4(out[1])}},
        'settings': {k: v for k, v in s.items()},
    }
    result['elapsedMs'] = round((time.perf_counter() - t0) * 1000)
    return result, rows, deduct


def matrix(wh, req):
    return compute_matrix(wh, req)[0]






def rows(wh, req):
    with wh.snapshot() as (cur, _):
        return _rows(wh, req, cur)


def _rows(wh, req, cur):
    s = settings(wh, req, cur)
    rule = req.get('rule')
    if rule and rule not in rules.BITS and rule != 'outlier':
        raise ValueError('未知异常规则')
    sql, args = scoped(s, rule=rule)
    where, wargs = ['1=1'], []
    if req.get('flow') is not None:
        where.append('flow = ?')
        wargs.append(req['flow'])
    if req.get('band') is not None:
        where.append('band = ?')
        wargs.append(int(req['band']))
    if 'split' in req and s['splitBy']:
        where.append('split IS NOT DISTINCT FROM ?')
        wargs.append(req['split'])
    offset, limit = max(0, int(req.get('offset', 0))), min(200, max(1, int(req.get('limit', 100))))
    tail = ' FROM s WHERE ' + ' AND '.join(where)
    total = cur.execute(sql + ' SELECT count(*)' + tail, args + wargs).fetchone()[0]
    res = cur.execute(sql + ''' SELECT id, waybill, day, send_day, province, city, district, customer, subsidy, weight, fee, dispatch, transit,
        waybill_fee, subsidy_amt, actual, flags''' + tail + ' ORDER BY id LIMIT ? OFFSET ?', args + wargs + [limit, offset]).fetchall()
    names = {r['bit']: r['code'] for r in rules.RULES if r['bit']}
    out = []
    for r in res:
        out.append({'id': r[0], 'waybill': r[1], 'day': str(r[2]) if r[2] else None, 'sendDay': str(r[3]) if r[3] else None,
                    'province': r[4], 'city': r[5], 'district': r[6], 'customer': r[7], 'subsidy': r[8],
                    'weight': r4(r[9] or 0), 'system': r4(r[10] or 0), 'dispatch': r4(r[11] or 0), 'transit': r4(r[12] or 0),
                    'waybillFee': r4(r[13] or 0), 'subsidyAmt': r4(r[14] or 0), 'actual': r4(r[15] or 0),
                    'flags': [code for bit, code in names.items() if r[16] & bit]})
    return {'total': total, 'rows': out}


def trend(wh, req):
    s = settings(wh, req)
    sql, args = scoped(s)
    col = rules.date_col(s['filters'])
    res = wh.cursor().execute(sql + ' SELECT %s AS d, split, count(*), sum(fee), sum(actual) FROM s WHERE band >= 0 GROUP BY ALL ORDER BY d, split' % col, args).fetchall()
    return {'rows': [{'day': str(r[0]) if r[0] else None, 'split': r[1], 'n': r[2], 'system': r4(r[3]), 'actual': r4(r[4]),
                      'avgActual': r4(D(r[4]) / r[2])} for r in res]}












def band_label(b):
    return '%g–%gkg' % (b['lo'], b['hi']) if b['hi'] < 9999 else '%gkg以上' % b['lo']


def export_sheets(wh, req):
    """Returns [(sheet name, headers, rows)] for server.multi_workbook."""
    result, _, deduct = compute_matrix(wh, req)
    kind = req.get('kind', 'internal')
    labels = [band_label(b) for b in result['bands']]
    f = result['settings']['filters']
    period = '%s 至 %s（%s）' % (f.get('start') or '最早', f.get('end') or '最新', '寄件日期' if f.get('dateBasis') == 'send' else '交件日期')
    if kind == 'quote':
        raise ValueError('请到报价页预览、保存后导出')
    head = ['流向'] + (['补贴类型'] if result['settings']['splitBy'] else []) + ['重量段', '票数', '占比', '均重kg', '系统成本', '派费', '中转费', '面单加价', '补贴', '返利', '额外成本', '实际成本', '单票系统成本', '单票实际成本']
    body = []
    for r in result['rows']:
        for i, c in enumerate(r['cells']):
            if c:
                body.append([r['flow']] + ([r['split'] or '无补贴'] if result['settings']['splitBy'] else []) + [labels[i], c['n'], c['share'], c['avgWeight'],
                            c['system'], c['dispatch'], c['transit'], c['waybillFee'], c['subsidy'], c['rebate'], c['adjustment'], c['actual'], c['avgSystem'], c['avgActual']])
    return [('成本矩阵明细', head, body),
            ('口径', ['项目', '内容'], [['样本期间', period], ['数据版本', result['version']],
                                    ['实际成本', '系统成本（总费用）− 客户补贴' + ('− 返利' if deduct else '') + ' ± 额外成本'],
                                    ['额外成本', '；'.join('%s %s%g 元/票' % (a['name'], '+' if a['sign'] > 0 else '−', a['amount']) for a in result['settings']['adjustments'] if a['enabled']) or '无'],
                                    ['补贴政策', '；'.join('%s 每票 %g 元' % (p['type'], p['perTicket']) for p in result['settings']['policies']) or '无'],
                                    ['剔除票数', result['excluded']['n']], ['未覆盖重量段票数', result['uncovered']['n']]])]


def meta(wh):
    with wh.snapshot() as (cur, version):
        return _meta(wh, cur, version)


def _meta(wh, cur, version):
    counts = cur.execute('SELECT count(*), count(*) FILTER (WHERE state=1), count(*) FILTER (WHERE state=0) FROM ship').fetchone()
    rng = cur.execute('SELECT min(send_day), max(send_day), min(day), max(day) FROM ship WHERE state=1').fetchone()
    months = {k: [r[0] for r in cur.execute("SELECT DISTINCT strftime(%s, '%%Y-%%m') m FROM ship WHERE state=1 AND %s IS NOT NULL ORDER BY m" % (c, c)).fetchall()]
              for k, c in (('send', 'send_day'), ('handover', 'day'))}
    geo = {}
    for p, c, d in cur.execute("SELECT DISTINCT province, city, district FROM ship WHERE state=1 AND province <> '' ORDER BY ALL").fetchall():
        geo.setdefault(p, {}).setdefault(c, [])
        if d:
            geo[p][c].append(d)
    dims = lambda col: [{'name': r[0], 'n': r[1]} for r in cur.execute('SELECT %s, count(*) FROM ship WHERE state=1 GROUP BY 1 ORDER BY 2 DESC' % col).fetchall()]
    batches = [{'id': r[0], 'name': r[1], 'created': r[2], 'stats': json.loads(r[3])} for r in cur.execute('SELECT id, name, created, stats FROM batches ORDER BY created DESC').fetchall()]
    return {
        'version': version,
        'counts': {'total': counts[0], 'active': counts[1], 'pending': counts[2]},
        'dateRanges': {'send': {'min': str(rng[0]) if rng[0] else None, 'max': str(rng[1]) if rng[1] else None},
                       'handover': {'min': str(rng[2]) if rng[2] else None, 'max': str(rng[3]) if rng[3] else None}},
        'months': months,
        'subsidies': [{'type': x['name'], 'n': x['n']} for x in dims('subsidy')],
        'customers': dims('customer'), 'packs': dims('pack'), 'shifts': dims('shift'), 'products': dims('product_norm'),
        'features': {'analysisV3': True, 'quotationV3': True},
        'geo': geo,
        'rules': [{k: r[k] for k in ('code', 'label', 'action', 'description')} for r in rules.RULES],
        'config': wh.config(cur), 'batches': batches,
    }
