#!/usr/bin/env python3
"""目标 Mac 发布事务：运行本脚本才会安装依赖、切换、重启或回滚。"""
import hashlib
import json
import os
import plistlib
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
    import fcntl  # macOS release entry point; other platforms can inspect the module.
    if not re.fullmatch(r'v\d+\.\d+\.\d+',tag):raise ValueError('版本标签无效')
    home=Path.home();base=home/'cost-studio';data=home/'cost-studio-data';release=base/'releases'/tag;current=base/'current'
    main_plist=Path('/Library/LaunchDaemons')/(LABEL+'.plist')
    if not main_plist.exists():raise RuntimeError('首次发布：请先按运维说明完成数据迁移和 LaunchDaemon 安装，本脚本用于后续已安装服务的升级')
    if not current.is_symlink():raise RuntimeError('缺少 current 旧版本链接，不能自动回滚')
    previous=current.resolve()
    if previous==release:raise RuntimeError('目标标签已经启用，无需重复发布')
    # Prevent the daily job from writing during the release transaction.
    with (data/'sync.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('每日同步正在运行，请完成后再发布') from None
        sync_dependencies(base, previous, release, home)
        activate(base,data,release,main_plist,Path('/Library/LaunchDaemons')/(LABEL+'.watchdog.plist'))


def sync_dependencies(base,previous,release,home):
    python=base/'venv/bin/python';old=previous/'requirements-server.txt';new=release/'requirements-server.txt'
    unchanged=python.exists() and old.exists() and old.read_bytes()==new.read_bytes()
    if not unchanged:
        uv=sorted((home/'.hermes/tools').glob('uv-*/uv'))+sorted((home/'.hermes/tools').glob('uv-*'))
        uv=[p for p in uv if p.is_file() and os.access(p,os.X_OK)]
        if not uv:raise RuntimeError('未发现 uv 可执行文件')
        # A dependency change must be staged in the local cache before release.
        # No TLS/network dependency during activation. Failure occurs before switch.
        if not python.exists():command(uv[-1],'venv','--offline','--python','3.12',base/'venv')
        command(uv[-1],'pip','sync','--offline','--python',python,new)
    command(python,'-c',"import platform,sys,duckdb,python_calamine,importlib.metadata as m; assert platform.machine()=='x86_64' and sys.version_info[:2]==(3,12); assert duckdb.__version__=='1.5.6' and m.version('python-calamine')=='0.8.3'")
    return unchanged


def unload(label,timeout=30):
    command('sudo','-n','launchctl','bootout','system/'+label)
    deadline=time.monotonic()+timeout
    while True:
        result=subprocess.run(['sudo','-n','launchctl','print','system/'+label],capture_output=True,text=True)
        if result.returncode and ('Could not find service' in result.stderr or 'Could not find service' in result.stdout):return
        if time.monotonic()>=deadline:raise RuntimeError(label+' 卸载未在30秒内完成，未执行 bootstrap')
        time.sleep(.25)


def online_backup(data,release):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8888/api/bootstrap',timeout=10) as response:token=json.load(response)['token']
    req=urllib.request.Request('http://127.0.0.1:8888/api/v2/backup',data=b'{}',headers={'Content-Type':'application/json','X-App-Token':token})
    with opener.open(req,timeout=600) as response:info=json.load(response)
    if not info.get('ok') or not re.fullmatch(r'\d{8}\.duckdb',info.get('file','')):raise RuntimeError('服务内一致性备份失败')
    dest=data/'release-backups'/(release.name+'-'+time.strftime('%Y%m%d-%H%M%S'));dest.mkdir(parents=True)
    target=dest/'warehouse.duckdb';shutil.copy2(data/'backups'/info['file'],target)
    digest=hashlib.sha256()
    with target.open('rb') as f:
        for chunk in iter(lambda:f.read(8<<20),b''):digest.update(chunk)
    if digest.hexdigest()!=info['sha256'] or target.stat().st_size!=info['bytes']:raise RuntimeError('发布备份 SHA-256/字节数不一致')
    (dest/'backup.json').write_text(json.dumps(info,ensure_ascii=False,indent=2))
    return dest


def plist_changed(installed,candidate):
    return candidate.exists() and plistlib.loads(candidate.read_bytes())!=plistlib.loads(installed.read_bytes())


def activate(base,data,release,main_plist,guard_plist):
    current=base/'current';previous=current.resolve()
    command('sudo','-n','true')
    candidate=release/'deploy'/(LABEL+'.plist')
    changed=plist_changed(main_plist,candidate)
    old_plist=main_plist.read_bytes()
    backup=online_backup(data,release)
    (backup/'service.plist').write_bytes(old_plist)
    switched=False;unloaded=False;guard_stopped=False
    try:
        if changed:
            unload(LABEL);unloaded=True;wait_port_free()
            command('sudo','-n','install','-o','root','-g','wheel','-m','644',candidate,main_plist)
        select(current,release);switched=True
        if changed:command('sudo','-n','launchctl','bootstrap','system',main_plist);unloaded=False
        else:command('sudo','-n','launchctl','kickstart','-k','system/'+LABEL)
        health=wait_health(release.name[1:],data)
        shutil.copy2(release/'docs/部署与运维说明.md',base/'部署与运维说明.md')
        print(json.dumps({'ok':True,'tag':release.name,'previous':str(previous),'backup':str(backup),'plistChanged':changed,'health':health},ensure_ascii=False))
    except Exception:
        if switched:
            # Rollback is the only unchanged-plist path that unloads: never replace
            # a live database. Preserve the failed DB as evidence before restoring.
            unload(LABEL+'.watchdog');guard_stopped=True
            if not unloaded:unload(LABEL);unloaded=True
            wait_port_free()
            failed=backup/'failed-state';failed.mkdir()
            for name in ('warehouse.duckdb','warehouse.duckdb.wal'):
                target=data/'v2'/name
                if target.exists():shutil.copy2(target,failed/name)
                target.unlink(missing_ok=True)
                if (backup/name).exists():shutil.copy2(backup/name,target)
            select(current,previous)
        if changed and unloaded:command('sudo','-n','install','-o','root','-g','wheel','-m','644',backup/'service.plist',main_plist)
        if unloaded:command('sudo','-n','launchctl','bootstrap','system',main_plist)
        raise
    finally:
        if guard_stopped:command('sudo','-n','launchctl','bootstrap','system',guard_plist)


if __name__=='__main__':main(sys.argv[1])
