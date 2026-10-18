'use strict';
const SIM_META=[['subsidy','一客一价补贴','账单标记一客一价的运单，每票减'],['operation','操作成本','全部正常件每票加'],['management','管理成本','全部正常件每票加'],['puhui','新普惠返利','全部正常件每票减（按日均件量对应档位）'],['w115','1–1.5kg 补贴','1–1.5kg 每票减'],['w13','1–3kg 超基数补贴','超过基数的部分每票减，折算到全部 1–3kg'],['tongpiao','通票扣减','全部正常件每票加'],['heavy','重货考核','3kg 以上：不足基数每 kg 罚，超过基数按中转费单价奖励'],['idle','市场闲置费','件量低于指标时每差 1 票罚'],['custom','返利（手工）','手填的每票返利'],['service','售后客服成本','每单拦截 / 退改加'],['addon','退改附加成本摊入客户成本','把退改件账单、退件费、客服成本摊到客户每票']];
const simCount=()=>Object.values(st.sim||{}).filter(x=>x.on).length;
function afterSalesPanel(result){
 const r=result.aftersales,net=r.network,g=r.group,n=r.normalN,divide=v=>n?v/n:null,per=r.per,netPer=net.per,high=net.rate>0&&r.rate>net.rate*1.5;
 const bar=(v,lab,col)=>`<div class="row" style="padding:4px 0"><span style="width:96px">${lab}</span><div style="flex:1;height:12px;background:var(--line2);border-radius:3px;overflow:hidden"><i style="display:block;height:100%;width:${r.rn?v/r.rn*100:0}%;background:${col}"></i></div><span class="num" style="width:110px;text-align:right">${int(v)} 票 · ${pct(r.n?v/r.n:0)}</span></div>`;
 const costs=[['退改件账单费用',r.rfee],['退件费（单独）',r.rbill],[`客服成本 ${result.simulation.items.service.on?'（'+result.simulation.items.service.v+' 元/单）':'<span class="hint">未开模拟</span>'}`,r.service],['<b>合计</b>',r.total]];
 return `<section class="panel" id="afterSales"><div class="panel-head"><h2>退改附加成本（拦截 / 退件 / 退改）</h2><span class="hint">不计入正常件单票成本；可在“成本模拟”中摊入客户成本</span></div><div class="cmp-grid aftersales-grid">
 <div><div class="eyebrow">退改情况</div><div class="v num ${high?'up':''}" style="font-size:26px;font-weight:600">${pct(r.rate)}</div><p class="hint" style="margin:2px 0 8px">网点 ${pct(net.rate)}${g?' · 同组 '+pct(g.rate):''} · 共 ${int(r.rn)} / ${int(r.n)} 票</p>${bar(r.n1,'拦截 + 退改','var(--c-main)')}${bar(r.n2,'仅拦截','var(--c-sub)')}${bar(r.n3,'退改 / 首中心退','var(--c-low)')}</div>
 <div><div class="eyebrow">附加成本</div><div class="kv" style="margin-top:6px"><span class="hint">项目</span><span class="hint">金额</span><span class="hint">摊到每票</span>${costs.map(([k,v])=>`<span>${k}</span><span class="num">${fmt(v)}</span><span class="num">${fmt(divide(v),3)}</span>`).join('')}</div><p class="hint">摊到每票 = 合计 ÷ 正常件 ${int(n)} 票；网点平均 ${fmt(netPer,3)}（不含客服）。</p></div>
 <div><div class="eyebrow">谈判提示</div>${!n?'<div class="ins">当前客户没有正常件，无法摊算每票成本。</div>':high?`<div class="ins warn" style="margin-top:6px"><b>退改率是网点的 ${fmt(r.rate/net.rate,1)} 倍</b><p>每票多出附加成本约 ${fmt(per-netPer,3)} 元。报价时可加收增值费，或约定退改上限 / 退件费由客户承担。</p></div>`:`<div class="ins" style="margin-top:6px"><b>${r.rate<net.rate*.7?'退改率低于网点':'退改率接近网点'}</b><p>每票附加成本 ${fmt(per,3)} 元（网点 ${fmt(netPer,3)}）。${r.rate<net.rate*.7?'可作为优质客户条件。':'无需额外条款。'}</p></div>`}</div></div></section>`;
}
function renderSim(){return NumericInputs.preserve(renderSimPanel)}
function renderSimPanel(){
 const host=$('#simulator');host.hidden=!st.simOpen;if(!st.simOpen||!st.result)return;
 const active=document.activeElement,key=active?.dataset.simv,scroll=host.querySelector('aside')?.scrollTop||0;
 const r=st.result,t=r.overview,sim=r.simulation,K=sim.constants,ret=r.aftersales,sm=st.sim;
 const num=(k,f,step,w=72)=>`<input class="num" type="text" inputmode="decimal" data-number="sim.${k}.${f}" data-committed="${sm[k][f]}" aria-label="${SIM_META.find(x=>x[0]===k)[1]} ${f}" data-simv="${k}.${f}" value="${sm[k][f]}" style="width:${w}px;border:1px solid var(--line);border-radius:6px;padding:3px 6px;background:var(--panel)">`;
 const tier=K.tier;
 const params={subsidy:`${num('subsidy','v',.01)} 元/票`,operation:`${num('operation','v',.01)} 元/票`,management:`${num('management','v',.01)} 元/票`,
  puhui:`${num('puhui','v',.0001)} 元/票 <button class="more" data-simtier="${tier.value}" style="padding:0 0 0 6px">按日均 ${int(K.daily)} 票套用 ${tier.lo}–${tier.hi??'∞'} 万档（${tier.value}）</button>`,
  w115:`${num('w115','v',.01)} 元/票`,w13:`基数 ${num('w13','base',1000,96)} 票 × ${num('w13','v',.01)} 元`,tongpiao:`${num('tongpiao','v',.01)} 元/票`,
  heavy:`基数 ${num('heavy','base',100,90)} kg · 罚 ${num('heavy','rate',.1,56)} 元/kg · 奖励 ${num('heavy','share',.05,56)} × 中转费单价<br><span class="hint">本期 3kg 以上结算重量 ${fmt(K.w3)} kg → ${K.heavyAmount<0?'罚 '+fmt(-K.heavyAmount):'奖 '+fmt(K.heavyAmount)} 元</span>`,
  idle:`指标 ${num('idle','target',10000,110)} 票 · 每差 1 票 ${num('idle','v',.1,56)} 元<br><span class="hint">本期正常件 ${int(K.n)} 票</span>`,custom:`${num('custom','v',.01)} 元/票（减）`,
  service:`${num('service','v',.1)} 元/单（本期 ${int(ret.rn)} 单退改）`,addon:`本客户 ${fmt(ret.total)} 元 ÷ ${int(t.n)} 票 = ${fmt(ret.per,3)} 元/票`};
 const rows=SIM_META.map(([k,label,desc])=>{const on=sm[k].on,e=sim.effects[k];return `<div class="sim-row ${on?'on':''}"><button class="switch" role="switch" aria-checked="${on}" data-simk="${k}" aria-label="${label}"></button><div class="sim-body"><b>${label}</b> <span class="hint">${desc}</span><div class="sim-params">${params[k]}</div></div><div class="sim-eff num">${on?st.pending?'<span class="hint">重算中…</span>':`${signed(t.n?e/t.n:null)}<small>元/票</small><br><span class="hint">${signed(e,2)} 元</span>`:'<span class="hint">未开启</span>'}</div></div>`}).join('');
 host.innerHTML=`<aside class="drawer sim" role="dialog" aria-label="成本模拟"><button class="xbtn" data-sim="close" aria-label="关闭成本模拟">×</button><div><div class="eyebrow">${esc(selName().slice(0,30))} · ${int(t.n)} 票</div><h3>成本模拟</h3><p class="hint" style="margin:4px 0 0">打开开关即时重算成本分析和报价。比价指标（比网点贵 / 便宜、▲▼）不受影响。</p><span class="hint sim-status" role="status">${st.pending?'正在重算…':''}</span></div><div class="kpis" style="grid-template-columns:repeat(3,minmax(0,1fr))">${[['账单单票',fmt(t.avgSystem,3),''],['模拟调整',signed(sim.per),sim.count+' 项开启'],['模拟后单票',fmt(t.avg,3),'']].map(([k,v,n])=>`<div class="kpi"><span class="k">${k}</span><span class="v num" style="font-size:20px">${v}</span><span class="s">${n}</span></div>`).join('')}</div><div class="sim-list">${rows}</div><div style="display:flex;gap:8px;flex-wrap:wrap">${btn('全部关闭','data-sim="reset"')}<button class="btn primary" data-sim="save" ${st.pending||st.error?'disabled':''}>保存为默认</button></div></aside>`;
 host.querySelector('aside').scrollTop=scroll;
 if(key)host.querySelector(`[data-simv="${key}"]`)?.focus({preventScroll:true});
}
function simulationChanged(){qs.context=null;qs.result=null;qs.saved=null;qs.overrides={};schedule('成本模拟');renderSim()}
document.addEventListener('click',async e=>{const t=e.target.closest('button');if(!t||t.disabled)return;
 if(t.dataset.simk){st.sim[t.dataset.simk].on=!st.sim[t.dataset.simk].on;simulationChanged();return}
 if(t.dataset.simtier){st.sim.puhui.v=+t.dataset.simtier;simulationChanged();return}
 const action=t.dataset.sim;if(!action)return;
 if(action==='open'){closeDrawer(false);st.simOpen=true;renderSim();return}
 if(action==='close'){st.simOpen=false;renderSim();return}
 if(action==='reset'){Object.values(st.sim).forEach(x=>x.on=false);simulationChanged();return}
 if(action==='save'){if(!NumericInputs.validate($('#simulator')))return;t.disabled=true;try{await api('config',{...st.meta.config,simulation:st.sim,expectedVersion:st.meta.dataVersion});await reloadMeta();cfgDraft=null;toast('已保存成本模拟默认值');schedule('保存模拟默认')}catch(e){toast(e.message)}finally{renderSim()}}
});
document.addEventListener('numericcommit',e=>{const t=e.target;if(!t.dataset.simv)return;const [k,f]=t.dataset.simv.split('.');st.sim[k][f]=e.detail;simulationChanged()});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&st.simOpen){st.simOpen=false;renderSim()}});
