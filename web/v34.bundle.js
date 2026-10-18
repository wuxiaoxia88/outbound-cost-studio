// v34-core.js
/* Pure presentation helpers. All cost and reference calculations stay on the server. */
(function(root){
'use strict';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(v,d=2)=>v==null||!Number.isFinite(+v)?'—':(+v).toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d});
const int=v=>v==null?'—':Math.round(v).toLocaleString('zh-CN');
const pct=(v,d=1)=>v==null||!Number.isFinite(v)?'—':(v*100).toFixed(d).replace('-','−')+'%';
const signed=(v,d=3)=>{if(v==null)return '—';const n=Math.abs(v)<.5*10**(-d)?0:v;return (n>=0?'+':'−')+fmt(Math.abs(n),d)};
const short=n=>String(n??'').trim().replace(/(维吾尔自治区|壮族自治区|回族自治区|自治区|特别行政区|省|市)$/,'');
const day=d=>d.toISOString().slice(0,10);
const dateLabel=(basis,businessDay=false)=>basis==='send'?'寄件':'交件'+(businessDay?'（业务日 12:00–次日 12:00）':'（自然日）');
function importRange(imported,basis='send'){return imported?{...imported,...(imported.dateRanges?.[basis]||{})}:null}
function dateRange(kind,today,imported){
  const d=new Date(today+'T12:00:00Z'),y=d.getUTCFullYear(),m=d.getUTCMonth();
  if(kind==='import')return imported?{start:imported.start,end:imported.end}:null;
  if(kind==='week'){d.setUTCDate(d.getUTCDate()-6);return {start:day(d),end:today}}
  if(kind==='month')return {start:day(new Date(Date.UTC(y,m,1))),end:today};
  if(kind==='previous')return {start:day(new Date(Date.UTC(y,m-1,1))),end:day(new Date(Date.UTC(y,m,0)))};
  return null;
}
const today=(basis='send',clock=new Date())=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(clock.getTime()-(basis==='handover'?12*3600000:0)));
function selected(names,name){const out=names==='all'?[name]:[...names];if(!out.includes(name))out.push(name);if(out.length>8)throw Error('最多同时对比 8 个客户');return out}
function normalize(s){return {...s,i:s.name,t:s.total||{n:s.n},cr:s.critical||{n:s.criticalN},topProv:s.topProvinces||[],d:[{n:0,fee:0},...(s.distribution||[])]}}
function referenceParts(t){const diff=(Math.round(t.eq*1000)-Math.round(t.avg*1000))/1000,price=-Math.round(t.gap*1000)/1000;return {diff,price,extra:(Math.round(diff*1000)-Math.round(price*1000))/1000}}
function ownQuoteCustomer(customers,target,basis){return basis==='selection'&&Array.isArray(customers)&&customers.length===1&&target.trim()===customers[0]}
function quoteSelection(result,rows,cols){
  if(!result||(!rows.size&&!cols.size))return {rows:0,cols:0,cells:[]};
  const selectedRows=[...result.rows,...(result.remoteRows||[])].filter(r=>!rows.size||rows.has(r.flow));
  const selectedCols=result.bands.map((_,i)=>i).filter(i=>!cols.size||cols.has(i));
  return {rows:selectedRows.length,cols:selectedCols.length,cells:selectedRows.flatMap(r=>selectedCols.map(i=>r.cells[i]).filter(c=>c&&c.price!=null))};
}
const validNumber=s=>String(s).trim()!=='' && /^(?:\d+\.?\d*|\.\d+)$/.test(String(s).trim()) && Number.isFinite(Number(s)) && Number(s)>=0;
const api={validNumber,ownQuoteCustomer,quoteSelection,referenceParts,esc,fmt,int,pct,signed,short,dateLabel,importRange,dateRange,today,selected,normalize};
if(typeof module==='object'&&module.exports)module.exports=api;else root.V3=api;
})(typeof globalThis!=='undefined'?globalThis:this);

