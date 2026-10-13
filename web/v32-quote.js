'use strict';
const qs={includeAddon:false,target:'',basis:'selection',subsidyMode:'target',subsidyType:'',mode:'percent',value:20,overrides:{},rows:new Set(),cols:new Set(),all:false,result:null,pending:false,error:null,saved:null,list:[],generation:0,bulkMode:'fixed',bulkValue:.1};
let qt=null;
function ownQuote(){return V3.ownQuoteCustomer(qs.saved?qs.result.request.customers:st.sel,qs.target,qs.basis)}
function defaultQuoteSubsidy(){qs.subsidyMode='target';qs.subsidyType=''}
function quoteRequest(){return {...request(),schema:'v3',target:qs.target,basis:qs.basis,subsidyType:qs.subsidyType,subsidyMode:qs.subsidyMode,mode:qs.mode,value:qs.value,overrides:{...qs.overrides},flows:qs.flows,includeAddon:qs.includeAddon}}
function enterQuote(scope){
  NumericInputs.clear('quote.');
  const context=JSON.stringify({customers:st.sel,start:st.start,end:st.end,dateBasis:st.dateBasis,version:st.meta.dataVersion,simulation:st.sim});
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
function quotePage(){const r=qs.result,t=r?.summary,disabled=qs.pending||!!qs.saved,selection=V3.quoteSelection(r,qs.rows,qs.cols);
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
    <div class="tw"><table class="quote"><thead><tr><th style="text-align:left">流向</th>${r.bands.map((b,i)=>`<th><label><input type="checkbox" data-qcol="${i}" aria-label="选择 ${esc(b.label)} 列" ${qs.cols.has(i)?'checked':''} ${disabled?'disabled':''}> <span class="num">${b.label}</span></label><br><span class="cls ${b.cls}">${pct(b.share)}</span></th>`).join('')}</tr></thead><tbody>${(qs.all?r.rows:r.rows.slice(0,14)).map(row=>`<tr><th style="text-align:left"><label><input type="checkbox" data-qrow="${esc(row.flow)}" aria-label="选择 ${esc(row.flow)} 行" ${qs.rows.has(row.flow)?'checked':''} ${disabled?'disabled':''}> ${esc(row.flow)}</label></th>${row.cells.map(td).join('')}</tr>`).join('')}</tbody></table></div>
    ${r.rows.length>14?`<button class="more" id="moreQ">${qs.all?'只看前 14 个流向':'展开全部 '+r.rows.length+' 个流向'}</button>`:''}<p class="hint">导出给客户的 Excel 只有流向、重量段和报价，不含成本、毛利和其他客户信息。成本含当前已开启的模拟项；增值附加由本页勾选控制，只摊入一次。客户补贴为额外指定扣减，默认无补贴。</p></section></div>`:'<section class="panel" role="status">正在核算报价…</section>'}
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
  if(t.id==='qSelectAll'){qs.rows=new Set(qs.result.rows.map(x=>x.flow));qs.cols=new Set([0,1,2,3,4,5,6]);render();return}
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
