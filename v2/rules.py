"""Anomaly rules, subsidy policies, weight bands and flow mapping, expressed as DuckDB SQL fragments."""
import datetime as dt
import math

# Bit flags written at import time. Toggling a rule only changes the exclusion mask, never the data.
RULES = [
    {'code': 'no_fee', 'bit': 1, 'label': '没有金额', 'action': 'exclude', 'description': '计费状态不是“计费成功”，或总费用 ≤ 0'},
    {'code': 'no_weight', 'bit': 2, 'label': '全程无重', 'action': 'exclude', 'description': '揽件、集包、中心重量都没有，按默认重计费'},
    {'code': 'no_dest', 'bit': 4, 'label': '缺目的地', 'action': 'exclude', 'description': '缺省份'},
    {'code': 'self_sign', 'bit': 8, 'label': '自发自签', 'action': 'exclude', 'description': '政策操作类型含“自发自签”'},
    {'code': 'return', 'bit': 16, 'label': '退件 / 拦截 / 退改', 'action': 'exclude', 'description': '首中心退、首中心后退，或增值类型含拦截、退改；属于增值费用，不计入客户实际成本'},
    {'code': 'fee_mismatch', 'bit': 32, 'label': '费用不平', 'action': 'flag', 'description': '总费用 ≠ 派费合计 + 中转费合计 + 面单加价（差额 > 0.01）'},
    {'code': 'outlier', 'bit': 0, 'label': '统计离群', 'action': 'flag', 'description': '同省份同重量段单票系统成本超出所设分位区间'},
]
BITS = {r['code']: r['bit'] for r in RULES if r['bit']}
DEFAULT_EXCLUDE = [r['code'] for r in RULES if r['action'] == 'exclude']
# Tickets carrying any of these bits are not "normal" shipments for layering and quoting.
SPECIAL_MASK = 1 | 2 | 4 | 8 | 16
SCENES = [
    {'code': 'self', 'bit': 8, 'label': '自发自签'},
    {'code': 'return', 'bit': 16, 'label': '退件 / 拦截 / 退改'},
    {'code': 'default', 'bit': 2, 'label': '默认重量待核'},
    {'code': 'invalid', 'bit': 1 | 4, 'label': '计费要素缺失 / 缺目的地'},
]
SCENE_TAGS = {'product': ('拦截', '退改'), 'policy': ('自发自签', '首中心退', '首中心后退')}

FLAGS_SQL = '''(CASE WHEN status <> '计费成功' OR coalesce(fee, 0) <= 0 THEN 1 ELSE 0 END)
 | (CASE WHEN w_site <= 0 AND w_pack <= 0 AND w_center <= 0 THEN 2 ELSE 0 END)
 | (CASE WHEN province = '' THEN 4 ELSE 0 END)
 | (CASE WHEN policy_op LIKE '%自发自签%' THEN 8 ELSE 0 END)
 | (CASE WHEN policy_op LIKE '%首中心%退%' OR product LIKE '%拦截%' OR product LIKE '%退改%' THEN 16 ELSE 0 END)
 | (CASE WHEN abs(coalesce(fee, 0) - coalesce(dispatch, 0) - coalesce(transit, 0) - coalesce(waybill_fee, 0)) > 0.01 THEN 32 ELSE 0 END)'''

DEFAULT_BANDS = [{'lo': 0, 'hi': .5}, {'lo': .5, 'hi': 1}, {'lo': 1, 'hi': 2}, {'lo': 2, 'hi': 3}, {'lo': 3, 'hi': 5}, {'lo': 5, 'hi': 9999}]
DEFAULT_CONFIG = {
    'conflictRule': 'replace',
    'bands': DEFAULT_BANDS,
    'bandPresets': [],
    'groups': [],
    'subsidyPolicies': [{'type': '一客一价', 'perTicket': 0.2, 'start': None, 'end': None}],
    'deductRebate': False,
    'excludeRules': DEFAULT_EXCLUDE,
    'dateBasis': 'send',
    'adjustments': [],
}


