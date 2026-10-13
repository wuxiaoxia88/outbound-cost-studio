"""V3 complete-grid quotation. Decimal prices; immutable, version-checked saves."""
import json
import uuid
from decimal import Decimal, ROUND_HALF_UP
from . import analysis, rules, simulation
from .analytics import VersionConflict
from .store import now

D = lambda x: Decimal(str(x))
BANDS = [{'lo': lo, 'hi': hi, 'label': label} for lo, hi, label in (
    (0,.5,'≤0.5kg'),(.5,1,'0.5–1kg'),(1,1.5,'1–1.5kg'),(1.5,2,'1.5–2kg'),
    (2,3,'2–3kg'),(3,5,'3–5kg'),(5,None,'5kg以上'))]

def money(value):
    return float(D(value).quantize(D('.1'), rounding=ROUND_HALF_UP))

def preview(wh, req):
    with wh.snapshot() as (c, version):
        return _preview(wh, req, c, version)

def _preview(wh, req, c, version):
    s = analysis.settings(wh, req, c)
    customers = req.get('customers', 'all')
    if customers != 'all':
        if not isinstance(customers, list) or not 1 <= len(customers) <= 8 or any(not isinstance(x,str) for x in customers):
            raise ValueError('请选择 1–8 个客户或全部客户')
        customers = list(dict.fromkeys(customers))
    basis = req.get('basis', 'selection')
    if basis not in ('selection','network'):
        raise ValueError('成本依据须为当前选择或网点整体')
    target = str(req.get('target','')).strip()[:200]
    own_customer = basis == 'selection' and customers != 'all' and len(customers) == 1 and target == customers[0]
    subsidy_mode = req.get('subsidyMode', 'target')
    if subsidy_mode not in ('existing', 'target'):
        raise ValueError('补贴方式须为沿用现有补贴或指定目标补贴')
    if subsidy_mode == 'existing' and not own_customer:
        raise ValueError('沿用现有补贴仅适用于以该客户自身数据报价')
    subsidy = '' if subsidy_mode == 'existing' else str(req.get('subsidyType','')).strip()
    if subsidy and subsidy not in {p['type'] for p in s['policies']}:
        raise ValueError('目标补贴类型未配置，请刷新设置')
    mode = req.get('mode','percent')
    if mode not in ('percent','fixed'):
        raise ValueError('加价方式只能是百分比或固定金额')
    value = D(rules.finite(req.get('value',20), '加价数值'))
    if value < 0 or value > 100000:
        raise ValueError('加价数值应为 0–100000')
    only=req.get('flows')
    if only is not None and (not isinstance(only,list) or not only or any(not isinstance(p,str) or p.count('/')>1 for p in only)):
        raise ValueError('请提供省份或省/市流向列表')
    city_scope=bool(only and any('/' in p for p in only))
    if city_scope and any('/' not in p for p in only):raise ValueError('省和市报价范围不能混用')
    overrides = req.get('overrides') or {}
    if not isinstance(overrides,dict):
        raise ValueError('人工改价格式不正确')
    ctx=analysis.context(wh,req,c,version)
    sm=simulation.state(wh,req,c);K=simulation.constants(ctx,sm)
    ret=simulation.returns(ctx,list(ctx['returns']) if customers=='all' else customers,sm)
    include_addon=req.get('includeAddon',False)
    if not isinstance(include_addon,bool):raise ValueError('退改附加成本须为开关')
    if include_addon and ret['per'] is None:raise ValueError('当前选择没有正常件，无法摊入退改附加成本')
    addon=ret['per'] if include_addon else 0
    # The quote checkbox alone controls aftersales allocation; the analysis addon
    # switch is deliberately omitted so a quote never allocates it twice.
    expr=simulation.sql(sm,K)
    sub,sa=('0',[]) if subsidy_mode=='existing' else rules.subsidy_sql(s['policies'],target=subsidy)

    where, args = rules.filter_sql(s['scope'])
    where.append('(flags & %d)=0' % rules.exclude_mask(s['scope']['excludeRules']))
    bc = 'CASE ' + ' '.join('WHEN weight <= %s THEN %d' % (b['hi'],i) for i,b in enumerate(BANDS) if b['hi']) + ' ELSE 6 END'
    query = '''SELECT customer, province, %s AS city, %s AS band, count(*),
       sum(coalesce(fee,0)-CAST((%s) AS DECIMAL(18,4))+CAST((%s) AS DECIMAL(38,12))+CAST(? AS DECIMAL(38,12)))
       FROM ship WHERE %s GROUP BY 1,2,3,4''' % ('city' if city_scope else "''", bc, sub, expr, ' AND '.join(where))
    own, net, nation = {}, {}, {}
    available=set()
    def add(dest,key,n,fee):
        old=dest.get(key,(0,D(0)));dest[key]=(old[0]+n,old[1]+fee)
    for name,p,city,b,n,fee in c.execute(query,sa+[D(addon)]+args).fetchall():
        fee=D(fee)
        add(net,(p,b),n,fee);add(nation,b,n,fee)
        flow=p+'/'+city if city_scope else p;available.add(flow)
        if basis=='network' or customers=='all' or name in customers:
            add(own,(flow,b),n,fee)
    # Complete province grid; reference rows remain useful even when this client has no traffic there.
    flows=sorted(available,key=lambda p:(-sum(own.get((p,b),(0,0))[0] for b in range(7)),p))
    if only is not None:
        if not isinstance(only,list) or any(p not in flows for p in only):
            raise ValueError('报价流向不在当前样本范围')
        flows=[p for p in flows if p in only]
    counts=[sum(own.get((p,b),(0,0))[0] for p in flows) for b in range(7)]
    n=sum(counts);used=set();rows=[];revenue=pricedCost=D(0);pricedN=edited=loss=0
    for p in flows:
        cells=[]
        for b in range(7):
            key=p+'|'+str(b);sample=own.get((p,b),(0,D(0)));source='own';basisCell=sample
            if basisCell[0]<30:
                basisCell=net.get((p.split('/')[0],b),(0,D(0)));source='province'
            if basisCell[0]<30:
                basisCell=nation.get(b,(0,D(0)));source='nation'
            if basisCell[0]<30:
                cells.append({'key':key,'n':sample[0],'cost':None,'price':None,'source':'none','edited':False,'loss':False});continue
            cost=basisCell[1]/basisCell[0]
            suggested=money(cost*(1+value/100) if mode=='percent' else cost+value)
            manual=key in overrides
            price=suggested
            if manual:
                v=rules.finite(overrides[key],'人工报价')
                if not 0<=v<=1000000:raise ValueError('人工报价应为 0–1000000 元')
                price=money(v);used.add(key)
            # Negative net costs must never produce a negative customer-facing price.
            price=max(0,price)
            isloss=D(price)<cost
            cells.append({'key':key,'n':sample[0],'referenceN':basisCell[0], 'cost':float(cost),'price':price,
                          'suggested':max(0,suggested),'source':source,'edited':manual,'loss':isloss})
            revenue+=D(price)*sample[0];pricedCost+=cost*sample[0];pricedN+=sample[0];edited+=manual;loss+=isloss
        rows.append({'flow':p,'n':sum(x['n'] for x in cells),'cells':cells})
    if set(overrides)-used:
        raise ValueError('部分人工改价格已无有效样本或不在所选流向，请恢复建议价后重试')
    bands=[dict(b,n=counts[i],share=counts[i]/n if n else 0,
                cls='main' if n and counts[i]/n>=s['parameters']['mainShare'] else 'sub' if n and counts[i]/n>=s['parameters']['secondaryShare'] else 'low') for i,b in enumerate(BANDS)]
    return {'schema':'v3','simulation':sm,'includeAddon':include_addon,'addonPer':ret['per'],'aftersales':ret,'version':version,'target':target,'subsidyMode':subsidy_mode,'subsidyType':subsidy,'scope':s['scope'],'settings':s,'bands':bands,'rows':rows,
            'summary':{'n':n,'pricedN':pricedN,'missingN':n-pricedN,'revenue':float(revenue),'profit':float(revenue-pricedCost),
                       'margin':float((revenue-pricedCost)/revenue) if revenue else None,'editedCells':edited,'lossCells':loss},
            'request':{**req,'simulation':sm,'includeAddon':include_addon,'schema':'v3','customers':customers,'target':target,'subsidyMode':subsidy_mode,'subsidyType':subsidy}}

