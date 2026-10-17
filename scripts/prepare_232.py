#!/usr/bin/env python3
"""生成 launchd / Hermes 材料并打印安装命令；不安装、不启动、不创建任务。"""
import argparse
import getpass
from pathlib import Path
import plistlib
import shlex

LABEL='uk.wuxiaoxia88.cost-studio'


def render(home,user,lan,domain):
    base=home/'cost-studio';data=home/'cost-studio-data';logs=data/'logs'
    main={'Label':LABEL,'UserName':user,'WorkingDirectory':str(base/'current'),
          'ProgramArguments':[str(base/'venv/bin/python'),str(base/'current/server.py'),'--no-browser','--port','8888',
                              '--data',str(data),'--host','0.0.0.0'],
          'KeepAlive':True,'RunAtLoad':True,'ThrottleInterval':10,'Umask':63,
          'EnvironmentVariables':{'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','LANG':'en_US.UTF-8','TZ':'Asia/Shanghai'},
          'StandardOutPath':str(logs/'server.log'),'StandardErrorPath':str(logs/'server.err.log')}
    for h in (lan+':8888',domain,'127.0.0.1:8888','localhost:8888'):main['ProgramArguments']+=['--allow-host',h]
    guard={'Label':LABEL+'.watchdog','ProgramArguments':['/usr/bin/python3','-I','/Library/Application Support/CostStudio/watchdog.py'],
           'StartInterval':60,'RunAtLoad':True,'Umask':63,'StandardOutPath':str(logs/'watchdog.log'),'StandardErrorPath':str(logs/'watchdog.err.log')}
    return main,guard


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--home',type=Path,default=Path.home());p.add_argument('--user',default=getpass.getuser())
    p.add_argument('--lan',required=True);p.add_argument('--domain',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    for label,obj in zip((LABEL,LABEL+'.watchdog'),render(a.home,a.user,a.lan,a.domain)):
        (a.output/(label+'.plist')).write_bytes(plistlib.dumps(obj))
    for name,command in [('cost-daily-sync.sh','run'),('cost-daily-backup.sh','backup')]:
        text='#!/bin/bash\nset -euo pipefail\nexport TZ=Asia/Shanghai\nexport DATA_ROOT="$HOME/zto-data"\nexec "$HOME/cost-studio/venv/bin/python" "$HOME/cost-studio/current/scripts/daily_sync.py" '+command+'\n'
        f=a.output/name;f.write_text(text);f.chmod(0o700)
    q=shlex.quote;out=q(str(a.output.resolve()));watch=q(str(a.home/'cost-studio/current/scripts/watchdog.py'))
    print(f'''仅生成材料，以下命令尚未执行。请在目标机运行（首次安装前 current、venv 和数据须准备妥当）：
mkdir -p "$HOME/cost-studio-data/logs" "$HOME/.hermes/scripts"
sudo install -d -m 755 '/Library/Application Support/CostStudio'
sudo install -o root -g wheel -m 644 {watch} '/Library/Application Support/CostStudio/watchdog.py'
sudo install -o root -g wheel -m 644 {out}/{LABEL}.plist /Library/LaunchDaemons/
sudo install -o root -g wheel -m 644 {out}/{LABEL}.watchdog.plist /Library/LaunchDaemons/
sudo launchctl bootstrap system /Library/LaunchDaemons/{LABEL}.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/{LABEL}.watchdog.plist
install -m 700 {out}/cost-daily-sync.sh "$HOME/.hermes/scripts/"
install -m 700 {out}/cost-daily-backup.sh "$HOME/.hermes/scripts/"
# 先确认 Hermes 时区 Asia/Shanghai，核对 cron list 没有同名任务，然后再创建：
HERMES_TIMEZONE=Asia/Shanghai "$HOME/.hermes/hermes-agent/venv/bin/hermes" cron create '0 14 * * *' --name '出港账单每日同步' --script cost-daily-sync.sh --no-agent --deliver local
HERMES_TIMEZONE=Asia/Shanghai "$HOME/.hermes/hermes-agent/venv/bin/hermes" cron create '0 3 * * *' --name '出港成本每日备份' --script cost-daily-backup.sh --no-agent --deliver local
''')


if __name__=='__main__':main()