def finite(v, name):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(name + '必须是数字')
    if not math.isfinite(x):
        raise ValueError(name + '必须是有限数字')
    return x


def check_date(v, name):
    if v in (None, ''):
        return None
    try:
        return dt.date.fromisoformat(str(v)).isoformat()
    except ValueError:
        raise ValueError(name + '日期格式应为 YYYY-MM-DD')


def bands(value):
    if not value:
        raise ValueError('至少保留一个重量段')
    out = sorted(({'lo': finite(b['lo'], '重量段下限'), 'hi': finite(b['hi'], '重量段上限')} for b in value), key=lambda b: b['lo'])
    prev = None
    for b in out:
        if b['lo'] < 0 or b['hi'] <= b['lo']:
            raise ValueError('重量段必须大于等于 0，且上限大于下限')
        if prev is not None and b['lo'] < prev:
            raise ValueError('重量段不能重叠')
        prev = b['hi']
    return out


def band_case(bs):
    """Lower bound exclusive, upper bound inclusive. Literals are validated floats, never user text."""
    parts = ' '.join('WHEN weight > %r AND weight <= %r THEN %d' % (b['lo'], b['hi'], i) for i, b in enumerate(bs))
    return 'CASE ' + parts + ' ELSE -1 END'


def policies(value):
    out = []
    for p in value or []:
        t = str(p.get('type', '')).strip()
        if not t:
            raise ValueError('补贴政策必须填写客户补贴类型')
        amt = finite(p.get('perTicket', 0), '每票补贴')
        if amt < 0:
            raise ValueError('每票补贴不能为负数')
        start, end = check_date(p.get('start'), '补贴开始'), check_date(p.get('end'), '补贴结束')
        if start and end and start > end:
            raise ValueError('补贴结束日期不能早于开始日期')
        out.append({'type': t, 'perTicket': amt, 'start': start, 'end': end})
    return out


def adjustments(value):
    out, ids = [], set()
    for x in value or []:
        a = {'id': str(x.get('id', '')).strip(), 'name': str(x.get('name', '')).strip(), 'amount': finite(x.get('amount', 0), '额外成本金额'),
             'sign': int(finite(x.get('sign', 1), '额外成本方向')), 'enabled': x.get('enabled', True) is not False}
        if not a['id'] or a['id'] in ids:
            raise ValueError('额外成本项标识为空或重复')
        if not a['name'] or a['amount'] < 0 or a['sign'] not in (1, -1):
            raise ValueError('额外成本须有名称、非负金额，方向为增加或减少')
        ids.add(a['id'])
        out.append(a)
    return out


def adjustment_total(adj):
    """Net extra cost per ticket (yuan); rebates are negative."""
    return sum(a['sign'] * a['amount'] for a in adj if a['enabled'])


def subsidy_sql(ps, target=None):
    """Per-ticket subsidy amount. Returns (sql, params); the first matching policy wins.
    With target set, every ticket is priced as if it belonged to that subsidy type ('' = no subsidy)."""
    if target is not None:
        ps = [p for p in ps if p['type'] == target]
    if not ps:
        return '0', []
    sql, args = 'CASE', []
    for p in ps:
        if target is None:
            cond = 'subsidy = ?'
            args.append(p['type'])
        else:
            cond = 'TRUE'
        if p['start']:
            cond += ' AND coalesce(send_day, day) >= CAST(? AS DATE)'
            args.append(p['start'])
        if p['end']:
            cond += ' AND coalesce(send_day, day) <= CAST(? AS DATE)'
            args.append(p['end'])
        sql += ' WHEN %s THEN %r' % (cond, p['perTicket'])
    return sql + ' ELSE 0 END', args


def exclude_mask(codes):
    unknown = set(codes) - {r['code'] for r in RULES}
    if unknown:
        raise ValueError('未知异常规则：' + '、'.join(sorted(unknown)))
    return sum(BITS.get(c, 0) for c in set(codes))


