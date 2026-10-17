#!/usr/bin/env python3
"""目标 Mac 发布事务：运行本脚本才会安装依赖、切换、重启或回滚。"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request

LABEL='uk.wuxiaoxia88.cost-studio'


def command(*args): subprocess.run([str(x) for x in args],check=True)


def select(current,target):
    tmp=current.with_name('.current-next');tmp.unlink(missing_ok=True);tmp.symlink_to(target);os.replace(tmp,current)


def wait_health(expected,data_dir):
    identity=hashlib.sha256(str((data_dir/'v2').resolve()).encode()).hexdigest()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(90):
        try:
            with opener.open('http://127.0.0.1:8888/api/health',timeout=4) as response: health=json.load(response)
            with opener.open('http://127.0.0.1:8888/api/identity',timeout=4) as response: ident=json.load(response)
            if health.get('ok') and health.get('version')==expected and ident.get('data')==identity:return health
        except Exception: pass
        time.sleep(2)
    raise RuntimeError('新服务版本/数据目录健康检查失败')


def wait_port_free():
    for _ in range(40):
        result=subprocess.run(['/usr/sbin/lsof','-nP','-iTCP:8888','-sTCP:LISTEN','-t'],capture_output=True)
        if result.returncode==1 and not result.stdout:return
        time.sleep(.25)
    raise RuntimeError('8888 仍有监听进程，停止恢复数据库以避免覆盖正在使用的文件')


def main(tag):
    if not re.fullmatch(r'v\d+\.\d+\.\d+',tag):raise ValueError('版本标签无效')
    home=Path.home();base=home/'cost-studio';data=home/'cost-studio-data';release=base/'releases'/tag;current=base/'current'
    main_plist=Path('/Library/LaunchDaemons')/(LABEL+'.plist')
    if not main_plist.exists():raise RuntimeError('首次发布：请先按运维说明完成数据迁移和 LaunchDaemon 安装，本脚本用于后续已安装服务的升级')
    if not current.is_symlink():raise RuntimeError('缺少 current 旧版本链接，不能自动回滚')
    previous=current.resolve()
    if previous==release:raise RuntimeError('目标标签已经启用，无需重复发布')
    uv=sorted((home/'.hermes/tools').glob('uv-*/uv'))
    if not uv:uv=sorted((home/'.hermes/tools').glob('uv-*'))
    uv=[p for p in uv if p.is_file() and os.access(p,os.X_OK)]
    if not uv:raise RuntimeError('未发现 uv 可执行文件')
    if not (base/'venv/bin/python').exists():command(uv[-1],'venv','--python','3.12',base/'venv')
    command(uv[-1],'pip','sync','--python',base/'venv/bin/python',release/'requirements-server.txt')
    command(base/'venv/bin/python','-c',"import platform,sys,duckdb,python_calamine,importlib.metadata as m; assert platform.machine()=='x86_64' and sys.version_info[:2]==(3,12); assert duckdb.__version__=='1.5.6' and m.version('python-calamine')=='0.8.3'")
    activate(base,data,release,main_plist,Path('/Library/LaunchDaemons')/(LABEL+'.watchdog.plist'))


def activate(base,data,release,main_plist,guard_plist):
    current=base/'current';previous=current.resolve()
    command('sudo','-n','true')
    # 禁用看门狗，避免迁移或回滚期间被它启动第二个主进程。
    command('sudo','-n','launchctl','bootout','system/'+LABEL+'.watchdog')
    stopped=False;switched=False;backup=None
    try:
        command('sudo','-n','launchctl','bootout','system/'+LABEL);stopped=True
        wait_port_free()
        backup=data/'release-backups'/(release.name+'-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(parents=True)
        # 数据库与 WAL 必须成对保留。原件无变动，不复制一份到每次升级备份中。
        for name in ('warehouse.duckdb','warehouse.duckdb.wal'):
            src=data/'v2'/name
            if src.exists():shutil.copy2(src,backup/name)
        if not (backup/'warehouse.duckdb').exists():raise RuntimeError('仓库备份缺失')
        select(current,release);switched=True
        command('sudo','-n','launchctl','bootstrap','system',main_plist)
        health=wait_health(release.name[1:],data)
        shutil.copy2(release/'docs/部署与运维说明.md',base/'部署与运维说明.md')
        print(json.dumps({'ok':True,'tag':release.name,'previous':str(previous),'backup':str(backup),'health':health},ensure_ascii=False))
    except Exception:
        if switched:
            subprocess.run(['sudo','-n','launchctl','bootout','system/'+LABEL],check=False)
            wait_port_free()
            # 恢复迁移前 DB/WAL，避免旧代码误用已迁移业务日。
            for name in ('warehouse.duckdb','warehouse.duckdb.wal'):
                target=data/'v2'/name
                target.unlink(missing_ok=True)
                if (backup/name).exists():shutil.copy2(backup/name,target)
            select(current,previous)
        if stopped:command('sudo','-n','launchctl','bootstrap','system',main_plist)
        raise
    finally:
        command('sudo','-n','launchctl','bootstrap','system',guard_plist)


if __name__=='__main__':main(sys.argv[1])
