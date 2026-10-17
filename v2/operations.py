"""服务进程内的健康检查和一致性备份；同步日志与业务库分开。"""
import datetime as dt
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from .version import VERSION


def health(wh):
    try:
        sync = json.loads((wh.home.parent / 'sync_latest.json').read_text())
        sync = {k: sync[k] for k in ('runId', 'started', 'finished', 'status', 'summary') if k in sync}
    except (OSError, ValueError, TypeError):
        sync = None
    with wh.snapshot() as (c, version):
        total, active, pending = c.execute('SELECT count(*),count(*) FILTER (WHERE state=1),count(*) FILTER (WHERE state=0) FROM ship').fetchone()
    return {'ok': True, 'version': VERSION, 'dataVersion': version, 'lastSync': sync,
            'records': {'total': total, 'active': active, 'pending': pending}}


def backup(wh, today=None):
    """同一天原子替换；成功后保留最新 3 个日期的数据库备份。"""
    date = (today or dt.date.today()).strftime('%Y%m%d')
    folder = wh.home.parent / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (date + '.duckdb')
    with wh.wlock:
        c = wh.cursor()
        try:
            # 写锁覆盖 CHECKPOINT 与完整复制，所有仓库写入均使用同一锁。
            c.execute('CHECKPOINT')
            version = wh.version(c)
            digest = hashlib.sha256()
            fd, name = tempfile.mkstemp(prefix='.backup-', dir=folder)
            try:
                with os.fdopen(fd, 'wb') as out, wh.path.open('rb') as src:
                    for part in iter(lambda: src.read(8 << 20), b''):
                        out.write(part); digest.update(part)
                    out.flush(); os.fsync(out.fileno())
                os.replace(name, target)
            finally:
                Path(name).unlink(missing_ok=True)
            existing = sorted(p for p in folder.iterdir() if re.fullmatch(r'\d{8}\.duckdb', p.name))
            for p in existing[:-3]:
                p.unlink()
            return {'ok': True, 'file': target.name, 'bytes': target.stat().st_size,
                    'sha256': digest.hexdigest(), 'dataVersion': version, 'retained': [p.name for p in existing[-3:]]}
        finally:
            c.close()
