"""交件业务日：本日 12:00（含）到次日 12:00（不含），寄件日期不变。"""
import json

MARKER = 'handover_business_day_v1'
LABEL = '交件（业务日 12:00–次日 12:00）'


def timestamp_sql(col):
    # 原件既可能是 Excel 序号，也可能是 ISO/中文斜线日期；无效值保持 NULL。
    n = f'TRY_CAST(trim({col}) AS DOUBLE)'
    return (f"CASE WHEN regexp_full_match(trim({col}), '\\d+(\\.\\d+)?') "
            f"THEN CASE WHEN {n} BETWEEN 0 AND 2958465 THEN TIMESTAMP '1899-12-30' + "
            f"to_microseconds(CAST(round({n} * 86400000000) AS BIGINT)) END "
            f"ELSE TRY_CAST(replace(trim({col}), '/', '-') AS TIMESTAMP) END")


def day_sql(col):
    return f'CAST(({timestamp_sql(col)}) - INTERVAL 12 HOURS AS DATE)'


def fill(c, ids_sql=None):
    scope = f' AND ship.id IN ({ids_sql})' if ids_sql else ''
    c.execute('UPDATE ship SET day=' + day_sql('r."交件时间"') + ' FROM raw r WHERE r.id=ship.id' + scope)


def migrate(wh):
    c = wh.cursor()
    c.execute('BEGIN')
    try:
        if not c.execute('SELECT 1 FROM meta WHERE key=?', [MARKER]).fetchone():
            total = c.execute('SELECT count(*) FROM ship').fetchone()[0]
            if total:
                if c.execute('SELECT count(*) FROM ship s LEFT JOIN raw r ON s.id=r.id WHERE r.id IS NULL').fetchone()[0]:
                    raise ValueError('业务日迁移缺少原始记录，未修改日期，请从完整备份恢复')
                changed = c.execute('SELECT count(*) FROM ship s JOIN raw r USING(id) WHERE s.day IS DISTINCT FROM ' + day_sql('r."交件时间"')).fetchone()[0]
                fill(c)
                # 导入清单中的旧统计同时更新；撤销前镜像在恢复时按 raw 重新计算。
                spans = {b: (str(lo) if lo else None, str(hi) if hi else None) for b, lo, hi in
                         c.execute('SELECT batch,min(day),max(day) FROM ship GROUP BY batch').fetchall()}
                for table, key in [('batches', 'id'), ('import_files', 'batch_id')]:
                    for bid, raw in c.execute(f'SELECT {key},stats FROM {table}').fetchall():
                        if bid in spans:
                            stats = json.loads(raw)
                            stats.update(zip(('minDay', 'maxDay'), spans[bid]))
                            c.execute(f'UPDATE {table} SET stats=? WHERE {key}=?', [json.dumps(stats, ensure_ascii=False), bid])
                wh.bump(c)
                wh.audit(c, '交件业务日迁移', {'rows': total, 'changed': changed, 'boundary': '12:00', 'sendDayUnchanged': True})
            c.execute('INSERT INTO meta VALUES (?,?)', [MARKER, '12:00'])
        c.execute('COMMIT')
    except Exception:
        c.execute('ROLLBACK')
        raise
    finally:
        c.close()
