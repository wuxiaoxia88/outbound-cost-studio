"""Validated province schemes and a single pre-aggregation exclusion predicate."""
import copy
import re

DEFAULT_GROUPS = [
    {'name':'一区','provinces':['上海','江苏','浙江','安徽']},
    {'name':'二区','provinces':['广东','福建','江西','山东','湖北','河北','北京','河南','天津','湖南']},
    {'name':'三区','provinces':['山西','陕西','重庆','贵州','辽宁','广西','云南','四川','吉林','黑龙江']},
    {'name':'四区','provinces':['内蒙古','甘肃','青海','宁夏','海南']},
    {'name':'五区','provinces':['新疆','西藏']},
]
DEFAULT = {'active':'default','schemes':{'default':{'name':'五区制','groups':DEFAULT_GROUPS}}}
SUFFIX = r'(维吾尔自治区|壮族自治区|回族自治区|自治区|特别行政区|省|市)$'


def short(name):
    return re.sub(SUFFIX, '', str(name or '').strip())


def validate(value=None):
    value = copy.deepcopy(DEFAULT if value is None else value)
    if not isinstance(value,dict) or not isinstance(value.get('schemes'),dict) or not 1<=len(value['schemes'])<=20:
        raise ValueError('请提供1–20套区域方案')
    if value.get('active') not in value['schemes']:
        raise ValueError('当前区域方案不存在')
    for key,scheme in value['schemes'].items():
        if not isinstance(key,str) or not key or not isinstance(scheme,dict):
            raise ValueError('区域方案格式不正确')
        scheme['name']=str(scheme.get('name','')).strip()
        groups=scheme.get('groups')
        if not scheme['name'] or not isinstance(groups,list) or not 1<=len(groups)<=50:
            raise ValueError('方案须有名称，且包含1–50个区域')
        names=set();provinces=set()
        for group in groups:
            if not isinstance(group,dict):raise ValueError('区域格式不正确')
            name=group.get('name');ps=group.get('provinces')
            if not isinstance(name,str) or not name.strip() or len(name)>50 or name.strip() in names:
                raise ValueError('区域名不能为空、重复或超过50字')
            group['name']=name.strip();names.add(group['name'])
            if not isinstance(ps,list) or len(ps)>100 or any(not isinstance(p,str) or not short(p) or len(p)>50 for p in ps):
                raise ValueError('省份须为有效名称列表')
            group['provinces']=[short(p) for p in ps]
            for p in group['provinces']:
                if p in provinces:raise ValueError('省份不能重复分组：'+p)
                provinces.add(p)
    return value


def groups(config):
    return config['schemes'][config['active']]['groups']


def scope(config, excluded=None):
    gs=groups(config);names={g['name'] for g in gs}
    if excluded is None:excluded=[]
    if not isinstance(excluded,list) or any(not isinstance(x,str) for x in excluded) or set(excluded)-names:
        raise ValueError('排除区域不在当前方案中，请刷新分组')
    excluded=[g['name'] for g in gs if g['name'] in excluded]
    # The UI retains one region; the API accepts an empty remaining scope and
    # returns honest zeroes (ungrouped provinces remain), without leaking data.
    return {'excludeZones':excluded,'excludeProvinces':sorted({p for g in gs if g['name'] in excluded for p in g['provinces']})}


def predicate(scope):
    ps=scope.get('excludeProvinces') or []
    if not ps:return [],[]
    if not isinstance(ps,list) or any(not isinstance(p,str) for p in ps):raise ValueError('排除省份格式不正确')
    return ["regexp_replace(trim(coalesce(province,'')), '%s', '') NOT IN (%s)" % (SUFFIX,','.join('?' for _ in ps))],list(ps)


def zone_of(province, config):
    p=short(province.split('/')[0])
    return next((g['name'] for g in groups(config) if p in g['provinces']),None)


def cube(cb, config):
    from .analysis import add,zero
    out={}
    for p,cp in cb.items():
        name=zone_of(p,config)
        if name is None:continue
        dest=out.setdefault(name,{})
        for u,v in cp.items():add(dest.setdefault(u,zero()),v)
    return out


def summary(ctx, names, all_view, config, excluded):
    from . import analysis as a
    cb=ctx['net'] if all_view else a.combine(ctx['cubes'],names)
    own=cube(cb,config);net=cube(ctx['net'],config);n=a.total(cb)[a.N];nn=ctx['netTotal'][a.N]
    rows=[]
    for group in groups(config):
        z=group['name'];cp=own.get(z,{});np=net.get(z,{})
        t=a.public(a.total({z:cp}));nt=a.public(a.total({z:np}))
        rows.append({**group,**t,'share':t['n']/n if n else 0,'network':nt,
                     'included':z not in excluded,'distribution':[sum(v[a.N] for u,v in cp.items() if i*10 < u <= (i+1)*10) for i in range(12)]})
    en=sum(r['n'] for r in rows if not r['included']);enn=sum(r['network']['n'] for r in rows if not r['included'])
    return {'rows':rows,'excluded':{'selected':{'n':en,'total':n,'share':en/n if n else 0},
                                   'network':{'n':enn,'total':nn,'share':enn/nn if nn else 0}},
            'ungroupedN':n-sum(r['n'] for r in rows)}
