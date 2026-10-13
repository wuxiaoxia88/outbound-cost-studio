"""Deterministic synthetic XLSX, no business database needed."""
import argparse, calendar, random, sys
from datetime import date
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import workbook

HEADERS=['运单号','计费状态','交件时间','寄件时间','省','市','区','结算重量(kg)','总费用(元)',
         '派费合计','中转费合计','面单加价(元)','所属客户','电子面单账号','客户补贴类型','政策操作类型',
         '增值类型','揽件网点重量(KG)','集包网点重量(KG)','中心实重(KG)','签收时间','建包类型','退件费']
PROVINCES=['浙江省','江苏省','广东省','四川省','山东省','河南省']

def generate(rows=6000,seed=32):
    rng=random.Random(seed);out=[];today=date.today();month=today.month-1 or 12;year=today.year-(today.month==1)
    months=[(year-(month==1),month-1 or 12),(year,month)]
    for i in range(rows):
        y,m=months[i%2];day=1+(i//2)%calendar.monthrange(y,m)[1];send=f'{y:04}-{m:02}-{day:02} 09:30:00'
        customer=rng.randrange(6);weight=round(rng.choice([.3,.45,.55,.8,.92,.95,.96,.96,1.05,1.05,1.05,1.35,1.8,2.05,2.8,3.5])+rng.random()*.04,2)
        province=PROVINCES[rng.randrange(len(PROVINCES))];dispatch=round(1.0+int(weight>.5)*.2+int(weight>1)*.5+int(weight>2)*.3,3)
        transit=round(.25+weight*.3+customer*.025+(m==month)*.06,3);mf=.08;fee=round(dispatch+transit+mf,3)
        special='拦截,退改' if i%97==0 else '无增值';policy='自发自签' if i%173==0 else '';source=0 if i%211==0 else weight
        out.append([f'DEMO{i+1:08}','计费成功',send,send,province,'示例市','示例区',weight,fee,dispatch,transit,mf,
                    '客户'+chr(65+customer),'DEMO_ACCOUNT_'+str(customer+1),'一客一价' if customer==1 else '',policy,special,
                    source,0,0,send,'网点自建包' if i%3 else '未建包',.6 if special!='无增值' else 0])
    return out

if __name__=='__main__':
    ap=argparse.ArgumentParser(description='生成完全虚构的两个月出港账单');ap.add_argument('--output',default='outputs/演示出港账单.xlsx');ap.add_argument('--rows',type=int,default=6000);a=ap.parse_args()
    if not 100<=a.rows<=100000:ap.error('rows 须为 100–100000')
    p=Path(a.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(workbook(HEADERS,generate(a.rows)));print(p.resolve())