def save(wh, req):
    if req.get('expectedVersion') is None:raise ValueError('保存前请先预览报价')
    with wh.wlock:
        c=wh.cursor();c.execute('BEGIN')
        try:
            version=wh.version(c)
            if int(req['expectedVersion'])!=version:raise VersionConflict('数据或配置已更新，请重新预览报价后再确认')
            result=_preview(wh,req.get('request') or {},c,version)
            if not result['target']:raise ValueError('请填写报价客户')
            if not any(x['price'] is not None for r in result['rows'] for x in r['cells']):raise ValueError('没有可报价样本')
            qid=uuid.uuid4().hex;name=str(req.get('name') or result['target']+'报价')[:200]
            c.execute('INSERT INTO quotes VALUES (?,?,?,?,?,?,?)',[qid,name,result['target'],now(),version,json.dumps(result['request'],ensure_ascii=False),json.dumps(result,ensure_ascii=False)])
            wh.audit(c,'保存报价',{'id':qid,'version':version});c.execute('COMMIT')
        except Exception:c.execute('ROLLBACK');raise
        finally:c.close()
    return {'id':qid,'result':result}

def listing(wh):
    rows=wh.cursor().execute("SELECT id,name,target,created,version FROM quotes WHERE json_extract_string(result,'$.schema')='v3' ORDER BY created DESC,id DESC").fetchall()
    return {'quotes':[dict(zip(('id','name','target','created','version'),r)) for r in rows]}

def read(wh,qid):
    r=wh.cursor().execute("SELECT id,name,created,result FROM quotes WHERE id=? AND json_extract_string(result,'$.schema')='v3'",[qid]).fetchone()
    if not r:raise ValueError('报价不存在')
    return {'id':r[0],'name':r[1],'created':r[2],'result':json.loads(r[3])}

def export(wh,qid):
    saved=read(wh,qid);r=saved['result'];s=r['scope']
    return [('报价表',['目的流向']+[b['label'] for b in r['bands']],[[row['flow']]+[x['price'] if x['price'] is not None else '' for x in row['cells']] for row in r['rows']]),
            ('说明',['项目','内容'],[['客户',r['target']],['样本期间',(s['start'] or '不限')+' 至 '+(s['end'] or '不限')],
             ['日期口径','寄件日期' if s['dateBasis']=='send' else '交件日期'],['单位','元/票，四舍五入至 0.1 元'],
             ['补贴类型','沿用现有补贴' if r.get('subsidyMode')=='existing' else r['subsidyType'] or '无补贴'],['确认时间',saved['created']],['说明','空白表示缺少可用样本，需另行商定；5kg以上为该段统一单票价。']])]