LEVEL_KEYS = {
    'province': 'province',
    'city': "province || '/' || city",
    'district': "province || '/' || city || '/' || district",
}


def member_sql(members, args):
    """Match any member written as 省, 省/市 or 省/市/区."""
    by_depth = {1: [], 2: [], 3: []}
    for m in members:
        m = str(m).strip().strip('/')
        if m:
            by_depth[min(3, m.count('/') + 1)].append(m)
    clauses = []
    for depth, key in ((3, LEVEL_KEYS['district']), (2, LEVEL_KEYS['city']), (1, LEVEL_KEYS['province'])):
        if by_depth[depth]:
            clauses.append('(%s) IN (%s)' % (key, ','.join('?' * len(by_depth[depth]))))
            args.extend(by_depth[depth])
    return '(' + ' OR '.join(clauses) + ')' if clauses else 'FALSE'


def flow_sql(flows):
    """Returns (flow_expr, flow_args, where_clauses, where_args)."""
    flows = flows or {}
    level = flows.get('level', 'province')
    if level not in LEVEL_KEYS:
        raise ValueError('流向层级只能是省、市或区')
    expr_args, where, where_args = [], [], []
    names = set()
    cases = []
    # More specific members win: a 市-level group overrides a 省-level group for that city.
    ranked = []
    for g in flows.get('groups') or []:
        name = str(g.get('name', '')).strip()
        if not name or name in names:
            raise ValueError('流向组名称不能为空或重复')
        names.add(name)
        if not g.get('members'):
            raise ValueError('流向组“%s”至少需要一个成员' % name)
        for m in g['members']:
            ranked.append((str(m).count('/'), m, name))
    for _, m, name in sorted(ranked, key=lambda x: -x[0]):
        cases.append('WHEN %s THEN ?' % member_sql([m], expr_args))
        expr_args.append(name)
    expr = 'CASE ' + ' '.join(cases) + ' ELSE ' + LEVEL_KEYS[level] + ' END' if cases else LEVEL_KEYS[level]
    if flows.get('only'):
        where.append(member_sql(flows['only'], where_args))
    if flows.get('drop'):
        where.append('NOT ' + member_sql(flows['drop'], where_args))
    return expr, expr_args, where, where_args


def date_col(f):
    basis = (f or {}).get('dateBasis') or 'send'
    if basis not in ('send', 'handover'):
        raise ValueError('日期口径只能是寄件日期或交件日期')
    return 'send_day' if basis == 'send' else 'day'


def filter_sql(f):
    """Common row filters (excluding anomaly rules). Returns (clauses, args)."""
    f = f or {}
    where, args = ['state = 1'], []
    start, end = check_date(f.get('start'), '开始'), check_date(f.get('end'), '结束')
    if start and end and start > end:
        raise ValueError('结束日期不能早于开始日期')
    col = date_col(f)
    if start:
        where.append(col + ' >= CAST(? AS DATE)')
        args.append(start)
    if end:
        where.append(col + ' <= CAST(? AS DATE)')
        args.append(end)
    for key, col in (('customers', 'customer'), ('accounts', 'account'), ('subsidies', 'subsidy'), ('packs', 'pack'), ('shifts', 'shift'), ('products', 'product_norm')):
        vals = f.get(key)
        if vals:
            where.append('%s IN (%s)' % (col, ','.join('?' * len(vals))))
            args.extend('' if v in (None, '__empty__') else str(v) for v in vals)
    from .regions import predicate
    extra, ea = predicate(f)
    return where + extra, args + ea


def outlier_opts(f):
    o = (f or {}).get('outlier') or {}
    lo, hi = finite(o.get('lowPct', 1), '离群下分位'), finite(o.get('highPct', 99), '离群上分位')
    if not 0 <= lo < hi <= 100:
        raise ValueError('离群分位须满足 0 ≤ 下分位 < 上分位 ≤ 100')
    return bool(o.get('enabled')), lo / 100, hi / 100