;
// v34-charts.js
'use strict';
// SVG renderers ported from the approved design/ui-preview prototype.
function nice(lo,hi,count=4){if(!Number.isFinite(lo)||!Number.isFinite(hi)){lo=0;hi=1}if(hi<=lo){lo=Math.min(0,lo);hi=Math.max(1,hi+1)}const raw=(hi-lo)/count,mag=10**Math.floor(Math.log10(raw)),step=[1,2,2.5,5,10].map(m=>m*mag).find(s=>s>=raw);const a=Math.floor(lo/step)*step,b=Math.ceil(hi/step)*step;const t=[];for(let v=a;v<=b+step/2;v+=step)t.push(+v.toFixed(6));return {lo:a,hi:b,ticks:t}}
function rangeU(d){const tot=d.reduce((s,x)=>s+x.n,0);let c=0;for(let u=1;u<=60;u++){c+=d[u].n;if(c>=tot*.995)return Math.max(20,Math.min(60,Math.ceil(u/5)*5))}return 30}
function distChart(d,netd,bands,opts={}){
  const W=1180,H=300,L=46,R=52,T=24,B=40,maxU=opts.maxU||rangeU(d);
  const iw=W-L-R,ih=H-T-B,bw=iw/maxU;
  const tot=d.reduce((s,x)=>s+x.n,0)||1, shares=d.map(x=>st.unit==='share'?x.n/tot:x.n);
  const sy=nice(0,Math.max(...shares.slice(1,maxU+1)),4),ymax=sy.hi;
  const yl=v=>st.unit==='share'?+(v*100).toFixed(1)+'%':v>=10000?+(v/10000).toFixed(1)+'万':int(v);
  const costs=[];for(let u=1;u<=maxU;u++){if(d[u].n>=30)costs.push(d[u].fee/d[u].n);if(netd[u].n>=30)costs.push(netd[u].fee/netd[u].n)}
  const cy=nice(Math.min(...costs),Math.max(...costs),4),cmin=cy.lo,cmax=cy.hi;
  const x=u=>L+(u-1)*bw, ys=v=>T+ih-(v/ymax)*ih, yc=v=>T+ih-((v-cmin)/(cmax-cmin))*ih;
  let g='';
  for(const v of sy.ticks){const y=ys(v);g+=`<line x1="${L}" x2="${W-R}" y1="${y}" y2="${y}" stroke="var(--line2)"/><text x="${L-6}" y="${y+4}" text-anchor="end">${yl(v)}</text>`}
  for(const c of cy.ticks)g+=`<text x="${W-R+6}" y="${yc(c)+4}">${c.toFixed(2)}</text>`;
  for(let u=1;u<=maxU;u++){const b=bandOf(bands,u),cls=b>=0?bands[b].cls:'low',h=ih-(ys(shares[u])-T);
    g+=`<rect x="${x(u)+1}" y="${ys(shares[u])}" width="${bw-2}" height="${Math.max(0,h)}" class="b-${cls}" rx="2"><title>${u===60?'5.9kg以上':((u-1)/10).toFixed(1)+'–'+(u/10).toFixed(1)+'kg'}：${int(d[u].n)}票，占${pct(d[u].n/tot)}${d[u].n?'，单票'+fmt(d[u].fee/d[u].n,3)+'元':''}</title></rect>`;
    if(u%5===0)g+=`<text x="${x(u)+bw}" y="${H-B+16}" text-anchor="middle">${(u/10).toFixed(1)}</text>`}
  g+=`<text x="${L}" y="${H-B+16}" text-anchor="middle">0</text><text x="${W-R}" y="${H-6}" text-anchor="end" class="lbl">结算重量 kg</text>`;
  for(const [s,j] of S.steps){if(s*10>maxU)continue;const xx=L+s*10*bw;g+=`<line x1="${xx}" x2="${xx}" y1="${T-6}" y2="${T+ih}" stroke="var(--warn)" stroke-dasharray="4 3"/><text x="${xx>W-R-130?xx-4:xx+4}" text-anchor="${xx>W-R-130?'end':'start'}" y="${T+4}" style="fill:var(--warn)">档位 ${s}kg · +${j.toFixed(2)}元</text>`}
  const line=(dd,minN)=>{let p='',connected=false;for(let u=1;u<=maxU;u++){if(dd[u].n<minN){connected=false;continue}p+=(connected?'L':'M')+(x(u)+bw/2).toFixed(1)+','+yc(dd[u].fee/dd[u].n).toFixed(1);connected=true}return p};
  if(!isAll())g+=`<path d="${line(netd,30)}" fill="none" stroke="var(--ink3)" stroke-width="1.5" stroke-dasharray="5 4"/>`;
  g+=`<path d="${line(d,30)}" fill="none" stroke="var(--r2)" stroke-width="2.2"/>`;
  for(let u=1;u<=maxU;u++)if(d[u].n>=30)g+=`<circle cx="${x(u)+bw/2}" cy="${yc(d[u].fee/d[u].n)}" r="2.6" fill="var(--r2)"/>`;
  g+=`<text x="${L-6}" y="${T-10}" text-anchor="end" class="lbl">${st.unit==='share'?'票量占比':'票数'}</text><text x="${W-4}" text-anchor="end" y="${T-10}" class="lbl">单票成本 元</text>`;
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="重量分布与单票成本">${g}</svg>`}

function overlayChart(list,maxU=rangeU(Array.from({length:61},(_,u)=>({n:list.reduce((n,s)=>n+(s.d[u]?.n||0),0)})))){
  const W=1180,H=260,L=46,R=20,T=18,B=36,iw=W-L-R,ih=H-T-B,bw=iw/maxU;
  const series=list.map(s=>{const tot=s.t.n;return s.d.map(x=>st.unit==='share'?x.n/(tot||1):x.n)});
  const sy=nice(0,Math.max(...series.flatMap(a=>a.slice(1,maxU+1))),4),ymax=sy.hi;
  const x=u=>L+(u-.5)*bw,y=v=>T+ih-(v/ymax)*ih;let g='';
  for(const v of sy.ticks)g+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line2)"/><text x="${L-6}" y="${y(v)+4}" text-anchor="end">${st.unit==='share'?+(v*100).toFixed(1)+'%':int(v)}</text>`;
  for(let u=5;u<=maxU;u+=5)g+=`<text x="${L+u*bw}" y="${H-B+16}" text-anchor="middle">${(u/10).toFixed(1)}</text>`;
  for(const [s] of S.steps){if(s*10>maxU)continue;const xx=L+s*10*bw;g+=`<line x1="${xx}" x2="${xx}" y1="${T}" y2="${T+ih}" stroke="var(--warn)" stroke-dasharray="4 3"/><text x="${xx+4}" y="${T+10}" style="fill:var(--warn)">${s}kg</text>`}
  list.forEach((s,k)=>{let p='';for(let u=1;u<=maxU;u++)p+=(p?'L':'M')+x(u).toFixed(1)+','+y(series[k][u]).toFixed(1);g+=`<path d="${p}" fill="none" stroke="${colorOf(s.i)}" stroke-width="2.2"/>`});
  g+=`<text x="${W-R}" y="${H-4}" text-anchor="end" class="lbl">结算重量 kg</text>`;
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="客户重量分布对比">${g}</svg>`}

function waterfall(steps){ // [{label,v,kind:'base'|'delta'|'end'}]
  const W=560,H=250,L=44,R=12,T=16,B=48,iw=W-L-R,ih=H-T-B;
  let run=0;const bars=steps.map(s=>{if(s.kind==='base'){run=s.v;return {...s,a:0,b:s.v}}if(s.kind==='end')return {...s,a:0,b:s.v};const a=run;run+=s.v;return {...s,a,b:run}});
  const vals=bars.flatMap(b=>b.kind==='delta'?[b.a,b.b]:[b.b]);const ax=nice(Math.min(...vals)-.3,Math.max(...vals),4),vmin=ax.lo,hi=ax.hi;
  const y=v=>T+ih-((v-vmin)/(hi-vmin))*ih,bw=iw/bars.length;let g='';
  for(const v of ax.ticks)g+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line2)"/><text x="${L-6}" y="${y(v)+4}" text-anchor="end">${v.toFixed(2)}</text>`;
  bars.forEach((b,k)=>{const top=y(Math.max(b.a,b.b)),bot=y(b.kind==='delta'?Math.min(b.a,b.b):vmin);const col=b.kind!=='delta'?'var(--c-sub)':b.v>=0?'var(--r2)':'var(--g2)';
    g+=`<rect x="${L+k*bw+bw*.18}" y="${top}" width="${bw*.64}" height="${Math.max(1,bot-top)}" fill="${col}" rx="3"/>`;
    const t=b.kind==='delta'?(b.v>=0?'+':'')+b.v.toFixed(3):b.v.toFixed(3);
    g+=`<text x="${L+k*bw+bw/2}" y="${top-5}" text-anchor="middle" class="lbl">${t}</text><text x="${L+k*bw+bw/2}" y="${H-B+16}" text-anchor="middle" class="lbl">${esc(b.label)}</text>`});
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="成本差异分解">${g}</svg>`}

;
// v34-simulation.js
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

;
// v34-inputs.js
/* Decimal drafts survive async rendering; only committed numbers enter calculations. */
'use strict';
const NumericInputs=(()=>{
 const drafts=new Map(),timers=new Map();let restoring=false;
 const key=t=>t?.dataset?.number;
 function error(t,bad){t.setAttribute('aria-invalid',String(bad));t.setCustomValidity(bad?'请输入不小于 0 的数字':'');let note=t.nextElementSibling;if(note?.classList.contains('numeric-error'))note.remove();if(bad){note=document.createElement('span');note.className='numeric-error';note.setAttribute('role','alert');note.textContent='请输入不小于 0 的数字';t.after(note)}}
 function commit(t){const k=key(t);if(!k||t.disabled)return true;clearTimeout(timers.get(k));timers.delete(k);const raw=t.value,valid=V3.validNumber(raw);drafts.set(k,raw);error(t,!valid);if(!valid)return false;const v=Number(raw);if(t.dataset.committed===String(v))return true;t.dataset.committed=String(v);t.dispatchEvent(new CustomEvent('numericcommit',{bubbles:true,detail:v}));return true}
 function preserve(fn){if(restoring)return fn();const a=document.activeElement,k=key(a),id=a?.id,search=id==='custSearch'?{value:a.value,open:a.getAttribute('aria-expanded')==='true'}:null,pos=a?.selectionStart,end=a?.selectionEnd;
   document.querySelectorAll('[data-number]').forEach(t=>{if(key(t))drafts.set(key(t),t.value)});
   restoring=true;try{fn();document.querySelectorAll('[data-number]').forEach(t=>{if(drafts.has(key(t))&&(key(t)===k||timers.has(key(t))||!V3.validNumber(drafts.get(key(t))))){t.value=drafts.get(key(t));if(!V3.validNumber(t.value))error(t,true)}});const next=k?[...document.querySelectorAll('[data-number]')].find(t=>key(t)===k):id?document.getElementById(id):null;if(next&&!next.disabled){if(search)next.value=search.value;next.focus({preventScroll:true});if(search){search.open?comboRender():comboClose()}if(pos!=null&&next.setSelectionRange)try{next.setSelectionRange(pos,end)}catch{}}}finally{restoring=false}}
 function validate(root=document){let valid=true;root.querySelectorAll('[data-number]:not(:disabled)').forEach(t=>{const ok=V3.validNumber(t.value);error(t,!ok);if(!ok)valid=false});return valid}
 document.addEventListener('input',e=>{const t=e.target,k=key(t);if(!k)return;drafts.set(k,t.value);delete t.dataset.committed;clearTimeout(timers.get(k));error(t,false);timers.set(k,setTimeout(()=>{const current=[...document.querySelectorAll('[data-number]')].find(x=>key(x)===k);if(current)commit(current)},500))});
 document.addEventListener('focusout',e=>{if(!restoring&&key(e.target))commit(e.target)});
 document.addEventListener('keydown',e=>{if(e.key==='Enter'&&key(e.target)){e.preventDefault();commit(e.target)}});
 return {commit,preserve,validate,clear(prefix=''){document.querySelectorAll('[data-number]').forEach(t=>{if(key(t).startsWith(prefix))t.removeAttribute('data-number')});for(const k of drafts.keys())if(k.startsWith(prefix)){drafts.delete(k);clearTimeout(timers.get(k));timers.delete(k)}}};
})();

;
// v34-history.js
'use strict';
const tip=text=>`<button type="button" class="tip" data-tip="${V3.esc(text)}" aria-label="说明：${V3.esc(text)}" aria-expanded="false" aria-describedby="pop">?</button>`;
function deltaText(k,v){return v==null?'—':k==='n'||k==='daily'?signed(v*100,1)+'%':k==='criticalShare'||k==='mainShare'?signed(v*100,1)+' 个百分点':signed(v,k==='avgW'?2:3)+(k==='avgW'?' kg':k==='avg'?' 元（账单）':'')}
function periodDelta(r,k){const p=r.previous;if(!p?.enabled)return '';return `<div class="delta">${p.summary?.n<300?`上期仅 ${int(p.summary.n)} 票，不比较`:`较 ${esc(p.range?.label||'上期')} <span class="${p.delta?.[k]>0?'up':p.delta?.[k]<0?'down':''}">${deltaText(k,p.delta?.[k])}</span>`}</div>`}
function historyPanel(r){const h=r.history;if(!h?.months?.length)return `<section class="panel"><h2>历史对比</h2><p class="hint">当前范围以前暂无寄件月份数据。</p></section>`;
 const a=h.current,b=h.previous,band=h.mainBand,bl=band.hi==null?`${band.lo}kg以上`:`${band.lo}–${band.hi}kg`,label=m=>esc(m);
 const values=x=>[int(x.n),int(x.daily),fmt(x.avgW),fmt(x.avg,3),pct(x.criticalShare),pct(x.mainShare)];
 const delta=h.delta?['n','daily','avgW','avg','criticalShare','mainShare'].map(k=>`<td class="num ${h.delta[k]>0?'up':h.delta[k]<0?'down':''}">${deltaText(k,h.delta[k]).replace(' kg','').replace(' 元（账单）','')}</td>`).join(''):`<td colspan="6">${label(b.month)} 仅 ${int(b.n)} 票，样本太少，不做比较</td>`;
 const bins=a.distribution.map((v,i)=>({i,a:v,b:b.distribution[i]})).filter(x=>Math.max(x.a,x.b)>=.01),max=Math.max(.01,...bins.flatMap(x=>[x.a,x.b]));
 return `<section class="panel" id="historyPanel"><div class="panel-head"><h2>历史对比</h2><span class="hint">按寄件月份 · 只含正常件 · 单票为账单费用 · 截至 ${label(a.month)}</span></div><div class="history-grid"><div><div class="tw"><table class="list fin"><thead><tr>${['月份','发件量','日均','均重 kg','账单单票 元','刚超档件占比',bl+' 占比'].map(t=>`<th>${t}</th>`).join('')}</tr></thead><tbody>${h.months.map(x=>`<tr><td>${esc(x.month)}</td>${values(x).map(v=>`<td class="num">${v}</td>`).join('')}</tr>`).join('')}<tr class="total"><td>变化</td>${delta}</tr></tbody></table></div>${h.hiddenMonthCount>0?`<p class="hint">另有 ${int(h.hiddenMonthCount)} 个零星月份（不足当月 10%，多为补录件）未列出</p>`:''}<p class="hint">${h.delta?`单票成本较 ${label(b.month)} ${h.delta.avg==null?'暂无本期样本':`${h.delta.avg>=0?'上升':'下降'} ${fmt(Math.abs(h.delta.avg),3)} 元`}；均重 ${deltaText('avgW',h.delta.avgW)}，刚超档件占比 ${deltaText('criticalShare',h.delta.criticalShare)}。`:'上期样本不足，暂不判断价格和重量变化。'} 当前主要重量段：${bl}。</p></div><div><div class="legend"><b>重量结构变化</b><span><i class="sw" style="background:var(--c-low)"></i>${label(b.month)}</span><span><i class="sw" style="background:var(--c-main)"></i>${label(a.month)}</span></div>${bins.map(x=>`<div class="hbar"><span class="num">${x.i===11?'5.5kg以上':`${x.i*.5}–${(x.i+1)*.5}kg`}</span><div class="hb"><i class="prev" style="width:${x.b/max*100}%" title="${pct(x.b)}"></i><i class="cur" style="width:${x.a/max*100}%" title="${pct(x.a)}"></i></div><span class="hv num">${pct(x.a)}${h.delta?`<small class="${x.a>x.b?'up':'down'}" title="变化（百分点）">${signed((x.a-x.b)*100,1)}</small>`:''}</span></div>`).join('')||'<p class="hint">没有重量分布数据</p>'}</div></div></section>`;
}
function chartControls(){return `<div class="seg" style="margin-left:auto" aria-label="重量图纵轴">${['share','count'].map(k=>`<button data-unit="${k}" aria-pressed="${st.unit===k}">${k==='share'?'占比':'票量'}</button>`).join('')}</div>`}
function heatControls(){return `<div class="seg" aria-label="格子小字">${[['count','票量'],['share','占比'],['weight','均重']].map(([k,n])=>`<button data-heatunit="${k}" aria-pressed="${st.heatUnit===k}">${n}</button>`).join('')}</div>`}
function cellSub(c,total){return st.heatUnit==='weight'?fmt(c.avgW)+'kg':st.heatUnit==='count'?int(c.n)+'票':pct(total?c.n/total:0)}
function ranking(r){const compare=r.previous?.enabled,sort=st.rankSort,value=s=>sort==='n'?s.total.n:s.previous?.delta?.[sort==='nChange'?'n':sort];
 const list=r.ranking.slice().sort((a,b)=>{const x=value(a),y=value(b);return x==null?(y==null?0:1):y==null?-1:(st.rankAsc?1:-1)*(x-y)||b.total.n-a.total.n});
 const changeCell=(s,k)=>s.previous?.n<300?(s.previous.n?`上期仅 ${int(s.previous.n)} 票`:'新客户'):deltaText(k,s.previous?.delta?.[k]).replace(' 元（账单）','');
 const headers=['客户','分群','票数','日均','占网点','均重 kg','主要重量','账单单票 元','比网点贵 / 便宜','刚超档','退改率','附加成本/票'];
 if(compare)headers.push('发件量较上期','单票较上期');
 return `<section class="panel"><div class="panel-head"><h2>客户排行</h2><span class="hint">点一行进入该客户；变化按账单费用比较</span></div><div class="tw"><table class="list"><thead><tr>${headers.map((t,i)=>`<th>${i===2||i>=12?`<button class="sort" data-ranksort="${i===2?'n':i===12?'nChange':'avg'}">${t} ↕</button>`:t}</th>`).join('')}</tr></thead><tbody>${(st.rankAll?list:list.slice(0,30)).map(s=>`<tr class="click" tabindex="0" data-solo="${esc(s.name)}"><td>${esc(s.name)}</td><td>${s.total.n>=r.settings.parameters.minGroupN?esc(s.key):'<span class="hint">小客户</span>'}</td>${[int(s.total.n),int(s.daily),pct(s.total.n/r.network.n),fmt(s.avgW),esc(s.main),fmt(s.avg,3),signed(s.gap),pct(s.critical.n/s.total.n),pct(s.returnRate),fmt(s.returnPer,3),...(compare?[changeCell(s,'n'),changeCell(s,'avg')]:[])].map(v=>`<td class="num">${v}</td>`).join('')}</tr>`).join('')}</tbody></table></div><button class="more" id="rankMore">${st.rankAll?'只看前 30 个':'展开全部 '+(r.rankingCount||r.ranking.length)+' 个客户'}</button></section>`;
}

;
// v34-regions.js
'use strict';
const SKINS=[['navy','深蓝','冷灰底、海军蓝强调，默认'],['ledger','账簿','米白纸色、墨褐与皮革棕，标题宋体']];
const AP={skin:'navy',mode:'system'};
try{const saved=JSON.parse(localStorage.getItem('cost-studio-appearance')||'{}');if(['navy','ledger'].includes(saved.skin))AP.skin=saved.skin;if(['system','light','dark'].includes(saved.mode))AP.mode=saved.mode}catch{}
function applyAppearance(){const r=document.documentElement;r.dataset.skin=AP.skin;if(AP.mode==='system')delete r.dataset.theme;else r.dataset.theme=AP.mode;try{localStorage.setItem('cost-studio-appearance',JSON.stringify(AP))}catch{}}
applyAppearance();
function renderAppearance(){document.querySelector('#skins').innerHTML=SKINS.map(([k,l])=>`<button class="sw-skin sk-${k}" data-skinpick="${k}" aria-pressed="${AP.skin===k}" title="主题：${l}" aria-label="主题：${l}"></button>`).join('')}
function appearancePanel(){return `<section class="panel"><div class="panel-head"><h2>外观</h2><span class="hint">只影响你自己的浏览器；导出的 A4 报告始终用浅色打印</span></div><div class="ap-grid"><div class="field"><span class="lab">主题</span><div class="skin-cards">${SKINS.map(([k,l,d])=>`<button class="skin-card sk-${k}" data-skinpick="${k}" aria-pressed="${AP.skin===k}"><span class="sk-prev" aria-hidden="true"><i></i><i></i><i></i></span><b>${l}</b><span class="hint">${d}</span></button>`).join('')}</div></div><div class="field"><span class="lab">明暗</span><div class="seg">${[['system','跟随系统'],['light','浅色'],['dark','深色']].map(([k,l])=>`<button data-modepick="${k}" aria-pressed="${AP.mode===k}">${l}</button>`).join('')}</div></div></div></section>`}
function activeZones(cfg=st.meta.config.regionGroups){return cfg.schemes[cfg.active].groups}
function visibleZones(cfg=st.meta.config.regionGroups){const gs=activeZones(cfg),hmt=['香港','澳门','台湾'];return cfg.showHMT?gs:gs.filter(g=>(!g.provinces.length&&!g.hmtGroup)||g.provinces.some(p=>!hmt.includes(short(p)))).map(g=>({...g,provinces:g.provinces.filter(p=>!hmt.includes(short(p)))}))}

function zoneControls(){const cfg=st.meta.config.regionGroups,z=visibleZones(),quick=cfg.active==='default'&&z.some(g=>g.name.includes('四区')||g.name.includes('五区'));return `<div class="field zrange"><span class="lab">测算范围 ${tip('取消某个区域后，所有成本、历史、退改和网点参考按剩余流向重新计算；数据不会删除。未分组省份继续参与。')}</span><div class="zchips">${z.map(g=>`<button class="zchip" data-zone="${esc(g.name)}" aria-pressed="${!st.excludeZones.includes(g.name)}" title="${esc(g.provinces.join('、'))}">${esc(g.name)}</button>`).join('')}${quick?btn('排除偏远（四、五区）','data-zremote'):btn('恢复全部','data-zall')}</div></div>`}
function zoneBanner(){if(st.pending)return '';const r=st.result,e=r?.zones?.excluded.selected;if(!r?.scope.excludeZones.length)return '';return `<div class="zbanner"><b>已排除 ${r.scope.excludeZones.map(esc).join('、')}</b><span>所选 ${int(e.n)} 票（${pct(e.share)}），网点 ${int(r.zones.excluded.network.n)} 票（${pct(r.zones.excluded.network.share)}）不参与测算</span><button class="linkbtn" data-zall>恢复全部</button></div>`}
function zonePanel(r){const zs=r.zones.rows,max=Math.max(.01,...zs.flatMap(z=>z.distribution.map(n=>z.n?n/z.n:0)));return `<section class="panel" id="zonePanel"><div class="panel-head"><h2>区域分布</h2><span class="hint">按流向区域分组 · 单票为账单费用 · 小图为 0–6kg 每 0.5kg 票量结构 · 保留排除前数据供对照</span></div><div class="tw"><table class="list fin zt"><thead><tr>${['区域','票数','占比','均重 kg','账单单票 元','网点该区单票 元','重量结构',''].map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${zs.map(z=>`<tr class="${z.included?'':'off'}"><th><b>${esc(z.name)}</b><span class="hint"> ${esc(z.provinces.slice(0,4).join(' '))}${z.provinces.length>4?' 等 '+z.provinces.length+' 省':''}</span></th><td class="num">${int(z.n)}</td><td><div class="zbar"><i style="width:${z.share*100}%"></i></div><span class="num">${pct(z.share)}</span></td><td class="num">${fmt(z.avgW)}</td><td class="num">${fmt(z.avgSystem,3)}</td><td class="num">${fmt(z.network.avgSystem,3)}</td><td><svg class="zspark" viewBox="0 0 120 26" role="img" aria-label="${esc(z.name)}重量结构">${z.distribution.map((n,i)=>{const h=z.n?n/z.n/max*24:0;return `<rect x="${i*10+1}" y="${25-h}" width="8" height="${h}" rx="1"/>`}).join('')}</svg></td><td><button class="zchip sm" data-zone="${esc(z.name)}" aria-pressed="${z.included}">${z.included?'参与测算':'已排除'}</button></td></tr>`).join('')}</tbody></table></div>${r.zones.ungroupedN?`<p class="hint">另有未分组 ${int(r.zones.ungroupedN)} 票照常参与测算，不列入区域视图。</p>`:''}</section>`}
function zoneHeatPanel(r){const rows=r.heat.rows.flatMap(z=>[z,...(st.expandedZones.includes(z.flow)?z.provinces:[])]),fake={...r,level:'zone-render',heat:{...r.heat,rows,rest:null,rowCount:0}};const box=document.createElement('div');box.innerHTML=heatPanel(fake);box.querySelector('.panel-head .hint').textContent='区域按方案顺序排列；展开查看省份，点省份格子查看明细';for(const row of box.querySelectorAll('tbody tr')){const header=row.querySelector('.rowh'),name=header?.textContent;if(r.heat.rows.some(z=>z.flow===name)){row.classList.add('zrow');header.innerHTML=`<button class="ztoggle" data-expandzone="${esc(name)}" aria-expanded="${st.expandedZones.includes(name)}"><span class="car">${st.expandedZones.includes(name)?'▾':'▸'}</span>${esc(name)}</button>`;row.querySelectorAll('[data-flow]').forEach(c=>{c.removeAttribute('data-flow');c.removeAttribute('data-band');c.removeAttribute('tabindex')})}else if(!row.classList.contains('foot'))row.classList.add('zsub')}if(r.zones.ungroupedN)box.insertAdjacentHTML('beforeend',`<p class="hint">区域合计仅含已分组省份；另有未分组 ${int(r.zones.ungroupedN)} 票计入概览。</p>`);return box.innerHTML}
function quoteZoneRows(rows,td,disabled,r){const groups=visibleZones(r.regionGroups||st.meta.config.regionGroups);return [...groups.map(g=>({name:g.name,provinces:g.provinces})),{name:null,provinces:[]}].map(g=>{const rs=rows.filter(x=>x.zone===g.name||(!x.zone&&!g.name));if(!rs.length)return '';return `<tr class="qzone"><th colspan="8">${esc(g.name||'未分组')} <span class="hint">${esc(g.provinces.join(' '))}</span></th></tr>`+rs.map(row=>`<tr><th style="text-align:left"><label><input type="checkbox" data-qrow="${esc(row.flow)}" aria-label="选择 ${esc(row.flow)} 行" ${qs.rows.has(row.flow)?'checked':''} ${disabled?'disabled':''}> ${esc(row.flow)}</label></th>${row.cells.map(td).join('')}</tr>`).join('')}).join('')}
function remoteQuoteTable(r,td,disabled){return r.remoteRows?.length?`<div class="remote"><div class="panel-head"><h2>偏远区域单独报价</h2><span class="hint">按未排除的全部数据计算；不计入上方收入、毛利估算</span></div><div class="tw"><table class="quote"><thead><tr><th>流向</th>${r.bands.map(b=>`<th>${esc(b.label)}</th>`).join('')}</tr></thead><tbody>${quoteZoneRows(r.remoteRows,td,disabled,r)}</tbody></table></div></div>`:''}
function skeleton(){return `<section class="skeleton" role="status" aria-label="正在计算成本"><div class="sk-cards">${Array.from({length:8},()=>'<div class="sk sk-card"></div>').join('')}</div><div class="sk sk-panel"></div><div class="sk sk-panel"></div></section>`}
let zoneDraft=null,zoneDirty=false,pickProvince=null,zoneSaving=false;
function zoneConfig(){return zoneDraft||(zoneDraft=JSON.parse(JSON.stringify(st.meta.config.regionGroups)))}
function zoneEditor(){const cfg=zoneConfig(),zs=activeZones(cfg),all=[...new Set([...Object.keys(st.meta.geo||{}).map(short),...activeZones().flatMap(g=>g.provinces)])],un=all.filter(p=>!zs.some(z=>z.provinces.includes(p)));const chip=p=>`<button class="pchip ${pickProvince===p?'picked':''}" draggable="true" data-prov="${esc(p)}" aria-pressed="${pickProvince===p}">${esc(p)}</button>`;const column=(z,i)=>`<div class="zcol ${i<0?'un':''}" data-zdrop="${i}"><div class="zcol-h">${i<0?'<b>未分组</b>':`<input class="zname" data-zname="${i}" value="${esc(z.name)}" aria-label="${esc(z.name)}分组名称">`}<span class="hint num">${z.provinces.length} 省</span><span class="zcol-act">${pickProvince&&!z.provinces.includes(pickProvince)?`<button class="linkbtn" data-zmove="${i}">移到这里</button>`:''}${i>=0?`<button class="linkbtn danger" data-zdel="${i}" ${zs.length<=1?'disabled':''} aria-label="删除${esc(z.name)}">删除</button>`:''}</span></div><div class="pchips">${z.provinces.map(chip).join('')||'<span class="hint">把省份拖到这里</span>'}</div></div>`;return `<section class="panel" id="zoneEditor"><div class="panel-head"><h2>流向区域分组</h2><span class="hint">把省份拖到别的分组，或点省份再点“移到这里”。</span><div class="row"><select id="zoneScheme" aria-label="区域方案">${Object.entries(cfg.schemes).map(([k,v])=>opt(k,k===cfg.active,v.name)).join('')}</select>${zoneDirty?'<span class="hint warn-t">有未保存的修改</span>':''}<button class="btn" data-zadd>新增分组</button><button class="btn" data-zreset>恢复默认分组</button><button class="btn primary" data-zsave ${!zoneDirty||zoneSaving?'disabled':''}>${zoneSaving?'正在保存…':'保存分组'}</button></div></div><div class="hmt-row"><label><input type="checkbox" role="switch" data-hmt ${cfg.showHMT?'checked':''}> 在成本分析和报价中显示港澳台</label><span class="hint">关闭时香港、澳门、台湾不参与任何测算；打开后可在测算范围中选择。保存分组后生效。</span></div><div class="zgrid">${zs.map(column).join('')}${column({name:'未分组',provinces:un},-1)}</div><p class="hint">删除分组后省份进入“未分组”，继续参与测算；港澳台仍服从上方开关。保存后全页生效。</p></section>`}
function moveProv(name,index){const zs=activeZones(zoneConfig());if(!name||index>=zs.length||index< -1)return;const available=[...Object.keys(st.meta.geo||{}).map(short),...zs.flatMap(z=>z.provinces)];if(!available.includes(name))return;zs.forEach(z=>z.provinces=z.provinces.filter(p=>p!==name));if(index>=0)zs[index].provinces.push(name);zoneDirty=true;pickProvince=null;render()}
function toggleZone(name){const off=new Set(st.excludeZones);if(off.has(name))off.delete(name);else {if(visibleZones().length-off.size<=1){toast('至少保留一个区域参与测算');return}off.add(name)}st.excludeZones=[...off];st.expandedZones=[];schedule('切换区域范围')}
let tipFor=null;
function hideTip(){const p=document.querySelector('#pop');if(p)p.hidden=true;if(tipFor)tipFor.setAttribute('aria-expanded','false');tipFor=null}
function showTip(b){if(tipFor===b){hideTip();return}hideTip();tipFor=b;const p=document.querySelector('#pop');p.textContent=b.dataset.tip;p.hidden=false;b.setAttribute('aria-expanded','true');const r=b.getBoundingClientRect(),w=Math.min(300,innerWidth-24);p.style.width=w+'px';p.style.left=Math.max(12,Math.min(r.left+scrollX-12,scrollX+innerWidth-w-12))+'px';p.style.top=(r.bottom+scrollY+8)+'px'}
document.addEventListener('click',e=>{const b=e.target.closest('.tip');if(b){e.stopImmediatePropagation();e.preventDefault();showTip(b)}else if(tipFor&&!e.target.closest('#pop'))hideTip()},true);
window.addEventListener('resize',hideTip);document.addEventListener('scroll',hideTip,true);document.addEventListener('keydown',e=>{if(e.key==='Escape')hideTip()});
document.addEventListener('click',async e=>{const t=e.target.closest('button');if(!t||t.disabled)return;
 if(t.dataset.skinpick){AP.skin=t.dataset.skinpick;applyAppearance();if(st.page==='data')captureSettings();render();return}
 if(t.dataset.modepick){AP.mode=t.dataset.modepick;applyAppearance();captureSettings();render();return}
 if(t.dataset.zone){toggleZone(t.dataset.zone);return}
 if(t.hasAttribute('data-zall')){st.excludeZones=[];schedule('恢复全部区域');return}
 if(t.hasAttribute('data-zremote')){st.excludeZones=visibleZones().filter(z=>z.name.includes('四区')||z.name.includes('五区')).map(z=>z.name);if(st.excludeZones.length===visibleZones().length)st.excludeZones.pop();schedule('排除偏远区域');return}
 if(t.dataset.expandzone){const n=t.dataset.expandzone;st.expandedZones=st.expandedZones.includes(n)?st.expandedZones.filter(x=>x!==n):[...st.expandedZones,n];render();return}
 if(t.dataset.prov){captureSettings();pickProvince=pickProvince===t.dataset.prov?null:t.dataset.prov;render();return}
 if(t.dataset.zmove!==undefined){moveProv(pickProvince,+t.dataset.zmove);return}
 if(t.dataset.zdel!==undefined){activeZones(zoneConfig()).splice(+t.dataset.zdel,1);zoneDirty=true;render();return}
 if(t.hasAttribute('data-zadd')){const zs=activeZones(zoneConfig());let n=1;while(zs.some(z=>z.name==='新区域'+n))n++;zs.push({name:'新区域'+n,provinces:[]});zoneDirty=true;render();return}
 if(t.hasAttribute('data-zreset')){zoneConfig().active='default';zoneConfig().schemes.default={name:'五区制',groups:[['一区','上海 江苏 浙江 安徽'],['二区','广东 福建 江西 山东 湖北 河北 北京 河南 天津 湖南'],['三区','山西 陕西 重庆 贵州 辽宁 广西 云南 四川 吉林 黑龙江'],['四区','内蒙古 甘肃 青海 宁夏 海南'],['五区','新疆 西藏'],['港澳台','香港 澳门 台湾']].map(([name,p])=>({name,provinces:p.split(' '),...(name==='港澳台'?{hmtGroup:true}:{})}))};zoneDirty=true;render();return}
 if(t.hasAttribute('data-zsave')){zoneSaving=true;render();try{await api('config',{...st.meta.config,regionGroups:zoneConfig(),expectedVersion:st.meta.dataVersion});await reloadMeta();zoneDraft=null;zoneDirty=false;pickProvince=null;if(cfgDraft)cfgDraft.regionGroups=JSON.parse(JSON.stringify(st.meta.config.regionGroups));toast('区域分组已保存，返回分析时自动重算')}catch(err){toast(err.message)}finally{zoneSaving=false;render()}}
});
document.addEventListener('change',e=>{const t=e.target;if(t.hasAttribute('data-hmt')){zoneConfig().showHMT=t.checked;zoneDirty=true;render()}if(t.dataset.zname!==undefined){activeZones(zoneConfig())[+t.dataset.zname].name=t.value;zoneDirty=true;render()}if(t.id==='zoneScheme'){zoneConfig().active=t.value;zoneDirty=true;render()}});
document.addEventListener('dragstart',e=>{const p=e.target.closest('[data-prov]');if(p)e.dataTransfer.setData('text/plain',p.dataset.prov)});
document.addEventListener('dragover',e=>{if(e.target.closest('[data-zdrop]'))e.preventDefault()});
document.addEventListener('drop',e=>{const z=e.target.closest('[data-zdrop]');if(z){e.preventDefault();moveProv(e.dataTransfer.getData('text/plain'),+z.dataset.zdrop)}});
window.addEventListener('beforeunload',e=>{if(zoneDirty){e.preventDefault();e.returnValue=''}});

;
// v34-report.js
'use strict';
// Capture once. Printing, pagination and Excel all use this exact response.
const exportState={open:false,capture:null,mods:[],rows:'all',rankRows:'top',excel:false};
function reportItems(r){return [['overview','概览指标','票数、日均、均重、成本与网点参考'],['insights','要点','当前重量和成本的主要发现'],['zones','区域分布','区域票量、成本与参与测算范围'],['history','历史对比','按月发件量、均重、单票及重量变化'],['dist','重量分布与成本','0.1kg 分布柱图与单票成本曲线'],['heat','流向 × 重量段成本','全部省份或区域与重量段同页比较'],['aftersales','退改附加成本','拦截、退件、退改及摊到每票'],r.mode==='multi'?['compare','客户对比','各客户主要指标与差异拆解']:null,r.mode==='all'?['ranking','客户排行','默认按当前排序取前 30 名']:null].filter(Boolean)}
function reportScope(r){return r.scope.excludeZones.length?'已排除 '+r.scope.excludeZones.join('、'):'全部流向'}
function reportName(r){return r.mode==='all'?'全部客户':r.customers.join('、')}
function reportDate(r){return `${r.scope.dateBasis==='handover'?'交件业务日':'寄件'} ${r.scope.start||'不限'} 至 ${r.scope.end||'不限'}`}
function exportPanel(){const r=exportState.capture.r;return `<aside class="drawer exp" role="dialog" aria-modal="true" aria-label="导出分析报告"><button class="xbtn" data-export="close" aria-label="关闭导出">×</button><div><div class="eyebrow">导出</div><h3>分析报告 · A4</h3><p class="hint">${esc(reportName(r))} · ${esc(reportDate(r))} · ${esc(reportScope(r))}</p></div><fieldset class="expf"><legend>包含的内容 <span class="hint">· 已选 ${exportState.mods.length} 项</span></legend>${reportItems(r).map(([k,l,d])=>`<label class="expi"><input type="checkbox" data-expmod="${k}" ${exportState.mods.includes(k)?'checked':''}><span><b>${l}</b><span class="hint">${d}</span></span></label>${k==='ranking'?`<label class="expi" style="margin-left:24px"><input type="checkbox" data-exprank ${exportState.rankRows==='all'?'checked':''} ${exportState.mods.includes('ranking')?'':'disabled'}><span>全部客户<span class="hint">勾选后包含全部 ${r.rankingCount||r.ranking.length} 个客户，报告与 Excel 同步</span></span></label>`:''}`).join('')}</fieldset><fieldset class="expf"><legend>流向表范围</legend><div class="seg"><button data-exprows="top" aria-pressed="${exportState.rows==='top'}">前 12 个流向，其余合并</button><button data-exprows="all" aria-pressed="${exportState.rows==='all'}">全部流向（一页）</button></div><p class="hint">省级、区域视图的流向 × 重量段固定排在一张横向 A4；市级流向过多时按页拆分并重复表头。</p></fieldset><label class="expi"><input type="checkbox" data-expexcel ${exportState.excel?'checked':''}><span><b>同时下载 Excel 数据</b><span class="hint">每个模块一张工作表，数值格式、冻结表头</span></span></label><p class="hint">内部分析，包含客户名称和成本。此报告与客户报价表用途不同。</p><div class="exp-act"><button class="btn" data-export="close">取消</button><button class="btn primary" data-export="make" ${exportState.mods.length?'':'disabled'}>生成报告</button></div></aside>`}
function drawExport(){document.querySelector('#drawer').hidden=false;document.querySelector('#drawer').innerHTML=exportPanel()}
function reportSections(){const {r,view}=exportState.capture,old={};for(const k of Object.keys(view)){old[k]=st[k];st[k]=view[k]}
 const prior=st.result,rendering=st.rendering,dec=st.dec;st.result=r;st.rendering=true;st.dec=null;
 try{const h=r.heat,rr={...r,heat:{...h,rows:exportState.rows==='all'||r.level==='zone'?h.allRows:h.allRows.slice(0,12),rest:exportState.rows==='all'||r.level==='zone'?null:h.topRest}};
  const builders={overview:()=>kpis(r),insights:()=>insights(r),zones:()=>zonePanel(r),history:()=>historyPanel(r),dist:()=>chartPanel(r),heat:()=>heatPanel(rr),aftersales:()=>afterSalesPanel(r),compare:()=>comparePage(r),ranking:()=>{const all=st.rankAll;st.rankAll=exportState.rankRows==='all';try{return ranking(r)}finally{st.rankAll=all}}};let no=0;const out=[];
  for(const [k,title] of reportItems(r)){if(!exportState.mods.includes(k))continue;no++;const box=document.createElement('div');box.innerHTML=builders[k]();
   // Preserve table labels, replace controls with their displayed text.
   box.querySelectorAll('.ztoggle,.sort').forEach(b=>b.replaceWith(document.createTextNode(b.textContent)));
   box.querySelectorAll('.zchip').forEach(b=>b.replaceWith(document.createTextNode(b.getAttribute('aria-pressed')==='true'?'参与':'已排除')));
   box.querySelectorAll('select').forEach(s=>s.replaceWith(document.createTextNode(s.selectedOptions[0]?.textContent||'')));
   box.querySelectorAll('button,.seg,input,.tip,.more').forEach(n=>n.remove());box.querySelectorAll('[tabindex]').forEach(n=>n.removeAttribute('tabindex'));
   box.querySelectorAll('[data-flow],[data-solo]').forEach(n=>{n.removeAttribute('data-flow');n.removeAttribute('data-solo')});
   const parts=k==='compare'?[...box.children].flatMap(n=>n.matches('.cmp-grid')?[...n.children]:[n]):[box];
   parts.forEach((part,i)=>{const label=part.querySelector('h2')?.textContent||title;part.querySelectorAll('h2').forEach(n=>n.remove());part.querySelectorAll('.panel').forEach(n=>n.classList.remove('panel'));part.classList.remove('panel');
    const section=document.createElement('section');section.className='rs';section.dataset.module=k;section.innerHTML=`<h3><span class="num">${no}</span>${esc(k==='compare'?label:k==='ranking'?title+(exportState.rankRows==='all'?' · 全部客户':' · 前 30 名'):title)}${i?' <span class="rsub">（续）</span>':''}</h3>`+part.innerHTML;
    if(k==='heat'){section.classList.add('matrix-report');section.querySelector('table').classList.add('rheat');section.querySelectorAll('tbody th.rowh').forEach(th=>{if(!th.closest('.zrow')&&!th.closest('.foot')&&r.level!=='city')th.textContent=short(th.textContent)});section.querySelectorAll('.dv').forEach(n=>n.textContent=n.textContent.startsWith('▲')?'▲':'▼')}
    out.push({node:section,land:true,one:k==='heat'&&r.level!=='city',matrix:k==='heat'});
   });
  }return out;
 }finally{for(const k of Object.keys(view))st[k]=old[k];st.result=prior;st.rendering=rendering;st.dec=dec}
}
function reportCover(r){const c=document.createElement('div');c.className='rcover';c.innerHTML=`<div class="eyebrow">出港成本分析报告 · 内部分析</div><h1>${esc(reportName(r))}</h1><dl class="rmeta"><div><dt>统计期间</dt><dd>${esc(reportDate(r))}</dd></div><div><dt>流向范围</dt><dd>${esc(reportScope(r))}</dd></div><div><dt>成本口径</dt><dd>正常件 · ${r.simulation.count?`账单费用 ± ${r.simulation.count} 项成本模拟`:'账单费用，未开模拟'}；退改另列</dd></div><div><dt>数据版本</dt><dd>${r.version} · ${esc(r.generatedAt)}</dd></div></dl><p class="rsub">金额单位：元/票或元；税口径未确认。历史对比按寄件月份；区域分布保留排除前数据供对照，其余按剩余流向计算。</p>`;return c}
function newReportSheet(land,r){const sh=document.createElement('article');sh.className='sheet land';sh.innerHTML=`<header class="rh"><span><b>出港成本分析</b> · ${esc(reportName(r))}</span><span>${esc(reportDate(r))}</span></header><div class="rbody"></div><footer class="rf"><span>内部分析 · ${esc(reportScope(r))} · ${r.simulation.count?'含模拟':'账单费用'}</span><span class="num"></span></footer>`;return sh}
function reportOverflows(sheet){const b=sheet.querySelector('.rbody'),last=b.lastElementChild;return !!last&&(last.getBoundingClientRect().bottom>b.getBoundingClientRect().bottom+1||b.scrollWidth>b.clientWidth+2)}
function paginateReport(parts,r){const box=document.querySelector('#report .sheets');let page=newReportSheet(true,r);box.append(page);page.querySelector('.rbody').append(reportCover(r));let count=1;
 const fresh=land=>{if(++count>300)throw Error('报告超过 300 页，请缩小分析范围');page=newReportSheet(land,r);box.append(page);return page.querySelector('.rbody')};
 for(const part of parts){let body=page.querySelector('.rbody');if(part.matrix||page.classList.contains('one'))body=fresh(true);if(part.one)page.classList.add('one');body.append(part.node);
  if(!reportOverflows(page))continue;
  if(body.children.length>1){part.node.remove();body=fresh(part.land);body.append(part.node)}
  if(!reportOverflows(page))continue;
  if(part.one)throw Error('流向矩阵超过单页空间，请调整重量段或分组数量');
  // Split a tall table in source order; each continuation keeps its thead.
  const original=part.node,table=original.querySelector('table'),tbody=table?.querySelector('tbody');
  if(!tbody||tbody.children.length<2)throw Error('报告模块超出 A4 页面，请缩小范围后重试');
  const rows=[...tbody.children],template=original.cloneNode(true);template.querySelector('tbody').replaceChildren();original.remove();let section=null,target=null;
  for(const row of rows){if(!section){section=template.cloneNode(true);if(body.children.length)body=fresh(part.land);if(row!==rows[0])section.querySelector('h3').insertAdjacentHTML('beforeend',' <span class="rsub">（续）</span>');body.append(section);target=section.querySelector('tbody')}
   target.append(row);if(reportOverflows(page)){row.remove();if(!target.children.length)throw Error('报告列太多或单行过高，请使用区域/省级视图或减少重量段');body=fresh(part.land);section=template.cloneNode(true);section.querySelector('h3').insertAdjacentHTML('beforeend',' <span class="rsub">（续）</span>');body.append(section);target=section.querySelector('tbody');target.append(row);if(reportOverflows(page))throw Error('报告单行超出 A4 页面')}
  }
 }
 const pages=[...box.children];pages.forEach((p,i)=>p.querySelector('.rf .num').textContent=`第 ${i+1} 页 / 共 ${pages.length} 页`);document.querySelector('#report .rtool .hint').textContent=`A4 · ${pages.length} 页 · 全横向 · 打印选择 A4、边距“无”、背景图形`;
}
async function makeReport(){if(exportState.making)return;exportState.making=true;const capture=exportState.capture;try{if(capture.r.lazy){const r=await api('analysis/details',{snapshotId:capture.r.snapshotId,part:'report'});if(!exportState.open||exportState.capture!==capture){exportState.making=false;return}capture.r=r}}catch(e){toast(e.message);exportState.making=false;return}exportState.making=false;const r=exportState.capture.r;hideTip();exportState.open=false;document.querySelector('#drawer').hidden=true;const rp=document.querySelector('#report');rp.hidden=false;rp.innerHTML=`<div class="rtool"><button class="btn" data-report="back">← 返回分析</button><span class="hint">正在分页…</span><span style="margin-left:auto;display:flex;gap:8px"><button class="btn" data-report="excel">下载 Excel 数据</button><button class="btn primary" data-report="print">打印 / 存为 PDF</button></span></div><div class="sheets"></div>`;document.body.classList.add('printing');document.querySelector('#app').inert=true;document.querySelector('.top').inert=true;
 try{paginateReport(reportSections(),r);rp.scrollTop=0;rp.querySelector('[data-report="back"]').focus();if(exportState.excel)downloadAnalysisExcel()}catch(e){rp.querySelector('.sheets').innerHTML=`<section class="panel" role="alert">${esc(e.message)}。可返回调整，或下载 Excel 数据。</section>`;rp.querySelector('[data-report="print"]').disabled=true;toast(e.message)}
}
async function downloadAnalysisExcel(){const r=exportState.capture.r;try{const response=await fetch('/api/v2/analysis/export',{method:'POST',headers:{'Content-Type':'application/json','X-App-Token':st.token},body:JSON.stringify({snapshotId:r.snapshotId,modules:exportState.mods,flowRows:exportState.rows,rankRows:exportState.rankRows,rankSort:exportState.capture.view.rankSort,rankAsc:exportState.capture.view.rankAsc})});if(!response.ok){const err=await response.json();throw Error(err.error||'导出失败')}const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download=`内部分析_${r.scope.start}_${r.scope.end}.xlsx`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(e){toast(e.message)}}
function closeReport(){document.querySelector('#report').hidden=true;document.querySelector('#report').replaceChildren();document.body.classList.remove('printing');document.querySelector('#app').inert=false;document.querySelector('.top').inert=false;document.querySelector('[data-export="open"]')?.focus()}
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b||b.disabled)return;
 if(b.dataset.export==='open'){if(st.pending||!st.result)return;closeDrawer(false);exportState.capture={r:JSON.parse(JSON.stringify(st.result)),view:{unit:st.unit,heatUnit:st.heatUnit,colorMode:st.colorMode,expandedZones:[...st.expandedZones],rankSort:st.rankSort,rankAsc:st.rankAsc}};exportState.rankRows='top';exportState.rows='all';exportState.mods=reportItems(st.result).map(x=>x[0]);exportState.open=true;drawExport();document.querySelector('[data-export="close"]').focus()}
 if(b.dataset.export==='close'){exportState.open=false;document.querySelector('#drawer').hidden=true;document.querySelector('[data-export="open"]')?.focus()}
 if(b.dataset.export==='make')makeReport();
 if(b.dataset.exprows){exportState.rows=b.dataset.exprows;drawExport()}
 if(b.dataset.report==='back')closeReport();if(b.dataset.report==='excel')downloadAnalysisExcel();if(b.dataset.report==='print')window.print();
});
document.addEventListener('change',e=>{const t=e.target;if(t.dataset.expmod){exportState.mods=t.checked?[...exportState.mods,t.dataset.expmod]:exportState.mods.filter(k=>k!==t.dataset.expmod);drawExport();document.querySelector(`[data-expmod="${t.dataset.expmod}"]`)?.focus()}if(t.hasAttribute('data-exprank'))exportState.rankRows=t.checked?'all':'top';if(t.hasAttribute('data-expexcel'))exportState.excel=t.checked});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!document.querySelector('#report').hidden){closeReport();e.stopImmediatePropagation()}});

document.addEventListener('keydown',e=>{if(e.key!=='Tab')return;const root=!document.querySelector('#report').hidden?document.querySelector('#report'):exportState.open?document.querySelector('#drawer'):null;if(!root)return;const items=[...root.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href]')].filter(x=>x.getClientRects().length);if(!items.length)return;const a=items[0],b=items[items.length-1];if(e.shiftKey&&document.activeElement===a){e.preventDefault();b.focus()}else if(!e.shiftKey&&document.activeElement===b){e.preventDefault();a.focus()}});

;
// v34.js
'use strict';
const {esc,fmt,int,pct,signed,short}=V3;
const $=s=>document.querySelector(s), CC=['var(--k1)','var(--k2)','var(--k3)','var(--k4)','var(--k5)','var(--k6)','var(--k7)','var(--k8)'];
const st={excludeZones:[],expandedZones:[],sim:null,simOpen:false,comparePrevious:false,heatUnit:'share',rankSort:'n',rankAsc:false,page:'analysis',sel:'all',unit:'share',colorMode:'cost',level:'province',expand:false,showAll:false,rankAll:false,openGroup:null,
  dateBasis:'send',start:null,end:null,preset:'previous',analysis:null,compareBand:null,result:null,meta:null,token:null,pending:false,drawer:null,dec:null};
let S={steps:[]},seq=0,timer=null,controller=null,drawerSeq=0,decSeq=0,returnFocus=null;
const isAll=()=>st.rendering?st.result.mode==='all':st.sel==='all';
const selName=()=>isAll()?'全部客户':(st.rendering?st.result.customers:st.sel).join('、');
const colorOf=name=>CC[Math.max(0,(isAll()?[]:st.rendering?st.result.customers:st.sel).indexOf(name))%CC.length];
const qty=(n,total)=>st.unit==='share'?pct(total?n/total:0):int(n)+'票';
const bandOf=(bands,u)=>bands.findIndex(b=>b.units.includes(u*2));
const opt=(v,sel,label)=>`<option value="${esc(v)}" ${sel?'selected':''}>${esc(label)}</option>`;
const btn=(text,attrs='')=>`<button class="btn" ${attrs}>${text}</button>`;
function request(){return {compact:true,excludeZones:[...st.excludeZones],customers:st.sel==='all'?'all':[...st.sel],dateBasis:st.dateBasis,start:st.start,end:st.end,level:st.level,expand:st.expand,showAll:false,analysis:st.analysis,compareBand:st.compareBand,simulation:st.sim,comparePrevious:st.comparePrevious}}
async function api(route,data,signal){
  const r=await fetch('/api/v2/'+route,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json','X-App-Token':st.token},body:data===undefined?undefined:JSON.stringify(data),signal});
  const x=await r.json();if(!r.ok||x.error)throw Error(x.error||'读取失败');return x;
}
function toast(msg){const t=document.createElement('div');t.className='toast';t.setAttribute('role','status');t.textContent=msg;document.body.append(t);setTimeout(()=>t.remove(),3000)}
function setBusy(b){st.pending=b;$('#app').setAttribute('aria-busy',b);$('#results')?.classList.toggle('pending',b);if($('#busy'))$('#busy').textContent=b?'正在更新…':''}
function scopeBar(){
  const chips=isAll()?'':st.sel.map(n=>`<span class="chip on"><span class="dot" style="background:${colorOf(n)}"></span>${esc(n)}<button class="x" data-remove="${esc(n)}" aria-label="移除${esc(n)}">×</button></span>`).join('');
  const imported=V3.importRange(st.meta.lastImport,st.dateBasis), dateLabel=imported?.start&&imported?.end?`本次导入 ${imported.start.slice(5).replace('-','/')}–${imported.end.slice(5).replace('-','/')}`:'本次导入';
  return `<section class="panel" id="scope"><div class="scope">
    <div class="field" style="flex:1 1 560px"><span class="lab">客户（输入名称添加，可多选；选 2 个以上自动对比，最多 8 个）</span><div class="chips"><button class="chip all" data-cust="all" aria-pressed="${isAll()}">全部客户 · 网点汇总</button>${chips}<div class="combo"><input id="custSearch" class="search" aria-label="搜索添加客户" role="combobox" aria-expanded="false" aria-controls="custList" aria-autocomplete="list" placeholder="+ 搜索添加客户（共 ${st.meta.customers.length} 个）" autocomplete="off"><div id="custList" class="combo-list" role="listbox" hidden></div></div></div></div>
    <div class="field"><span class="lab">${st.dateBasis==='send'?'日期（寄件）':V3.dateLabel(st.dateBasis,st.meta.features?.handoverBusinessDay)}</span><div class="seg">${[['import',dateLabel],['week','近 7 天'],['month','本月'],['previous','上月'],['custom','自定义']].map(([k,v])=>`<button data-date="${k}" aria-pressed="${st.preset===k}" ${k==='import'&&st.dateBasis==='send'?`title="另有 ${int(imported?.outside||0)} 票寄件日期在此范围外"`:''} ${k==='import'&&!imported?'disabled':''}>${esc(v)}</button>`).join('')}</div></div>
    <div class="field"><span class="lab">对比期间</span><div class="seg"><button data-period="off" aria-pressed="${!st.comparePrevious}">不对比</button><button data-period="on" aria-pressed="${st.comparePrevious}">对比 ${esc(st.result?.previous?.range?.label||'上期')}</button></div></div>
    <div class="field"><span class="lab">附加费用与政策</span><button class="btn ${simCount()?'primary':''}" data-sim="open">成本模拟 · 已开 ${simCount()} 项</button></div>
    <div class="field"><span class="lab">流向</span><div class="seg"><button data-level="zone" aria-pressed="${st.level==='zone'}">区域</button><button data-level="province" aria-pressed="${st.level==='province'}">省</button><button data-level="city" aria-pressed="${st.level==='city'}">市</button></div></div>
    ${st.preset==='custom'?`<div class="date-fields"><label>日期口径 <select id="dateBasis">${opt('send',st.dateBasis==='send','寄件日期')}${opt('handover',st.dateBasis==='handover',V3.dateLabel('handover',st.meta.features?.handoverBusinessDay))}</select></label><label>开始日期 <input type="date" id="start" value="${st.start||''}"></label><label>结束日期 <input type="date" id="end" value="${st.end||''}"></label></div>`:''}
  ${zoneControls()}<div class="field act-field">${btn('导出报告',`data-export="open" ${st.pending||!st.result?'disabled':''}`)}</div><span id="busy" role="status" style="margin-left:auto">${st.pending?'正在更新…':''}</span></div></section>${zoneBanner()}`;
}
function kpis(r){const t=r.overview,b=r.baseBands,main=b.filter(x=>x.cls==='main'),name=r.mode==='all'?'全部客户':r.customers.join('、'),{diff,price,extra}=V3.referenceParts(t),reference=r.mode==='all'?'当前即网点汇总':'账单同结构参考 + 当前选择的模拟调整';return `<section class="kpi-band"><div class="kpis analysis-kpis kpi-main">
  <div class="kpi"><span class="k">正常件票数</span><span class="v num">${int(t.n)}</span><span class="s">${esc(name.length>14?r.customers.length+' 个客户':name)}</span>${periodDelta(r,'n')}</div>
  <div class="kpi"><span class="k">日均发件量 ${tip(r.mode==='all'?'正常件票数 ÷ 范围内自然天数':'正常件票数 ÷ 有正常件发货的寄件天数；多客户使用日期并集')}</span><span class="v num">${int(t.daily)}</span><span class="s">${r.mode==='all'?`统计 ${t.calendarDays} 天`:`发货 ${t.activeDays}/${t.calendarDays} 天`}</span>${periodDelta(r,'daily')}</div>
  <div class="kpi"><span class="k">平均结算重量</span><span class="v num">${fmt(t.avgW)}<small style="font-size:13px"> kg</small></span><span class="s">主要重量 ${esc(main.map(x=>x.label).join('、')||'—')}</span>${periodDelta(r,'avgW')}</div>
  <div class="kpi"><span class="k">单票实际成本 ${tip('账单费用 ± 已开启的成本模拟项，元/票')}</span><span class="v num">${fmt(t.avg,3)}</span><span class="s">${r.simulation.count?`账单 ${fmt(t.avgSystem,3)} · 模拟 ${signed(r.simulation.per)}（${r.simulation.count} 项）`:'账单费用，未开模拟项'}</span>${periodDelta(r,'avg')}</div>
  </div><div class="kpis analysis-kpis kpi-sub"><div class="kpi"><span class="k">网点同类件成本 ${tip('按所选客户的目的地和重量分布，换成网点平均价算出的单票成本')}</span><span class="v num">${fmt(t.eq,3)}</span><span class="s">${esc(reference)}</span></div>
  <div class="kpi"><span class="k">比网点贵 / 便宜 ${tip('同目的地、同重量的件，比网点平均每票贵（+）或便宜（−）多少，按账单费用比较')}</span><span class="v num ${t.gap>.005?'up':t.gap<-.005?'down':''}">${signed(t.gap)}</span><span class="s" title="按账单费用比较，不受成本模拟影响">${isAll()?'—':t.gap==null?'无参考':t.gap>.005?'比网点贵':t.gap<-.005?'比网点便宜':'与网点持平'} · 账单口径</span></div>
  <div class="kpi"><span class="k">退改率 ${tip('拦截、退件、退改的票数 ÷ 全部票数')}</span><span class="v num ${r.aftersales.rate>r.aftersales.network.rate*1.5?'up':''}">${pct(r.aftersales.rate)}</span><span class="s">网点 ${pct(r.aftersales.network.rate)} · 附加 ${fmt(r.aftersales.per,3)} 元/票</span></div>
  <div class="kpi alert"><span class="k">刚超档的件 ${tip('重量刚超过当前计费档位 0.1kg 以内的件；控制在档内每票能少付的钱')}</span><span class="v num">${int(t.critical.n)}</span><span class="s">占 ${pct(t.n?t.critical.n/t.n:0)} · 多付约 ${fmt(t.critical.save)} 元</span>${periodDelta(r,'criticalShare')}</div></div></section>`}
function insights(r){const t=r.overview,top=r.baseBands.slice().sort((a,b)=>b.n-a.n)[0],worst=r.insights.largestStep,gap=t.gap;return `<section class="insights">
  <div class="ins"><b>发货集中在 ${esc(top?.label||'—')}</b><p>占 ${pct(top?.share)}；${r.baseBands.filter(b=>b.cls==='main').length>1?'两个主要重量并存，报价需分段关注。':'报价和成本优化都以这一段为准。'}</p></div>
  <div class="ins warn"><b>${worst?`刚超 ${worst.step}kg 档的件 ${int(worst.n)} 票`:'当前未识别到计费档位'}</b><p>${worst?`${worst.step.toFixed(1)}–${(worst.step+.1).toFixed(1)}kg 的件比 ${worst.step}kg 以内的每票多付约 ${fmt(worst.n?worst.save/worst.n:0,3)} 元，本期合计约 ${fmt(worst.save)} 元。`:'可在设置中调整档位。'}</p></div>
  <div class="ins"><b>${isAll()?'网点汇总视图':Math.abs(gap)<.03?'价格与网点基本一致':gap>0?'同格单价高于网点':'同格单价低于网点'}</b><p>${isAll()?`共 ${int(t.n)} 票，另有 ${int(r.excluded.n)} 票特殊件已隔离。`:`同流向同重量段账单单价差 ${signed(gap)} 元/票。已隔离特殊件 ${int(r.excluded.n)} 票。`}</p></div></section>`}
function chartPanel(r){const p=r.settings.parameters;return `<section class="panel"><div class="panel-head"><h2>重量分布与成本</h2><span class="hint">每根柱 0.1kg；颜色表示所在重量段的集中度</span>${chartControls()}</div>${distChart([{n:0,fee:0},...r.distribution],[{n:0,fee:0},...r.networkDistribution],r.baseBands)}<div class="legend">${[['main','主要重量 ≥'+pct(p.mainShare,0)],['sub','次要段 '+pct(p.secondaryShare,0)+'–'+pct(p.mainShare,0)],['low','少量 <'+pct(p.secondaryShare,0)]].map(([c,l])=>`<span><i class="sw" style="background:var(--c-${c})"></i>${l}</span>`).join('')}<span><i class="ln" style="border-color:var(--r2)"></i>单票成本</span>${isAll()?'':'<span><i class="ln" style="border-color:var(--ink3);border-top-style:dashed"></i>网点同重量成本</span>'}<span><i class="ln" style="border-color:var(--warn);border-top-style:dashed"></i>计费档位</span></div></section>`}
function heatPanel(r){if(r.level==='zone')return zoneHeatPanel(r);const h=r.heat,bs=r.bands,allN=r.overview.n,cuts=h.cuts;
  const head=`<tr><th class="rowh">${r.level==='city'?'城市':'流向'}</th>${bs.map(b=>`<th><div class="bandh"><span class="bl num">${esc(b.label)}</span><span class="bw num">均重 ${fmt(b.avgW)}kg</span><span class="cls ${b.cls}">${{main:'主要',sub:'次要',low:'少量'}[b.cls]} ${pct(b.share)}</span><div class="bar"><i style="width:${b.share/(Math.max(...bs.map(x=>x.share))||1)*100}%;background:var(--c-${b.cls})"></i></div></div></th>`).join('')}<th>合计</th></tr>`;
  const cell=(c,b,j,flow)=>{if(!c.n)return '<td class="c empty">·</td>';const color=st.colorMode==='network'&&!isAll()?c.comparisonClass:c.costClass;
    const tip=`${int(c.n)}票 · 单票 ${fmt(c.avg,3)} 元 · 网点同类件成本 ${fmt(c.ref.avg,3)} 元${c.deviation!=null?' · 账单差 '+pct(c.deviation):''}${c.n<30?' · 样本少于30票':''}`;
    return `<td class="c ${color} ${b.cls==='main'?'mainc':''}" tabindex="0" data-flow="${esc(flow)}" data-band="${j}" title="${esc(tip)}" aria-label="${esc(flow+' '+b.label+' '+tip)}">${c.badge&&st.colorMode==='cost'?`<span class="dv ${c.badge}">${c.badge==='up'?'▲':'▼'}${Math.abs(c.deviation*100).toFixed(0)}%</span>`:''}<span class="a num">${fmt(c.avg,3)}</span><span class="m num">${cellSub(c,allN)}</span></td>`};
  const row=(x,aggregate=false)=>`<tr${aggregate&&x.flow==='全部流向'?' class="foot"':''}><th class="rowh">${esc(aggregate?x.flow==='其余'?`其余 ${x.members.length} 个`:x.flow:r.level==='city'?x.flow.split('/')[1]:x.flow)}${!aggregate&&r.level==='city'?`<small class="hint"> ${esc(short(x.flow.split('/')[0]))}</small>`:''}</th>${x.cells.map((c,j)=>aggregate?`<td class="c n0 aggregate"><span class="a num">${fmt(c.avg,3)}</span><span class="m num">${cellSub(c,allN)}</span></td>`:cell(c,bs[j],j,x.flow)).join('')}<td class="tot num">${fmt(x.total.avg,3)}<br><span class="hint">${cellSub(x.total,allN)}</span></td></tr>`;
  const rng=[`< ${fmt(cuts[0])}`,`${fmt(cuts[0])}–${fmt(cuts[1])}`,`${fmt(cuts[1])}–${fmt(cuts[2])}`,`${fmt(cuts[2])}–${fmt(cuts[3])}`,`≥ ${fmt(cuts[3])}`];
  const legend=st.colorMode==='cost'||isAll()?`<span>格子颜色 = 单票成本区间（元）：</span>${rng.map((t,i)=>`<span><i class="sw" style="background:var(--s${i+1});border:1px solid var(--line)"></i><span class="num">${esc(t)}</span></span>`).join('')}${isAll()?'':'<span><b class="up">▲</b>/<b class="down">▼</b> 账单价比网点同结构贵 / 便宜 ≥2%</span>'}`:`<span>格子颜色 = 与网点同流向、同重量分布的账单费用比较：</span>${[['g2','便宜 ≥5%'],['g1','便宜 2–5%'],['n0','持平 ±2%'],['r1','贵 2–5%'],['r2','贵 ≥5%']].map(([c,t])=>`<span><i class="sw" style="background:var(--${c})"></i>${t}</span>`).join('')}`;
  return `<section class="panel" id="heatPanel"><div class="panel-head"><h2>${r.mode==='multi'?'合并后的重量段 × 流向成本':'重量段 × 流向成本'}</h2><span class="hint">单票实际成本（元）· 小字为${{share:'票量占比',count:'票数',weight:'均重'}[st.heatUnit]} · 斜纹格不足 30 票 · 点格子看构成</span><span style="margin-left:auto;display:flex;gap:8px;flex-wrap:wrap">${heatControls()}${isAll()?'':`<div class="seg"><button data-color="cost" aria-pressed="${st.colorMode==='cost'}">颜色：成本区间</button><button data-color="network" aria-pressed="${st.colorMode==='network'}">颜色：与网点比</button></div>`}${r.level==='province'?btn(st.expand?'收起主要重量':'主要重量细分到 0.1kg','id="expandBtn"'):''}</span></div><div class="tw"><table class="heat"><thead>${head}</thead><tbody>${h.rows.map(x=>row(x)).join('')}${h.rest?row(h.rest,true):''}${row(h.total,true)}</tbody></table></div>${h.rowCount>12?`<button class="more" id="moreBtn">${st.showAll?'只看前 12 个':`展开全部 ${h.rowCount} 个${r.level==='city'?'城市':'流向'}`}</button>`:''}<div class="legend" style="margin-top:8px">${legend}</div></section>`;
}
function similarPanel(r){const me=V3.normalize(r.comparison[0]),g=r.cluster;if(!me.name)return '';const rows=[{customer:{name:me.name,key:me.key,topProvinces:me.topProv,n:me.t.n,avg:me.avg,gap:me.gap,criticalN:me.cr.n},similarity:null},...r.similar];
  return `<section class="panel"><div class="panel-head"><h2>相似客户</h2><span class="hint">按重量分布和流向分布的重合度自动挑选 · ${esc(me.name)} 属于「${esc(me.key)}」${g?`（${g.members.length} 个客户）`:''}</span><span style="margin-left:auto;display:flex;gap:8px">${btn('与前 4 个相似客户对比','data-cmpsim="true"')}${g?.members.length>1?btn('对比整组',`data-cmpgroup="${esc(g.key)}"`):''}</span></div><div class="tw"><table class="list"><thead><tr>${['客户','相似度','分群','主要流向','票数','账单单票','比网点贵 / 便宜','刚超档'].map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${rows.map(({customer:s,similarity},i)=>`<tr ${i?`class="click" tabindex="0" data-solo="${esc(s.name)}"`:''}><td>${esc(s.name)}${i?'':'（当前）'}</td><td class="num">${pct(similarity)}</td><td>${esc(s.key)}</td><td>${esc(s.topProvinces.map(short).join('、'))}</td><td class="num">${int(s.n)}</td><td class="num">${fmt(s.avg,3)}</td><td class="num">${signed(s.gap)}</td><td class="num">${pct(s.criticalN/s.n)}</td></tr>`).join('')}</tbody></table></div></section>`;
}
function flags(s){return (s.tags||[]).map(t=>`<span class="badge ${t==='比网点贵'?'up':t==='比网点便宜'?'down':'warn'}">${t}</span>`).join(' ')}
function spark(counts){const mx=Math.max(...counts.slice(0,30),1),n=counts.reduce((a,b)=>a+b,0)||1;let g='';for(let u=0;u<30;u++){const v=counts[u]||0,h=v/mx*34;g+=`<rect class="b-${v/n>=.1?'main':v/n>=.03?'sub':'low'}" x="${u*10+1}" y="${36-h}" width="8" height="${Math.max(h,.5)}" rx="1"/>`}for(const [s] of S.steps)if(s<=3)g+=`<line x1="${s*100}" x2="${s*100}" y1="0" y2="38" stroke="var(--warn)" stroke-dasharray="2 2"/>`;return `<svg class="spark" viewBox="0 0 300 38" preserveAspectRatio="none" aria-hidden="true">${g}</svg>`}
function groupsPanel(r){if(r.lazy&&!r.groupStats)return groupSummaries(r);const c=r.clusters,stats=new Map((r.groupStats||r.ranking).map(s=>[s.name,s]));const member=s=>`<tr class="click" tabindex="0" data-solo="${esc(s.name)}"><td>${esc(s.name)}</td><td class="num">${int(s.total.n)}</td><td class="num">${fmt(s.avg,3)}</td><td class="num ${s.gap>=.03?'worst':s.gap<=-.03?'best':''}">${signed(s.gap)}</td><td class="num">${pct(s.critical.n/s.total.n)}</td><td class="num ${s.returnRate>=r.aftersales.network.rate*1.5?'worst':''}">${pct(s.returnRate)}</td><td>${flags(s)}</td></tr>`;
  const card=g=>`<div class="grp"><div class="grp-h"><b>${esc(g.wlabel)}</b><span>·</span><b>${esc(g.flabel)}</b><span class="hint">${g.members.length} 个客户 · ${int(g.n)} 票 · 占网点 ${pct(g.n/r.network.n)} · 平均 ${fmt(g.fee/g.n,3)} 元/票</span></div>${spark(g.distribution)}<div class="tw"><table class="list"><thead><tr>${['客户','票数','账单单票','比网点贵 / 便宜','刚超档','退改率',''].map(t=>`<th>${t}</th>`).join('')}</tr></thead><tbody>${(st.openGroup===g.key?g.members:g.members.slice(0,5)).map(n=>member(stats.get(n))).join('')}</tbody></table></div><div style="display:flex;gap:8px;flex-wrap:wrap">${btn('对比这一组'+(g.members.length>8?'（前 8 个）':''),`data-cmpgroup="${esc(g.key)}"`)}${g.members.length>5?`<button class="more" data-opengroup="${esc(g.key)}">${st.openGroup===g.key?'收起':'查看全部 '+g.members.length+' 个'}</button>`:''}</div></div>`;
  return `<section class="panel"><div class="panel-head"><h2>同类客户分群</h2><span class="hint">按主要重量段 + 流向偏向自动归类（某大区占比比网点高 6 个百分点以上记为“偏该大区”）· 期间票量 ≥ ${r.settings.parameters.minGroupN} 的 ${c.eligibleCustomers} 个客户分为 ${c.groups.length} 组，另有 ${c.unique.length} 个结构独特；${c.smallCustomers} 个小客户（合计 ${int(c.smallN)} 票）未参与</span></div><div class="groups">${c.groups.map(card).join('')}</div>${c.unique.length?`<div style="margin-top:12px"><div class="eyebrow">结构独特的客户</div><div class="tw"><table class="list"><tbody>${c.unique.map(g=>{const s=stats.get(g.members[0]);return `<tr class="click" tabindex="0" data-solo="${esc(s.name)}"><td>${esc(s.name)}</td><td>${esc(g.key)}</td><td class="num">${int(s.total.n)} 票</td><td class="num">${fmt(s.avg,3)} 元</td><td>${flags(s)}</td></tr>`}).join('')}</tbody></table></div></div>`:''}<p class="hint">“比网点贵 / 便宜”已排除重量和流向结构差异：同组客户结构相近，单票成本差异主要看这一列和“刚超档”。</p></section>`;
}

function decompositionPanel(d,list){
  if(!d)return '<p class="hint">所选范围没有可分解的数据</p>';
  const bn=d.b==='network'?'网点整体':d.b;
  const wf=waterfall([{label:bn.slice(0,6),v:d.avgB,kind:'base'},{label:'重量结构',v:d.weight,kind:'delta'},{label:'流向结构',v:d.flow,kind:'delta'},{label:'同格单价',v:d.price,kind:'delta'},{label:d.a.slice(0,6),v:d.avgA,kind:'end'}]);
  return `<div class="panel-head" style="margin-bottom:6px"><h2>差在哪里</h2><select id="cmpA" class="btn" aria-label="分解客户">${list.map(s=>opt(s.name,s.name===d.a,s.name)).join('')}</select><span class="hint">对比</span><select id="cmpB" class="btn" aria-label="对比基准">${opt('network',d.b==='network','网点整体')}${list.filter(s=>s.name!==d.a).map(s=>opt(s.name,s.name===d.b,s.name)).join('')}</select></div>${wf}<p class="hint">${esc(d.a)} 比 ${esc(bn)} ${d.delta>=0?'贵':'便宜'} ${fmt(Math.abs(d.delta),3)} 元/票：重量结构 ${signed(d.weight)}，流向结构 ${signed(d.flow)}，同格单价 ${signed(d.price)}。以上按账单费用分解，不受成本模拟影响。</p>`;
}
function comparePage(r){
  const list=r.comparison.map(V3.normalize),ext=k=>({min:Math.min(...list.map(s=>s[k])),max:Math.max(...list.map(s=>s[k]))});
  const mark=(v,{min,max})=>min===max?'':v===min?'best':v===max?'worst':'';
  const ea=ext('avg'),eg=ext('gap'),cr=list.map(s=>s.cr.n/s.t.n),ec={min:Math.min(...cr),max:Math.max(...cr)};
  const table=`<div class="tw"><table class="list"><thead><tr>${['客户','票数','日均','均重 kg','主要重量','账单单票','同结构网点价','比网点贵 / 便宜','刚超档','可省','退改率','附加成本/票'].map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${list.map(s=>`<tr class="click" tabindex="0" data-solo="${esc(s.name)}"><td><span class="dot" style="background:${colorOf(s.name)}"></span>${esc(s.name)}</td><td class="num">${int(s.t.n)}</td><td class="num">${int(s.daily)}</td><td class="num">${fmt(s.avgW)}</td><td>${esc(s.main)}</td><td class="num ${mark(s.avg,ea)}">${fmt(s.avg,3)}</td><td class="num">${fmt(s.eq,3)}</td><td class="num ${mark(s.gap,eg)}">${signed(s.gap)}</td><td class="num ${mark(s.cr.n/s.t.n,ec)}">${pct(s.cr.n/s.t.n)}</td><td class="num">${fmt(s.cr.save)}</td><td class="num ${s.returnRate>=r.aftersales.network.rate*1.5?'worst':''}">${pct(s.returnRate)}</td><td class="num">${fmt(s.returnPer,3)}</td></tr>`).join('')}</tbody></table></div><p class="hint">点一行只看该客户。绿色 = 组内最好，红色 = 组内最差。比网点贵 / 便宜、可省金额按账单费用比较。</p>`;
  const weights=new Map();for(const s of list)for(const [p,v] of Object.entries(s.flowShares))weights.set(p,(weights.get(p)||0)+v*s.t.n);
  const top=[...weights].sort((a,b)=>b[1]-a[1]).slice(0,6).map(x=>x[0]);
  const mix=`<table class="list"><tbody>${list.map(s=>`<tr><td style="width:120px"><span class="dot" style="background:${colorOf(s.name)}"></span>${esc(s.name.slice(0,6))}</td><td><div class="mix">${top.map((p,k)=>`<i style="width:${(s.flowShares[p]||0)*100}%;background:var(--f${k+1})" title="${esc(p)} ${pct(s.flowShares[p]||0)}"></i>`).join('')}<i style="width:${Math.max(0,1-top.reduce((a,p)=>a+(s.flowShares[p]||0),0))*100}%;background:var(--line)" title="其他"></i></div></td></tr>`).join('')}</tbody></table><div class="legend" style="margin-top:6px">${top.map((p,k)=>`<span><i class="sw" style="background:var(--f${k+1})"></i>${esc(p)}</span>`).join('')}<span><i class="sw" style="background:var(--line)"></i>其他</span></div>`;
  const cells=r.sameCells.map(s=>new Map(s.heat.rows.map(x=>[x.flow,x.cells[0]]))),net=new Map(r.sameNetwork.rows.map(x=>[x.flow,x.cells[0]])),counts=new Map();
  for(const c of cells)for(const [p,v] of c)counts.set(p,(counts.get(p)||0)+v.n);
  const flows=[...counts].sort((a,b)=>b[1]-a[1]).slice(0,10).map(x=>x[0]),values=flows.flatMap(p=>cells.map(m=>m.get(p)).filter(c=>c?.n>=30).map(c=>c.avg)).sort((a,b)=>a-b),cuts=[.2,.4,.6,.8].map(q=>values[Math.min(values.length-1,Math.floor(q*values.length))]??0);
  const same=`<div class="tw"><table class="heat"><thead><tr><th class="rowh">${esc(r.sameBands[r.sameBand].label)}</th>${list.map(s=>`<th title="${esc(s.name)}"><span class="dot" style="background:${colorOf(s.name)}"></span>${esc(s.name.slice(0,6))}</th>`).join('')}<th>网点</th></tr></thead><tbody>${flows.map(p=>`<tr><th class="rowh">${esc(p)}</th>${cells.map(m=>{const c=m.get(p);return !c?.n?'<td class="c empty">·</td>':`<td class="c ${c.n<30?'small':'s'+(1+cuts.filter(v=>c.avg>=v).length)}" style="cursor:default" title="${int(c.n)}票 · 均重 ${fmt(c.avgW)}kg · 网点同类件成本 ${fmt(c.ref.avg,3)} 元">${c.badge?`<span class="dv ${c.badge}">${c.badge==='up'?'▲':'▼'}${Math.abs(c.deviation*100).toFixed(0)}%</span>`:''}<span class="a num">${fmt(c.avg,3)}</span><span class="m num">${int(c.n)}票 · ${fmt(c.avgW)}kg</span></td>`}).join('')}<td class="tot num" style="text-align:center">${fmt(net.get(p)?.avg,3)}</td></tr>`).join('')}</tbody></table></div>`;
  return `<section class="panel"><div class="panel-head"><h2>客户对比</h2><span class="hint">${list.length} 个客户 · 成本与票数为正常件；退改率以全部票数计算</span></div>${table}</section><section class="panel"><div class="panel-head"><h2>重量分布对比</h2><span class="hint">${st.unit==='share'?'按各自票量占比比较':'按各自票量比较'}</span>${chartControls()}<div class="legend">${list.map(s=>`<span><i class="ln" style="border-color:${colorOf(s.name)}"></i>${esc(s.name)}</span>`).join('')}</div></div>${overlayChart(list)}</section><section class="cmp-grid"><div class="panel" id="decomposition">${decompositionPanel(st.dec||r.decomposition,list)}</div><div class="panel"><div class="panel-head"><h2>流向结构</h2><span class="hint">前 6 大流向占比</span></div>${mix}</div></section><section class="panel"><div class="panel-head"><h2>同流向同重量段，谁贵谁便宜</h2><select id="cmpBand" class="btn" aria-label="对比重量段">${r.sameBands.map((b,i)=>opt(i,i===r.sameBand,b.label)).join('')}</select></div>${same}<p class="hint">格子颜色为账单成本区间（深 = 贵）；▲/▼ 按账单费用与网点同流向、相同 0.1kg 重量分布折算后比较；“网点”列为该段账单费用平均。</p></section>`;
}
function render(){return NumericInputs.preserve(renderPage)}
function renderPage(){
  document.querySelectorAll('.tabs button').forEach(b=>b.setAttribute('aria-selected',b.dataset.page===st.page));
  const shown=st.result?.scope||st;$('#topMeta').textContent=`数据：${V3.dateLabel(shown.dateBasis,st.meta?.features?.handoverBusinessDay)} ${shown.start||'不限'} – ${shown.end||'不限'}${st.result?' · '+int(st.result.network.n)+' 票正常件':''}`;
  if(!st.meta)return;
  $('#versionTag').textContent='v'+st.meta.version;$('#versionTag').title=`发布 ${st.meta.released} · 数据版本 ${st.meta.dataVersion}`;
  renderAppearance();hideTip();
  if(st.page==='quote'){renderQuote();return}
  if(st.page==='data'){$('#app').innerHTML=settingsPage()+appearancePanel()+zoneEditor();renderAppearance();return}
  renderAppearance();hideTip();
  const r=st.result;
  if(st.pending&&!st.error){$('#app').innerHTML=scopeBar()+skeleton();setBusy(true);return}
  st.rendering=!!r;
  let body=st.error?`<section class="panel" role="alert"><h2>分析未完成</h2><p>${esc(st.error)}</p>${btn('重新加载','id="retry"')}</section>`:!r?skeleton():!r.overview.n?`<section class="panel"><h2>这个范围没有正常件</h2><p class="hint">请恢复区域或调整客户、日期范围。特殊件已隔离 ${int(r.excluded.n)} 票。</p>${btn('恢复全部区域','data-zall')}${btn('换日期','data-date="custom"')}</section>${r.mode!=='all'?afterSalesPanel(r):''}${historyPanel(r)}`:r.mode==='multi'?kpis(r)+insights(r)+zonePanel(r)+historyPanel(r)+comparePage(r)+afterSalesPanel(r)+heatPanel(r):r.mode==='single'?kpis(r)+insights(r)+zonePanel(r)+historyPanel(r)+afterSalesPanel(r)+similarPanel(r)+chartPanel(r)+heatPanel(r):kpis(r)+insights(r)+zonePanel(r)+historyPanel(r)+groupsPanel(r)+chartPanel(r)+heatPanel(r)+ranking(r);
  st.rendering=false;
  $('#app').innerHTML=scopeBar()+`<div id="results" class="${st.pending?'pending':''}">${body}</div>`;
  $('#app').setAttribute('aria-busy',st.pending);renderSim();
}
function closeDrawer(restore=true){if(typeof exportState!=='undefined')exportState.open=false;st.simOpen=false;if($('#simulator'))$('#simulator').hidden=true;++drawerSeq;st.drawer=null;$('#drawer').hidden=true;$('#drawer').innerHTML='';if(restore&&returnFocus?.isConnected)returnFocus.focus()}
const responseCache=new Map();
const responseKey=req=>JSON.stringify(req);
function cacheResponse(req,r){const key=responseKey(req);responseCache.delete(key);responseCache.set(key,r);while(responseCache.size>16)responseCache.delete(responseCache.keys().next().value)}
function schedule(reason){const started=performance.now(),generation=++seq;clearTimeout(timer);controller?.abort();++decSeq;if(!st.simOpen)closeDrawer(false);st.error=null;setBusy(true);render();timer=setTimeout(()=>loadAnalysis(reason,started,generation),0)}
async function expandModule(part){const captured=st.result;if(!captured)return;try{const detail=await api('analysis/details',{snapshotId:captured.snapshotId,part});if(st.result!==captured)return false;Object.assign(captured,detail);if(part==='ranking')captured.rankingLoaded=true;render();return true}catch(e){toast(e.message);return false}}
function groupSummaries(r){const c=r.clusters;return `<section class="panel"><div class="panel-head"><h2>同类客户分群</h2><span class="hint">${c.eligibleCustomers} 个客户 · ${c.groups.length} 组 · ${c.unique.length} 个结构独特 · ${c.smallCustomers} 个小客户未参与</span></div><div class="groups">${[...c.groups,...c.unique].map(g=>`<div class="grp"><div class="grp-h"><b>${esc(g.wlabel)}</b><b>${esc(g.flabel)}</b><span class="hint">${g.memberCount} 个客户 · ${int(g.n)} 票 · ${fmt(g.fee/g.n,3)} 元/票</span></div>${spark(g.distribution)}<button class="more" data-loadgroups>查看成员</button></div>`).join('')}</div></section>`}

async function loadAnalysis(reason,started,generation){
  controller=new AbortController();const httpStart=performance.now();
  try{const query=request(),cached=responseCache.get(responseKey(query));const r=cached||await api('analysis',query,controller.signal);cacheResponse(query,r);let d=null;if(r.mode==='multi'&&st.decA&&r.comparison.some(x=>x.name===st.decA))d=await api('analysis/decompose',{...request(),a:st.decA,b:r.comparison.some(x=>x.name===st.decB)?st.decB:'network'},controller.signal);const received=performance.now();if(generation!==seq)return;
    st.result=st.showAll?{...r,heat:{...r.heat,rows:r.heat.allRows,rest:null}}:r;st.dec=d;st.pending=false;st.error=null;S.steps=r.steps.map(x=>[x.step,x.jump]);render();
    requestAnimationFrame(()=>requestAnimationFrame(()=>{if(generation!==seq)return;Object.assign($('#app').dataset,{action:reason,navigationMs:reason==='首次打开'?performance.now().toFixed(1):'',interactionMs:(performance.now()-started).toFixed(1),requestMs:(received-httpStart).toFixed(1),renderMs:(performance.now()-received).toFixed(1),completed:String(generation),mode:r.mode,level:r.level,baseWidth:String(r.settings.parameters.baseWidth)});}));
  }catch(e){if(generation!==seq||e.name==='AbortError')return;st.pending=false;st.error=e.message;render()}
}
function choose(names){st.sel=names;st.decA=null;st.decB=null;st.compareBand=null;st.expand=false;st.showAll=false;st.rankAll=false;schedule('切换客户')}
function addCustomer(input){const name=input.value.trim();if(!name)return;if(!st.meta.customers.some(c=>c.name===name)){toast('请从匹配结果中选择完整客户名称');return}try{choose(V3.selected(st.sel,name))}catch(e){toast(e.message)}}
async function changeDecomposition(){const g=++decSeq,ag=seq,a=$('#cmpA').value,b=$('#cmpB').value===a?'network':$('#cmpB').value;st.decA=a;st.decB=b;$('#decomposition').setAttribute('aria-busy','true');
  try{const d=await api('analysis/decompose',{...request(),a,b});if(g!==decSeq||ag!==seq)return;st.dec=d;$('#decomposition').innerHTML=decompositionPanel(d,st.result.comparison);$('#decomposition').setAttribute('aria-busy','false')}
  catch(e){if(g===decSeq&&ag===seq){$('#decomposition').setAttribute('aria-busy','false');toast(e.message)}}
}
async function openCell(target,page=1){
  const g=++drawerSeq;let scope;
  if(target){returnFocus=target;const b=st.result.bands[+target.dataset.band];scope={...request(),level:st.level==='zone'?'province':st.level,flow:target.dataset.flow,band:{lo:b.lo,hi:b.hi},label:b.label,customerLabel:selName()};st.drawer={scope}}
  else scope=st.drawer.scope;
  const host=$('#drawer');host.hidden=false;host.innerHTML='<aside class="drawer" role="dialog" aria-label="格子明细"><button class="xbtn" id="xbtn" aria-label="关闭明细">×</button><p role="status">正在读取明细…</p></aside>';$('#xbtn').focus();
  try{const d=await api('analysis/cell',{...scope,page,pageSize:20});if(g!==drawerSeq)return;st.drawer={scope,data:d};renderDrawer();$('#xbtn').focus()}
  catch(e){if(g!==drawerSeq)return;host.innerHTML=`<aside class="drawer" role="dialog" aria-label="格子明细"><button class="xbtn" id="xbtn" aria-label="关闭明细">×</button><p role="alert">${esc(e.message)}</p></aside>`;$('#xbtn').focus()}
}
function renderDrawer(){const {scope,data:d}=st.drawer,t=d.total,ref=d.ref,n=t.n||1,comp=[['派费',t.disp/n,ref.disp,'var(--c-main)'],['中转费',t.tran/n,ref.tran,'var(--c-sub)'],['面单加价',t.mf/n,ref.mf,'var(--c-low)']];
  $('#drawer').innerHTML=`<aside class="drawer" role="dialog" aria-label="格子明细"><button class="xbtn" id="xbtn" aria-label="关闭明细">×</button><div><div class="eyebrow">${esc(scope.customerLabel)}</div><h3>${esc(scope.flow.replace('/',' · '))} · ${esc(scope.label)}</h3></div><div class="kpis" style="grid-template-columns:repeat(3,minmax(0,1fr))"><div class="kpi"><span class="k">票数</span><span class="v num" style="font-size:20px">${int(t.n)}</span><span class="s">均重 ${fmt(t.avgW)} kg</span></div><div class="kpi"><span class="k">单票实际成本</span><span class="v num" style="font-size:20px">${fmt(t.avg,3)}</span></div><div class="kpi"><span class="k">网点同类件成本</span><span class="v num" style="font-size:20px">${fmt(ref.avg,3)}</span><span class="s">账单差 ${signed(d.gap)}</span></div></div><div><div class="eyebrow">成本构成（元/票）</div><div class="stack" style="margin:6px 0">${comp.map(([k,v,,col])=>`<i style="width:${Math.max(0,t.avgSystem?v/t.avgSystem*100:0)}%;background:${col}" title="${k} ${fmt(v,3)}"></i>`).join('')}</div><div class="kv"><span class="hint">项目</span><span class="hint">本格</span><span class="hint">网点账单</span>${comp.map(([k,v,m,col])=>`<span><i class="sw" style="background:${col}"></i>${k}</span><span class="num">${fmt(v,3)}</span><span class="num">${fmt(m,3)}</span>`).join('')}${d.simulationLines.map(x=>`<span>模拟：${esc(x.name)}</span><span class="num">${signed(x.per)}</span><span class="hint">—</span>`).join('')}<span>实际合计</span><span class="num">${fmt(t.avg,3)}</span><span class="num">${fmt(ref.avg,3)}</span></div></div>${d.cities.length?`<div><div class="eyebrow">城市明细（前 8 个）</div><div class="kv"><span class="hint">城市</span><span class="hint">票数</span><span class="hint">单票 / 网点</span>${d.cities.slice(0,8).map(c=>`<span>${esc(c.flow.split('/')[1])}</span><span class="num">${int(c.n)}</span><span class="num">${fmt(c.avg,3)} / ${fmt(c.ref.avg,3)}</span>`).join('')}</div></div>`:''}<p class="hint">网点参考按本格相同的 0.1kg 重量分布折算。网点列仅为账单费用；账单差不受成本模拟影响。</p><div><div class="eyebrow">运单列表 · ${int(d.totalRows)} 票</div><div class="tw"><table class="list"><thead><tr><th>运单</th><th>重量 kg</th><th>实际成本</th><th></th></tr></thead><tbody>${d.rows.map(x=>`<tr><td class="num">${esc(x.waybill)}</td><td class="num">${fmt(x.weight)}</td><td class="num">${fmt(x.actual,3)}</td><td><button class="more" data-raw="${x.id}">原始</button></td></tr>`).join('')}</tbody></table></div><div class="row">${btn('上一页',`data-detailpage="${d.page-1}" ${d.page===1?'disabled':''}`)}<span class="hint">${d.page} / ${Math.max(1,Math.ceil(d.totalRows/d.pageSize))} 页</span>${btn('下一页',`data-detailpage="${d.page+1}" ${d.page*d.pageSize>=d.totalRows?'disabled':''}`)}</div><section id="rawRecord" aria-live="polite"></section></div><div><div class="eyebrow">报价范围</div><p class="hint">${esc(scope.customerLabel)} · ${esc(scope.flow)} · ${esc(scope.label)} · ${esc(d.quoteScope.start||'不限')} 至 ${esc(d.quoteScope.end||'不限')}</p>${btn('按此范围报价','id="rangeQuote"')}</div></aside>`;
}
async function rawRecord(id){const g=drawerSeq;const slot=$('#rawRecord');slot.dataset.id=id;slot.innerHTML='<p role="status">正在读取原始记录…</p>';try{const d=await api('raw?id='+encodeURIComponent(id));if(g!==drawerSeq||$('#rawRecord')?.dataset.id!==id)return;slot.innerHTML=`<h4>原始记录</h4><p class="hint">文件：${esc(d.source.file)}<br>工作表：${esc(d.source.sheet)} · 第 ${int(d.source.line)} 行</p><dl class="raw-fields">${Object.entries(d.raw).map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>`;slot.scrollIntoView({block:'nearest'})}catch(e){if(g===drawerSeq)slot.textContent=e.message}}

document.addEventListener('click',async e=>{const t=e.target.closest('button,[data-flow],tr[data-solo]');if(!t||t.disabled||(st.pending&&t.closest('#results')))return;
  if(t.id==='xbtn'){closeDrawer();return}
  if(t.dataset.page){if(st.page==='data'&&!captureSettings())return;if(t.dataset.page==='quote'){enterQuote();return}if(t.dataset.page===st.page)return;closeDrawer(false);st.page=t.dataset.page;if(st.page==='analysis'&&st.dirtyWidth){st.dirtyWidth=false;schedule('改重量段')}else render();return}
  if(t.id==='retry'){schedule('重试');return}
  if(t.dataset.cust==='all'){choose('all');return}
  if(t.dataset.solo){choose([t.dataset.solo]);window.scrollTo({top:0});return}
  if(t.dataset.remove){const names=st.sel.filter(n=>n!==t.dataset.remove);choose(names.length?names:'all');return}
  if(t.dataset.period){st.comparePrevious=t.dataset.period==='on';schedule('切换对比期间');return}
  if(t.dataset.heatunit){st.heatUnit=t.dataset.heatunit;render();return}
  if(t.hasAttribute('data-loadgroups')){await expandModule('groups');return}
  if(t.dataset.ranksort){if(st.result.lazy&&!st.result.rankingLoaded&&!await expandModule('ranking'))return;st.rankAsc=st.rankSort===t.dataset.ranksort?!st.rankAsc:false;st.rankSort=t.dataset.ranksort;render();return}
  if(t.dataset.unit){st.unit=t.dataset.unit;render();return}
  if(t.dataset.color){st.colorMode=t.dataset.color;render();return}
  if(t.dataset.date){st.preset=t.dataset.date;if(st.preset==='custom'){render();return}Object.assign(st,V3.dateRange(st.preset,V3.today(st.meta?.features?.handoverBusinessDay?st.dateBasis:'send'),V3.importRange(st.meta.lastImport,st.dateBasis)));schedule('切换日期');return}
  if(t.dataset.level){st.level=t.dataset.level;if(st.level!=='province')st.expand=false;st.showAll=false;schedule('切换流向层级');return}
  if(t.dataset.width){st.analysis={...st.analysis,baseWidth:+t.dataset.width};st.dirtyWidth=true;render();return}
  if(t.id==='expandBtn'){st.expand=!st.expand;schedule('细分主要重量');return}
  if(t.id==='moreBtn'){st.showAll=!st.showAll;st.result={...st.result,heat:{...st.result.heat,rows:st.showAll?st.result.heat.allRows:st.result.heat.allRows.slice(0,12),rest:st.showAll?null:st.result.heat.topRest}};render();return}
  if(t.id==='rankMore'){if(st.result.lazy&&!st.result.rankingLoaded&&!await expandModule('ranking'))return;st.rankAll=!st.rankAll;render();return}
  if(t.dataset.opengroup){st.openGroup=st.openGroup===t.dataset.opengroup?null:t.dataset.opengroup;render();return}
  if(t.dataset.cmpsim){choose([st.sel[0],...st.result.similar.slice(0,4).map(x=>x.customer.name)]);window.scrollTo({top:0});return}
  if(t.dataset.cmpgroup){const g=st.result.cluster?.key===t.dataset.cmpgroup?st.result.cluster:st.result.clusters?.groups.find(x=>x.key===t.dataset.cmpgroup);if(g){choose(g.members.slice(0,8));window.scrollTo({top:0})}return}
  if(t.dataset.flow){openCell(t);return}
  if(t.dataset.detailpage){openCell(null,+t.dataset.detailpage);return}
  if(t.dataset.raw){rawRecord(t.dataset.raw);return}
});
document.addEventListener('change',e=>{const t=e.target;
  if(t.id==='cmpBand'){st.compareBand=+t.value;schedule('切换对比重量段');return}
  if(t.id==='cmpA'||t.id==='cmpB'){changeDecomposition();return}
  if(['start','end','dateBasis'].includes(t.id)){const start=$('#start').value,end=$('#end').value,dateBasis=$('#dateBasis').value;if(!start||!end||start>end){toast('请选择完整日期范围，结束日期不能早于开始日期');return}Object.assign(st,{start,end,dateBasis});schedule('切换日期口径')}
});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('#drawer').hidden){closeDrawer();return}if((e.key==='Enter'||e.key===' ')&&e.target.matches('[data-flow],tr[data-solo]')){e.preventDefault();e.target.click()}});
async function init(){const started=performance.now();try{
  let [boot,meta,r]=await Promise.all([fetch('/api/bootstrap').then(r=>r.json()),api('meta'),api('analysis/default')]);for(let i=0;meta.dataVersion!==r.version&&i<3;i++)[meta,r]=await Promise.all([api('meta'),api('analysis/default')]);if(meta.dataVersion!==r.version)throw Error('数据正在更新，请稍后重试');st.token=boot.token;st.meta=meta;st.sim=JSON.parse(JSON.stringify(meta.config.simulation));st.analysis={...meta.analysisConfig};S.steps=meta.steps.map(x=>[x.step,x.jump]);Object.assign(st,{start:r.scope.start,end:r.scope.end});
  if(meta.dataVersion===r.version)cacheResponse(request(),r);
  st.pending=true;render();await loadAnalysis('首次打开',started,++seq);
}catch(e){$('#app').innerHTML=`<section class="panel" role="alert"><h2>无法读取本地账单</h2><p>${esc(e.message)}</p><button class="btn" onclick="location.reload()">重新加载</button></section>`}}
let revisionBusy=false;
async function refreshRevision(){if(revisionBusy||!st.meta||document.hidden||importBusy||settingsBusy||zoneSaving||st.pending||st.page!=='analysis'||cfgDraft||zoneDirty||st.simOpen)return;revisionBusy=true;try{const d=await api('revision');if(JSON.stringify(d.revision)!==JSON.stringify(st.meta.responseRevision)){responseCache.clear();await reloadMeta();if(st.page==='analysis')schedule('数据已更新')}}catch{}finally{revisionBusy=false}}
window.addEventListener('focus',refreshRevision);setInterval(refreshRevision,30000);
init();

;
// v34-quote.js
'use strict';
const qs={includeAddon:false,target:'',basis:'selection',subsidyMode:'target',subsidyType:'',mode:'percent',value:20,overrides:{},rows:new Set(),cols:new Set(),all:false,result:null,pending:false,error:null,saved:null,list:[],generation:0,bulkMode:'fixed',bulkValue:.1};
let qt=null;
function ownQuote(){return V3.ownQuoteCustomer(qs.saved?qs.result.request.customers:st.sel,qs.target,qs.basis)}
function defaultQuoteSubsidy(){qs.subsidyMode='target';qs.subsidyType=''}
function quoteRequest(){return {...request(),schema:'v3',target:qs.target,basis:qs.basis,subsidyType:qs.subsidyType,subsidyMode:qs.subsidyMode,mode:qs.mode,value:qs.value,overrides:{...qs.overrides},flows:qs.flows,includeAddon:qs.includeAddon}}
function enterQuote(scope){
  NumericInputs.clear('quote.');
  const context=JSON.stringify({customers:st.sel,start:st.start,end:st.end,dateBasis:st.dateBasis,version:st.meta.dataVersion,simulation:st.sim,excludeZones:st.excludeZones});
  if(!scope&&qs.context===context&&qs.result){closeDrawer(false);st.page='quote';render();refreshQuotes();return}qs.context=context;
  qs.overrides={};qs.saved=null;qs.result=null;qs.rows.clear();qs.cols.clear();qs.flows=scope?[scope.flow]:undefined;
  qs.includeAddon=false;qs.target=Array.isArray(st.sel)&&st.sel.length===1?st.sel[0]:'';qs.basis='selection';defaultQuoteSubsidy();
  if(scope)for(let i=0;i<7;i++){const b=[[0,.5],[.5,1],[1,1.5],[1.5,2],[2,3],[3,5],[5,Infinity]][i];if(b[1]>scope.band.lo&&b[0]<(scope.band.hi??Infinity))qs.cols.add(i)}
  if(scope)qs.rows.add(scope.flow);
  closeDrawer(false);st.page='quote';refreshQuotes();updateQuote();
}
async function refreshQuotes(){try{qs.list=(await api('quotes?schema=v3')).quotes;if(st.page==='quote'&&!document.activeElement?.matches('input'))render()}catch(e){toast(e.message)}}
function updateQuote(){const g=++qs.generation;clearTimeout(qt);qs.pending=true;qs.error=null;qs.saved=null;render();qt=setTimeout(async()=>{try{const r=await api('quote/preview',quoteRequest());if(g!==qs.generation)return;qs.result=r;qs.pending=false;render()}catch(e){if(g!==qs.generation)return;qs.pending=false;qs.error=e.message;render()}},200)}
function renderQuote(){
 const active=document.activeElement,identity=active?.id||null,key=active?.dataset.q,start=active?.selectionStart,end=active?.selectionEnd;
 $('#app').innerHTML=quotePage();const next=key?[...document.querySelectorAll('[data-q]')].find(x=>x.dataset.q===key):identity?document.getElementById(identity):null;
 if(next&&!next.disabled&&next.tagName==='INPUT'){next.focus({preventScroll:true});try{if(start!=null)next.setSelectionRange(start,end)}catch{}}
}
function quotePage(){const r=qs.result,t=r?{...r.summary,editedCells:r.summary.editedCells+(r.remoteStats?.editedCells||0),lossCells:r.summary.lossCells+(r.remoteStats?.lossCells||0)}:null,disabled=qs.pending||!!qs.saved,selection=V3.quoteSelection(r,qs.rows,qs.cols);
  const td=x=>x.price==null?'<td class="none">—</td>':`<td class="${x.source!=='own'?'ref':''} ${x.edited?'edited':''} ${x.loss?'loss':''}"><input class="num" data-q="${esc(x.key)}" data-number="quote.cell.${esc(x.key)}" data-committed="${qs.overrides[x.key]??x.price}" value="${esc(qs.pending&&qs.overrides[x.key]!==undefined?qs.overrides[x.key]:x.price.toFixed(1))}" inputmode="decimal" aria-label="${esc(x.key)} 报价" ${qs.saved?'disabled':''}><small class="num">成本 ${fmt(x.cost,3)} · ${x.source==='province'?'<span class="tag">参考网点</span>':x.source==='nation'?'<span class="tag">参考全网</span>':int(x.n)+'票'}</small></td>`;
  return `<section class="panel"><fieldset class="qbar" ${qs.saved?'disabled':''}>
    <div class="field"><label for="qTarget">报价客户</label><input id="qTarget" type="text" list="quoteCustomers" value="${esc(qs.target)}" placeholder="选择或输入新客户"><datalist id="quoteCustomers">${st.meta.customers.map(x=>`<option value="${esc(x.name)}"></option>`).join('')}</datalist></div>
    <div class="field"><label for="qBasis">成本依据</label><select id="qBasis">${opt('selection',qs.basis==='selection','当前选择：'+(qs.saved?(r.request.customers==='all'?'全部客户':r.request.customers.join('、')):selName()).slice(0,16))}${opt('network',qs.basis==='network','网点整体')}</select></div>
    <div class="field"><span class="lab">退改附加成本</span><label class="check"><input type="checkbox" id="qAddon" ${qs.includeAddon?'checked':''} ${r?.addonPer==null?'disabled':''}> 附加本客户 +${fmt(r?.addonPer,3)}/票</label></div>
    <div class="field"><label for="qSub">客户补贴</label><select id="qSub">${qs.saved&&qs.subsidyMode==='existing'?opt('__existing__',true,'沿用现有补贴'):''}${opt('',qs.subsidyMode!=='existing'&&!qs.subsidyType,'无补贴')}${[...new Set(st.meta.config.subsidyPolicies.map(p=>p.type))].map(s=>opt(s,qs.subsidyMode!=='existing'&&s===qs.subsidyType,s)).join('')}</select>${!ownQuote()?'<small class="hint">新客户请选择适用补贴</small>':''}</div>
    <div class="field"><span class="lab">加价方式</span><div class="seg">${['percent','fixed'].map(m=>`<button data-qmode="${m}" aria-pressed="${qs.mode===m}">${m==='percent'?'成本 + %':'每票 + 元'}</button>`).join('')}</div></div>
    <div class="field"><label for="qVal">${qs.mode==='percent'?'加价 %':'加价 元/票'}</label><input id="qVal" type="text" inputmode="decimal" data-number="quote.value" data-committed="${qs.value}" value="${qs.value}"></div>
    <div class="qactions">${btn('恢复建议价',`id="qReset" ${Object.keys(qs.overrides).length?'':'disabled'}`)}${btn('保存报价',`id="qSave" ${qs.pending||qs.error||!r?'disabled':''}`)}<button class="btn primary" id="qExport" ${qs.pending||qs.error||!r?'disabled':''}>确认并导出 Excel</button></div>
    <span class="hint" role="status">${qs.pending?'正在更新报价…':''}</span></fieldset>${qs.flows?`<p class="hint">抽屉所选流向：${esc(qs.flows.join('、'))} · 已勾选对应重量段，报价表保留完整重量网格 · ${btn('恢复全部流向','id="qAllFlows"')}</p>`:''}
    ${qs.saved?`<div class="row"><b>已保存报价 · ${esc(r.target)}</b>${btn('返回新报价','id="qNew"')}<a class="btn primary" href="/api/v2/quotes/export?schema=v3&id=${qs.saved}" download>再次导出 Excel</a></div>`:''}</section>
    ${qs.error?`<section class="panel" role="alert">${esc(qs.error)} ${btn('恢复建议价并重算','id="qReset"')}</section>`:''}
    ${r?`<div class="quote-results ${qs.pending?'pending':''}"><section class="kpis quote-kpis">
    <div class="kpi"><span class="k">按样本结构估算收入</span><span class="v num">${fmt(t.revenue)}</span><span class="s">${int(t.pricedN)} 票 × 对应报价${t.missingN?' · '+int(t.missingN)+' 票缺参考价':''}</span></div>
    <div class="kpi"><span class="k">估算毛利</span><span class="v num">${fmt(t.profit)}</span><span class="s">毛利率 ${pct(t.margin)}</span></div><div class="kpi"><span class="k">人工改价</span><span class="v num">${t.editedCells}</span><span class="s">黄框为已改</span></div><div class="kpi ${t.lossCells?'alert':''}"><span class="k">低于成本</span><span class="v num">${t.lossCells}</span><span class="s">红框格子需要确认</span></div></section>
    <section class="panel"><div class="panel-head"><h2>报价表 · ${esc(r.target||'未命名客户')}</h2><span class="hint">元/票，保留 1 位小数 · 深色标签为主要重量 · 斜纹格：样本不足 30 票，参考网点或全网同重量段</span></div>
    <fieldset class="bulkbar" ${disabled?'disabled':''}><span>批量调整 · 已选 ${selection.rows} 行 × ${selection.cols} 列 · 将修改 ${selection.cells.length} 格</span>${btn('全选行列','id="qSelectAll"')}${btn('清空选择','id="qClear"')}<select id="qBulkMode" aria-label="批量调整方式">${[['fixed','+ 元'],['percent','+ %'],['set','设为']].map(([v,label])=>opt(v,qs.bulkMode===v,label)).join('')}</select><input id="qBulkValue" type="text" inputmode="decimal" data-number="quote.bulk" data-committed="${qs.bulkValue}" value="${esc(qs.bulkValue)}" step="0.1" aria-label="批量调整数值">${btn('应用到选中格','id="qApply"')}</fieldset>
    <div class="tw"><table class="quote"><thead><tr><th style="text-align:left">流向</th>${r.bands.map((b,i)=>`<th><label><input type="checkbox" data-qcol="${i}" aria-label="选择 ${esc(b.label)} 列" ${qs.cols.has(i)?'checked':''} ${disabled?'disabled':''}> <span class="num">${b.label}</span></label><br><span class="cls ${b.cls}">${pct(b.share)}</span></th>`).join('')}</tr></thead><tbody>${quoteZoneRows(r.rows,td,disabled,r)}</tbody></table></div>
    ${remoteQuoteTable(r,td,disabled)}<p class="hint">导出给客户的 Excel 只有流向、重量段和报价，不含成本、毛利和其他客户信息。成本含当前已开启的模拟项；增值附加由本页勾选控制，只摊入一次。客户补贴为额外指定扣减，默认无补贴。</p></section></div>`:'<section class="panel" role="status">正在核算报价…</section>'}
    <section class="panel"><div class="panel-head"><h2>已保存的报价</h2></div>${qs.list.length?`<div class="tw"><table class="list"><thead><tr><th>客户</th><th>保存时间</th><th>数据版本</th><th></th></tr></thead><tbody>${qs.list.map(x=>`<tr><td>${esc(x.target)}</td><td>${esc(x.created.replace('T',' ').slice(0,19))}</td><td>${x.version}</td><td>${btn('查看',`data-viewquote="${x.id}"`)} <a class="btn" href="/api/v2/quotes/export?schema=v3&id=${x.id}" download>再次导出</a></td></tr>`).join('')}</tbody></table></div>`:'<p class="hint">还没有保存过报价。保存后在这里按客户查看、再次导出。</p>'}</section>`;
}
async function saveQuote(exportNow){if(!NumericInputs.validate($('#app')))return;if(qs.pending||qs.error||!qs.result)return;const result=qs.result;if(!result.target){toast('请填写报价客户');$('#qTarget').focus();return}
  qs.pending=true;render();try{const d=await api('quotes',{schema:'v3',expectedVersion:result.version,request:result.request});qs.saved=d.id;qs.result=d.result;toast('报价已保存');await refreshQuotes();if(exportNow){const a=document.createElement('a');a.href='/api/v2/quotes/export?schema=v3&id='+d.id;a.download='报价.xlsx';a.click()}}catch(e){qs.error=e.message;toast(e.message)}finally{qs.pending=false;render()}}
document.addEventListener('click',async e=>{const t=e.target.closest('button');if(!t||t.disabled)return;
  if(t.id==='rangeQuote'){enterQuote(st.drawer.scope);return}
  if(t.dataset.qmode){qs.mode=t.dataset.qmode;updateQuote();return}
  if(t.id==='qReset'){NumericInputs.clear('quote.cell.');qs.overrides={};updateQuote();return}
  if(t.id==='qAllFlows'){qs.flows=undefined;qs.overrides={};updateQuote();return}
  if(t.id==='qNew'){qs.saved=null;qs.overrides={};updateQuote();return}
  if(t.id==='qSave'||t.id==='qExport'){await saveQuote(t.id==='qExport');return}
  if(t.id==='moreQ'){qs.all=!qs.all;render();return}
  if(t.id==='qSelectAll'){qs.rows=new Set([...qs.result.rows,...(qs.result.remoteRows||[])].map(x=>x.flow));qs.cols=new Set([0,1,2,3,4,5,6]);render();return}
  if(t.id==='qClear'){qs.rows.clear();qs.cols.clear();render();return}
  if(t.id==='qApply'){if(!NumericInputs.validate($('#app')))return;const value=Number($('#qBulkValue').value),mode=$('#qBulkMode').value,selection=V3.quoteSelection(qs.result,qs.rows,qs.cols);if(!Number.isFinite(value)||(!qs.rows.size&&!qs.cols.size)){toast('先勾选流向或重量段，并填写调整数值');return}const over={...qs.overrides};for(const c of selection.cells){const v=mode==='set'?value:mode==='percent'?c.price*(1+value/100):c.price+value;if(v<0||v>1000000){toast('调整后报价须在 0–1000000 元之间');return}over[c.key]=Number(v.toFixed(8))}if(!selection.cells.length){toast('选中格没有可报价样本');return}qs.overrides=over;updateQuote();return}
  if(t.dataset.viewquote){try{const d=await api('quotes?schema=v3&id='+t.dataset.viewquote);++qs.generation;clearTimeout(qt);qs.pending=false;qs.error=null;qs.saved=d.id;qs.result=d.result;Object.assign(qs,{includeAddon:false,subsidyMode:'target',...d.result.request,rows:new Set(),cols:new Set()});render();window.scrollTo({top:0})}catch(e){toast(e.message)}}
});
document.addEventListener('change',e=>{const t=e.target;
  if(t.id==='qBulkMode'){qs.bulkMode=t.value;return}
  if(t.dataset.qrow){t.checked?qs.rows.add(t.dataset.qrow):qs.rows.delete(t.dataset.qrow);render();return}
  if(t.dataset.qcol!==undefined){t.checked?qs.cols.add(+t.dataset.qcol):qs.cols.delete(+t.dataset.qcol);render();return}
  if(t.id==='qAddon'){qs.includeAddon=t.checked;updateQuote();return}
  if(t.id==='qSub'){qs.subsidyMode=t.value==='__existing__'?'existing':'target';qs.subsidyType=t.value==='__existing__'?'':t.value;updateQuote();return}
  const map={qTarget:'target',qBasis:'basis'};if(map[t.id]){qs[map[t.id]]=t.id==='qVal'?Number(t.value):t.value;if(t.id==='qTarget'||t.id==='qBasis')defaultQuoteSubsidy();updateQuote()}
});

// Text target drafts and numeric drafts have separate lifecycles.
document.addEventListener('input',e=>{const t=e.target;if(t.id!=='qTarget'&&!t.dataset.number?.startsWith('quote.'))return;
 if(t.id==='qBulkValue')return;
 ++qs.generation;clearTimeout(qt);qs.pending=true;qs.saved=null;$('.quote-results')?.classList.add('pending');$('#qSave')?.setAttribute('disabled','');$('#qExport')?.setAttribute('disabled','');
 if(t.id==='qTarget'){qs.target=t.value;defaultQuoteSubsidy();qt=setTimeout(()=>updateQuote(),500)}
});
document.addEventListener('numericcommit',e=>{const t=e.target;if(t.id==='qBulkValue'){qs.bulkValue=e.detail;return}if(t.id==='qVal')qs.value=e.detail;else if(t.dataset.q)qs.overrides[t.dataset.q]=e.detail;else return;updateQuote()});

;
// v34-settings.js
'use strict';
let cfgDraft=null,settingsBusy=false,importBusy=false,importProgress='',pendingRows=null,pendingOffset=0;
const clone=x=>JSON.parse(JSON.stringify(x));
function draft(){if(!cfgDraft){cfgDraft=clone(st.meta.config);if(!cfgDraft.adjustments.length)cfgDraft.adjustments=[{id:'operation',name:'操作成本',amount:0,sign:1,enabled:false},{id:'management',name:'管理成本',amount:0,sign:1,enabled:false}]}return cfgDraft}
function formValue(selector,key,type='string'){return [...document.querySelectorAll(selector)].map(row=>Object.fromEntries(Object.entries(key).map(([k,sel])=>{const input=row.querySelector(sel);return [k,input.type==='checkbox'?input.checked:!!input.dataset.number?Number(input.value):input.value]})))}
function captureSettings(){if(!$('#configForm'))return true;if(!NumericInputs.validate($('#configForm')))return false;const c=draft();c.conflictRule=$('#conflictRule').value;c.analysis={...st.analysis,baseWidth:Number($('#baseWidth').dataset.value),mainShare:Number($('#mainShare').value)/100,secondaryShare:Number($('#secondaryShare').value)/100,tailShare:Number($('#tailShare').value)/100,minGroupN:Number($('#minGroupN').value),steps:$('#autoSteps').checked?null:formValue('.stepedit',{step:'[name=step]',jump:'[name=jump]'})};
 c.excludeRules=[...document.querySelectorAll('[data-rule]:checked')].flatMap(x=>x.dataset.rule==='invalid'?['no_fee','no_dest']:[x.dataset.rule]);return true;
}
function settingsPage(){const c=draft(),p={...st.analysis,...c.analysis},ex=st.meta.lastImport,groups=st.meta.importGroups||[],rules=[['invalid','没有金额 / 缺目的地'],['no_weight','全程无重（按默认重计费）'],['self_sign','自发自签'],['return','退件 / 拦截 / 退改（增值费用）']];
 const num=(id,v,label,step='.01')=>`<label>${label} <input id="${id}" type="text" inputmode="decimal" data-number="settings.${id}" value="${v}" step="${step}"></label>`;
 return `<section class="set-grid" id="configForm"><div class="panel"><div class="panel-head"><h2>导入账单</h2></div>
 <input id="billFiles" type="file" accept=".xlsx" multiple hidden><button class="drop" id="chooseBills" ${importBusy?'disabled':''}><b>把出港账单 Excel 拖到这里，或点击选择</b><br>可一次放多个文件：同一运单不会重复计算</button><p id="importProgress" class="hint" role="status">${esc(importProgress)}</p>
 <div class="row"><label for="conflictRule">同运单新版本</label><select id="conflictRule">${opt('replace',c.conflictRule==='replace','以最新导入为准')}${opt('review',c.conflictRule==='review','逐条核对')}</select></div><p class="hint">冲突规则保存后对后续导入生效。同一文件内多个不同版本进入核对，避免按行顺序选价。</p>
 <div class="import-list">${groups.map(g=>`<div class="import-item"><div class="row"><b>${esc(g.created.replace('T',' ').slice(0,19))} · ${g.files.length} 个文件</b><span class="hint">${({open:'导入中',done:'已完成',partial:'部分完成',undone:'已撤销'})[g.status]}</span></div>${g.files.map(f=>`<div class="fileline"><span title="${esc(f.name)}">${esc(f.name)}</span><span class="num">${int(f.rows)} 行</span></div>`).join('')}<p class="hint">新增/生效 ${int(g.stats.new)} · 重复 ${int(g.stats.duplicate)} · 替换 ${int(g.stats.replaced)} · 待核 ${int(g.stats.conflict)}${g.legacy?' · 旧版文件单独记录':''}</p>${g.status!=='undone'&&g.status!=='open'?btn('撤销这次导入',`data-undo="${g.id}" ${importBusy?'disabled':''}`):g.status==='open'&&!importBusy?btn('结束未完成导入',`data-finishimport="${g.id}"`):''}</div>`).join('')||'<p class="hint">还没有导入记录。</p>'}</div>
 <div class="row"><span>已导入 ${st.meta.customers.length} 个客户</span>${btn('冲突记录','id="showPending"')}</div>${pendingRows?pendingPanel():''}</div>
 <div class="panel"><div class="panel-head"><h2>重量段</h2></div><div class="row"><label for="baseWidth">基础区间宽度</label><div class="seg" id="baseWidth" data-value="${p.baseWidth}">${[.25,.5,1].map(v=>`<button data-settingwidth="${v}" aria-pressed="${p.baseWidth===v}">${v}kg</button>`).join('')}</div></div><div class="row">${num('mainShare',p.mainShare*100,'主要门槛 %')}${num('secondaryShare',p.secondaryShare*100,'次要门槛 %')}</div><div class="row">${num('tailShare',p.tailShare*100,'两端合并门槛 %')}${num('minGroupN',p.minGroupN,'分群最低票数','1')}</div><div class="row"><label><input type="checkbox" id="autoSteps" ${p.steps===null?'checked':''}> 自动识别计费档位</label>${btn('+ 添加档位','id="addStep"')}</div>
 <div id="stepRows">${(p.steps===null?st.meta.steps:p.steps).map((s,i)=>`<div class="row stepedit"><label>重量 kg <input name="step" type="text" inputmode="decimal" data-number="settings.step.${i}" step="0.1" min="0.1" max="4.9" value="${s.step}" ${p.steps===null?'disabled':''}></label><label>加价 元 <input name="jump" type="text" inputmode="decimal" data-number="settings.jump.${i}" step="0.01" min="0" value="${s.jump}" ${p.steps===null?'disabled':''}></label>${btn('删除',`data-delstep="${i}" ${p.steps===null?'disabled':''}`)}</div>`).join('')}</div><p class="hint">自动识别或手工设置；保存后，分析页会按新参数重新计算。</p></div>
 <div class="panel"><div class="panel-head"><h2>不计入客户成本的件</h2><span class="hint">本次导入中的票数；规则票数可能重叠</span></div>${rules.map(([code,label])=>`<div class="row"><label for="rule-${code}">${label}<br><span class="hint">${int(ex?.rules?.[code]||0)} 票</span></label><input type="checkbox" role="switch" id="rule-${code}" data-rule="${code}" ${(code==='invalid'?c.excludeRules.includes('no_fee')&&c.excludeRules.includes('no_dest'):c.excludeRules.includes(code))?'checked':''}></div>`).join('')}<p class="hint">关闭剔除后，对应记录将参与成本分析和报价。</p></div>
 <div class="panel"><div class="panel-head"><h2>成本模拟默认值</h2><span class="hint">在成本分析页的“成本模拟”面板调整；保存为默认后下次打开沿用。</span></div>${SIM_META.map(([k,label])=>`<div class="row"><span>${label}</span><span class="num ${c.simulation[k].on?'':'hint'}">${c.simulation[k].on?'开启':'关闭'}${Object.entries(c.simulation[k]).filter(([f])=>f!=='on').map(([f,v])=>' · '+({v:k==='service'?'每单退改':'每票',base:'基数',rate:'罚款/公斤',share:'奖励比例',target:'指标'}[f]||f)+' '+v).join('')}</span></div>`).join('')}</div></section>
 <section class="panel settings-actions"><span class="hint">更改会应用于成本分析与新报价；已保存报价保留原值。</span>${btn('放弃未保存修改','id="resetSettings"')}<button class="btn primary" id="saveSettings" ${settingsBusy?'disabled':''}>${settingsBusy?'正在保存…':'保存设置并重算'}</button></section>${aboutPanel()}`;
}
function pendingPanel(){return `<section class="pending-list"><h3>冲突记录 · ${int(pendingRows.total)} 条</h3><div class="tw"><table class="list"><thead><tr><th>选择</th><th>运单</th><th>新金额 / 旧金额</th></tr></thead><tbody>${pendingRows.rows.map(x=>`<tr><td><input type="checkbox" data-pending="${x.id}" aria-label="选择待核 ${esc(x.waybill)}"></td><td>${esc(x.waybill)}<br><span class="hint">${esc(x.customer)} · ${esc(x.sendDay)}</span></td><td class="num">${fmt(x.system)} / ${x.existing.map(y=>fmt(y.system)).join('、')||'无'}<br><span class="hint">${esc(x.issue)}</span></td></tr>`).join('')}</tbody></table></div><div class="row">${btn('替换旧版本','data-resolve="replace"')}${btn('排除新版本','data-resolve="ignore"')}</div><div class="row">${btn('上一页',`data-pendingpage="${pendingOffset-20}" ${pendingOffset===0?'disabled':''}`)}<span>${Math.floor(pendingOffset/20)+1} / ${Math.max(1,Math.ceil(pendingRows.total/20))}</span>${btn('下一页',`data-pendingpage="${pendingOffset+20}" ${pendingOffset+20>=pendingRows.total?'disabled':''}`)}</div></section>`}
async function reloadMeta(){responseCache.clear();st.meta=await api('meta');st.excludeZones=st.excludeZones.filter(n=>visibleZones().some(g=>g.name===n));st.analysis={...st.meta.analysisConfig};S.steps=st.meta.steps.map(x=>[x.step,x.jump]);st.dirtyWidth=true;qs.result=null;qs.saved=null;qs.error=null;qs.overrides={};}
async function loadPending(offset=0){captureSettings();try{pendingRows=await api('pending',{offset,limit:20});pendingOffset=offset;render()}catch(e){toast(e.message)}}
async function importFiles(files){if(importBusy||!files.length)return;captureSettings();if(files.some(f=>!f.name.toLowerCase().endsWith('.xlsx'))){toast('请选择 .xlsx 出港账单');return}importBusy=true;let gid,failed=0;const status=m=>{importProgress=m;if(st.page==='data'&&$('#importProgress'))$('#importProgress').textContent=m};render();
 try{gid=(await api('batches/start',{})).id;for(let i=0;i<files.length;i++){const f=files[i];status(`正在上传 ${i+1}/${files.length}：${f.name}`);try{
   const upload=await new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST','/api/v2/import');xhr.setRequestHeader('X-App-Token',st.token);xhr.setRequestHeader('X-Filename',encodeURIComponent(f.name));xhr.setRequestHeader('X-Import-Group',gid);xhr.upload.onprogress=e=>{if(e.lengthComputable)status(`上传 ${i+1}/${files.length} · ${Math.round(e.loaded/e.total*100)}% · ${f.name}`)};xhr.onload=()=>{try{const d=JSON.parse(xhr.responseText);xhr.status===200&&!d.error?resolve(d):reject(Error(d.error||'上传失败'))}catch(e){reject(e)}};xhr.onerror=()=>reject(Error('上传连接中断，请在导入记录核对结果'));xhr.send(f)});
   let job;do{await new Promise(r=>setTimeout(r,500));job=await api('jobs?id='+upload.jobs[0]);status(`${i+1}/${files.length} · ${job.phase} · ${int(job.rows)} 行 · ${f.name}`)}while(['queued','running'].includes(job.state));if(job.state==='error')throw Error(job.message);
 }catch(e){failed++;toast(f.name+'：'+e.message)}}
 const d=await api('batches/finish',{id:gid,partial:failed>0});await reloadMeta();cfgDraft=null;status(`导入结束：新增/生效 ${int(d.group.stats.new)}，重复 ${int(d.group.stats.duplicate)}，替换 ${int(d.group.stats.replaced)}，待核 ${int(d.group.stats.conflict)}${failed?'；'+failed+' 个文件失败':''}`);toast(importProgress);
 const span=V3.importRange(d.group,st.dateBasis);
 if(span.start&&d.group.customers.length){st.page='analysis';st.sel=d.group.customers.length<=8?d.group.customers:'all';st.preset='import';st.start=span.start;st.end=span.end;st.level='province';st.expand=false;st.showAll=false;st.compareBand=null;st.decA=st.decB=null;st.dirtyWidth=false;schedule('导入完成')}else render();
 }catch(e){status(e.message);toast(e.message)}finally{importBusy=false;if(st.page==='data')render()}
}
document.addEventListener('click',async e=>{const t=e.target.closest('button');if(!t||t.disabled)return;
 if(t.dataset.settingwidth){if(!captureSettings())return;draft().analysis.baseWidth=+t.dataset.settingwidth;render();return}
 if(t.id==='chooseBills'){$('#billFiles').click();return}
 if(t.id==='saveSettings'){if(!captureSettings())return;settingsBusy=true;render();try{await api('config',{...draft(),expectedVersion:st.meta.dataVersion});await reloadMeta();cfgDraft=null;toast('设置已保存，返回分析时重新计算')}catch(e){toast(e.message)}finally{settingsBusy=false;NumericInputs.clear('settings.');render()}return}
 if(t.id==='resetSettings'){NumericInputs.clear('settings.');cfgDraft=null;render();return}
 if(['addStep','addPolicy','addCost','addRebate'].includes(t.id)||t.dataset.delstep!==undefined||t.dataset.delpolicy!==undefined||t.dataset.delcost!==undefined){if(!captureSettings())return;NumericInputs.clear('settings.');const c=draft();if(t.id==='addStep'){c.analysis.steps=c.analysis.steps||clone(st.meta.steps);c.analysis.steps.push({step:3,jump:0})}if(t.dataset.delstep!==undefined)c.analysis.steps.splice(+t.dataset.delstep,1);if(t.id==='addPolicy')c.subsidyPolicies.push({type:'',perTicket:0,start:null,end:null});if(t.dataset.delpolicy!==undefined)c.subsidyPolicies.splice(+t.dataset.delpolicy,1);if(t.id==='addCost'||t.id==='addRebate')c.adjustments.push({id:crypto.randomUUID(),name:t.id==='addRebate'?'返利':'操作成本',amount:0,sign:t.id==='addRebate'?-1:1,enabled:true});if(t.dataset.delcost!==undefined)c.adjustments.splice(+t.dataset.delcost,1);render();return}
 if(t.id==='showPending'){loadPending();return}
 if(t.dataset.pendingpage!==undefined){loadPending(+t.dataset.pendingpage);return}
 if(t.dataset.resolve){const ids=[...document.querySelectorAll('[data-pending]:checked')].map(x=>+x.dataset.pending);if(!ids.length){toast('先勾选待核记录');return}try{await api('pending/resolve',{ids,action:t.dataset.resolve,expectedVersion:st.meta.dataVersion});await reloadMeta();await loadPending(pendingOffset)}catch(e){toast(e.message)}return}
 if(t.dataset.finishimport){try{await api('batches/finish',{id:t.dataset.finishimport,partial:true});await reloadMeta();render()}catch(e){toast(e.message)}return}
 if(t.dataset.undo){captureSettings();if(t.dataset.confirmUndo!=='true'){t.dataset.confirmUndo='true';t.textContent='确认撤销并恢复旧记录';const note=document.createElement('p');note.className='hint';note.textContent='原始文件和已保存报价会保留。再次点击确认撤销。';t.after(note);return}try{await api('batches/undo',{id:t.dataset.undo,expectedVersion:st.meta.dataVersion});await reloadMeta();pendingRows=null;toast('已撤销，原始文件和操作记录已保留');render()}catch(e){toast(e.message)}return}
});
document.addEventListener('change',e=>{if(e.target.id==='billFiles')importFiles([...e.target.files]);if(e.target.id==='autoSteps'){const checked=e.target.checked;captureSettings();draft().analysis.steps=checked?null:clone(st.meta.steps);render()}});
document.addEventListener('dragover',e=>{if(e.target.closest('#chooseBills'))e.preventDefault()});document.addEventListener('drop',e=>{if(e.target.closest('#chooseBills')){e.preventDefault();importFiles([...e.dataTransfer.files])}});

function aboutPanel(){return `<section class="panel" id="aboutPanel"><div class="panel-head"><h2>关于</h2><span class="hint">出港成本分析 · 开源项目</span></div><div class="about">${[['当前版本','v'+st.meta.version],['发布日期',st.meta.released],['数据版本',st.meta.dataVersion],['库内记录',int(st.meta.counts.total)],['历史版本','v3.4.0 加速与横版报告 · v3.3.0 区域与报告 · v3.2.0 历史对比']].map(([k,v])=>`<div class="hint">${k}<b>${esc(v)}</b></div>`).join('')}</div></section>`}

document.addEventListener('numericcommit',e=>{if(e.target.dataset.number?.startsWith('settings.'))captureSettings()});

;
// v34-combo.js
'use strict';
let comboIndex=-1,comboMatches=[];
function comboClose(){const list=$('#custList'),input=$('#custSearch');if(list)list.hidden=true;if(input){input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant')}}
function comboRender(reset=true){const input=$('#custSearch'),list=$('#custList');if(!input||!list)return;const q=input.value.trim().toLocaleLowerCase(),selected=st.sel==='all'?[]:st.sel;
 const scoped=new Map((st.result?.customerIndex||[]).map(x=>[x.name,x]));
 const all=st.meta.customers.map(x=>scoped.get(x.name)||{...x,normalN:0,cluster:null}).filter(x=>!selected.includes(x.name)&&x.name.toLocaleLowerCase().includes(q)).sort((a,b)=>b.normalN-a.normalN||a.name.localeCompare(b.name));comboMatches=all.slice(0,12);if(reset)comboIndex=-1;
 const mark=n=>{const i=n.toLocaleLowerCase().indexOf(q);return q&&i>=0?esc(n.slice(0,i))+'<mark>'+esc(n.slice(i,i+q.length))+'</mark>'+esc(n.slice(i+q.length)):esc(n)};
 list.innerHTML=comboMatches.map((x,i)=>`<div class="opt ${i===comboIndex?'active':''}" id="customer-option-${i}" role="option" aria-selected="${i===comboIndex}" data-pick="${esc(x.name)}"><span class="nm" title="${esc(x.name)}">${mark(x.name)}</span><span class="meta num">${int(x.normalN)}票</span><span class="tag">${esc(x.normalN<(st.analysis?.minGroupN||300)?'小客户':x.cluster||'未分群')}</span></div>`).join('')+(all.length>12?`<div class="more-hint">还有 ${all.length-12} 个，继续输入缩小范围</div>`:!all.length?'<div class="more-hint">没有匹配的客户</div>':'');list.hidden=false;input.setAttribute('aria-expanded','true');if(comboIndex>=0){input.setAttribute('aria-activedescendant','customer-option-'+comboIndex);list.children[comboIndex]?.scrollIntoView({block:'nearest'})}else input.removeAttribute('aria-activedescendant')}
function pickCustomer(name){try{const next=V3.selected(st.sel,name);$('#custSearch').value='';comboClose();choose(next);$('#custSearch')?.focus({preventScroll:true});comboClose()}catch(e){toast(e.message)}}
document.addEventListener('input',e=>{if(e.target.id==='custSearch')comboRender()});
document.addEventListener('focusin',e=>{if(e.target.id==='custSearch')comboRender()});
document.addEventListener('mousedown',e=>{if(e.target.closest('[data-pick]'))e.preventDefault()});
document.addEventListener('click',e=>{const row=e.target.closest('[data-pick]');if(row){pickCustomer(row.dataset.pick);return}if(!e.target.closest('.combo'))comboClose()});
document.addEventListener('keydown',e=>{if(e.target.id!=='custSearch')return;if(e.key==='Escape'){comboClose();return}if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();if($('#custList').hidden)comboRender();comboIndex=Math.max(0,Math.min(comboMatches.length-1,comboIndex+(e.key==='ArrowDown'?1:-1)));comboRender(false)}if(e.key==='Enter'){e.preventDefault();const c=comboMatches[comboIndex]||comboMatches.find(c=>c.name===e.target.value.trim());if(c)pickCustomer(c.name)}});
