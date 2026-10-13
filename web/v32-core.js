/* Pure presentation helpers. All cost and reference calculations stay on the server. */
(function(root){
'use strict';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(v,d=2)=>v==null||!Number.isFinite(+v)?'—':(+v).toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d});
const int=v=>v==null?'—':Math.round(v).toLocaleString('zh-CN');
const pct=(v,d=1)=>v==null||!Number.isFinite(v)?'—':(v*100).toFixed(d).replace('-','−')+'%';
const signed=(v,d=3)=>{if(v==null)return '—';const n=Math.abs(v)<.5*10**(-d)?0:v;return (n>=0?'+':'−')+fmt(Math.abs(n),d)};
const short=n=>String(n).replace(/省|市|壮族自治区|回族自治区|维吾尔自治区|自治区/g,'');
const day=d=>d.toISOString().slice(0,10);
function dateRange(kind,today,imported){
  const d=new Date(today+'T12:00:00Z'),y=d.getUTCFullYear(),m=d.getUTCMonth();
  if(kind==='import')return imported?{start:imported.start,end:imported.end}:null;
  if(kind==='week'){d.setUTCDate(d.getUTCDate()-6);return {start:day(d),end:today}}
  if(kind==='month')return {start:day(new Date(Date.UTC(y,m,1))),end:today};
  if(kind==='previous')return {start:day(new Date(Date.UTC(y,m-1,1))),end:day(new Date(Date.UTC(y,m,0)))};
  return null;
}
const today=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
function selected(names,name){const out=names==='all'?[name]:[...names];if(!out.includes(name))out.push(name);if(out.length>8)throw Error('最多同时对比 8 个客户');return out}
function normalize(s){return {...s,i:s.name,t:s.total||{n:s.n},cr:s.critical||{n:s.criticalN},topProv:s.topProvinces||[],d:[{n:0,fee:0},...(s.distribution||[])]}}
function referenceParts(t){const diff=(Math.round(t.eq*1000)-Math.round(t.avg*1000))/1000,price=-Math.round(t.gap*1000)/1000;return {diff,price,extra:(Math.round(diff*1000)-Math.round(price*1000))/1000}}
function ownQuoteCustomer(customers,target,basis){return basis==='selection'&&Array.isArray(customers)&&customers.length===1&&target.trim()===customers[0]}
function quoteSelection(result,rows,cols){
  if(!result||(!rows.size&&!cols.size))return {rows:0,cols:0,cells:[]};
  const selectedRows=result.rows.filter(r=>!rows.size||rows.has(r.flow));
  const selectedCols=result.bands.map((_,i)=>i).filter(i=>!cols.size||cols.has(i));
  return {rows:selectedRows.length,cols:selectedCols.length,cells:selectedRows.flatMap(r=>selectedCols.map(i=>r.cells[i]).filter(c=>c&&c.price!=null))};
}
const validNumber=s=>String(s).trim()!=='' && /^(?:\d+\.?\d*|\.\d+)$/.test(String(s).trim()) && Number.isFinite(Number(s)) && Number(s)>=0;
const api={validNumber,ownQuoteCustomer,quoteSelection,referenceParts,esc,fmt,int,pct,signed,short,dateRange,today,selected,normalize};
if(typeof module==='object'&&module.exports)module.exports=api;else root.V3=api;
})(typeof globalThis!=='undefined'?globalThis:this);
