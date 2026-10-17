"""Internal exports are immutable copies of a single displayed analysis response."""
import copy
from collections import OrderedDict
from datetime import datetime
from threading import RLock
import uuid

LOCK=RLock()
MODULES={'overview','insights','zones','history','dist','heat','aftersales','compare','ranking'}


def remember(wh, result):
    result={**result,'snapshotId':uuid.uuid4().hex,'generatedAt':datetime.now().astimezone().isoformat(timespec='seconds')}
    with LOCK:
        if not hasattr(wh,'_report_snapshots'):wh._report_snapshots=OrderedDict()
        wh._report_snapshots[result['snapshotId']]=copy.deepcopy(result)
        while len(wh._report_snapshots)>8:wh._report_snapshots.popitem(last=False)
    return result


def sheets(wh, req):
    with LOCK:
        r=getattr(wh,'_report_snapshots',{}).get(req.get('snapshotId'))
        if r is None:raise ValueError('分析报告已过期，请刷新分析后重新生成')
    mods=req.get('modules')
    if not isinstance(mods,list) or not mods or set(mods)-MODULES:raise ValueError('请选择有效的报告模块')
    out=[];o=r['overview'];scope=r['scope']
    if 'overview' in mods:
        out.append(('概览',['指标','数值','单位'],[['正常件票数',o['n'],'票'],['日均',o['daily'],'票/天'],['均重',o['avgW'],'kg'],['单票实际成本',o['avg'],'元/票'],['账单单票',o['avgSystem'],'元/票'],['网点同类件',o['eq'],'元/票'],['比网点贵便宜',o['gap'],'元/票'],['退改率',r['aftersales']['rate'],'比例'],['刚超档',o['critical']['n'],'票']]))
    if 'insights' in mods:
        out.append(('要点',['指标','数值','单位'],[['主要重量', '、'.join(b['label'] for b in r['baseBands'] if b['cls']=='main'),''],['刚超档多付',o['critical']['save'],'元'],['同格账单价差',o['gap'],'元/票'],['已隔离特殊件',r['excluded']['n'],'票']]))
    if 'zones' in mods:
        out.append(('区域分布',['区域','省份','参与测算','票数','占比','均重kg','账单单票元','网点单票元']+[('%g–%gkg'%(i*.5,(i+1)*.5)) if i<11 else '5.5kg以上' for i in range(12)],
                    [[z['name'],'、'.join(z['provinces']),'是' if z['included'] else '否',z['n'],z['share'],z['avgW'],z['avgSystem'],z['network']['avgSystem']]+z['distribution'] for z in r['zones']['rows']]))
    if 'history' in mods:
        out.append(('历史对比',['月份','发件量','日均','均重kg','账单单票元','刚超档占比','主要段占比'],[[m['month'],m['n'],m['daily'],m['avgW'],m['avg'],m['criticalShare'],m['mainShare']] for m in r['history']['months']]))
    if 'dist' in mods:
        out.append(('重量分布与成本',['重量上限kg','票量','占比','均重kg','账单单票元','实际单票元','网点账单单票元'],[[p['u']/10,p['n'],p['n']/o['n'] if o['n'] else 0,p['avgW'],p['avgSystem'],p['avg'],net['avgSystem']] for p,net in zip(r['distribution'],r['networkDistribution'])]))
    if 'heat' in mods:
        h=r['heat'];rows=h['allRows'] if req.get('flowRows')=='all' or r['level']=='zone' else h['allRows'][:12]+([h['topRest']] if h['topRest'] else [])
        out.append(('流向重量成本',['流向']+[b['label']+' 实际单票元' for b in r['bands']]+[b['label']+' 票数' for b in r['bands']]+[b['label']+' 均重kg' for b in r['bands']],
                    [[row['flow']]+[c['avg'] for c in row['cells']]+[c['n'] for c in row['cells']]+[c['avgW'] for c in row['cells']] for row in rows+[h['total']]]))
    if 'aftersales' in mods:
        a=r['aftersales'];out.append(('退改附加成本',['项目','数值','单位'],[['全部票数',a['n'],'票'],['退改票数',a['rn'],'票'],['退改率',a['rate'],'比例'],['退改件账单',a['rfee'],'元'],['单独退件费',a['rbill'],'元'],['客服模拟',a['service'],'元'],['附加合计',a['total'],'元'],['每正常票分摊',a['per'],'元/票']]))
    for key,title in [('compare','客户对比'),('ranking','客户排行')]:
        if key in mods:
            out.append((title,['客户','正常件票数','日均','均重kg','账单单票元','网点同结构元','账单价差元','刚超档票数','退改率'],[[x['name'],x['total']['n'],x.get('daily'),x['avgW'],x['avg'],x['eqSystem'],x['gap'],x['critical']['n'],x['returnRate']] for x in r.get('comparison' if key=='compare' else 'ranking',[])]))
    out.append(('口径说明',['项目','内容'],[['用途','内部分析，含客户名称及成本信息；不得作为客户报价表'],['客户','全部客户' if r['mode']=='all' else '、'.join(r['customers'])],['日期口径',scope['dateBasis']],['开始',scope.get('start')],['结束',scope.get('end')],['排除区域','、'.join(scope['excludeZones']) or '无'],['数据版本',r['version']],['分析快照',r['snapshotId']],['生成时间',r['generatedAt']],['已开模拟项',r['simulation']['count']],['金额口径','元/票或元，按账单原精度计算；税口径未确认'],['特殊件','按当前剔除规则隔离；退改附加成本单列'],['零星月份未列出',r['history'].get('hiddenMonthCount',0)],['区域分布','保留排除前票数用于对照，参与测算列标注是否纳入；其余模块按剩余流向计算']]))
    return out
