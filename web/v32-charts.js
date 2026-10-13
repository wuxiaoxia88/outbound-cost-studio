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
