#!/usr/bin/env python3
"""由 launchd 每分钟运行；只读健康检查，连续 3 次失败后重启，冷却 300 秒。"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

LABEL='uk.wuxiaoxia88.cost-studio'


def tick(state, healthy, stamp, restart):
    state=dict(state)
    state['failures']=0 if healthy else state.get('failures',0)+1
    if state['failures']>=3 and stamp-state.get('lastRestart',0)>=300:
        # 即便 kickstart 失败也进入冷却，防止每分钟重复执行。
        state['lastRestart']=stamp
        state['restartOK']=bool(restart())
        state['failures']=0
    return state


def main():
    import fcntl
    ap=argparse.ArgumentParser();ap.add_argument('--state',default='/var/run/cost-studio-watchdog.json')
    ap.add_argument('--url',default='http://127.0.0.1:8888/api/health');args=ap.parse_args()
    path=Path(args.state);path.parent.mkdir(parents=True,exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        try:state=json.loads(path.read_text())
        except (OSError,ValueError):state={}
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(args.url,timeout=8) as response:health=json.load(response)
            healthy=health.get('ok') is True and str(health.get('version','')).startswith('3.') and isinstance(health.get('dataVersion'),int)
        except Exception:healthy=False
        def restart():
            r=subprocess.run(['/bin/launchctl','kickstart','-k','system/'+LABEL],capture_output=True,timeout=20)
            print('成本服务健康检查连续失败，已请求重启；结果 '+str(r.returncode),flush=True)
            return r.returncode==0
        state=tick(state,healthy,time.time(),restart)
        tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(state));os.chmod(tmp,0o600);os.replace(tmp,path)


if __name__=='__main__':main()
