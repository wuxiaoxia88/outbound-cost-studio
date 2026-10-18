'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const V=require('../web/v34-core.js');
test('calendar presets handle leap month and year boundaries',()=>{
  assert.deepEqual(V.dateRange('previous','2024-03-31'),{start:'2024-02-01',end:'2024-02-29'});
  assert.deepEqual(V.dateRange('previous','2026-01-10'),{start:'2025-12-01',end:'2025-12-31'});
  assert.deepEqual(V.dateRange('week','2026-01-03'),{start:'2025-12-28',end:'2026-01-03'});
  assert.deepEqual(V.dateRange('month','2026-10-13'),{start:'2026-10-01',end:'2026-10-13'});
  assert.deepEqual(V.dateRange('import','2026-10-13',{start:'2026-09-01',end:'2026-10-01'}),{start:'2026-09-01',end:'2026-10-01'});
});
test('selection deduplicates and enforces eight without changing input',()=>{
  assert.deepEqual(V.selected('all','甲'),['甲']);const names=['甲','乙'];assert.deepEqual(V.selected(names,'甲'),names);
  assert.deepEqual(names,['甲','乙']);assert.throws(()=>V.selected(['1','2','3','4','5','6','7','8'],'9'),/8/);
});
test('money labels distinguish unavailable and zero, suppress negative zero',()=>{
  assert.equal(V.signed(-0),'+0.000');assert.equal(V.signed(-.00001),'+0.000');assert.equal(V.signed(-.003),'−0.003');
  assert.equal(V.fmt(null),'—');assert.equal(V.pct(null),'—');assert.equal(V.pct(0),'0.0%');
  assert.equal(V.esc('<img onerror="x">&'), '&lt;img onerror=&quot;x&quot;&gt;&amp;');
});
test('charts handle sparse, constant and negative actual cost without invalid geometry',()=>{
  const ctx=vm.createContext({...V,st:{unit:'share'},S:{steps:[]},isAll:()=>true,bandOf:()=>-1,colorOf:()=> 'red'});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/v34-charts.js'),'utf8'),ctx);
  for(const cost of [0,-2,3]){
    const d=Array.from({length:61},()=>({n:0,fee:0}));d[8]={n:cost===0?1:50,fee:cost*50};
    ctx.d=d;const svg=vm.runInContext('distChart(d,d,[])',ctx);assert.ok(!/NaN|Infinity/.test(svg));
  }
  const svg=vm.runInContext("waterfall([{label:'B',kind:'base',v:-2},{label:'delta',kind:'delta',v:-1},{label:'A',kind:'end',v:-3}])",ctx);assert.ok(!/NaN|Infinity/.test(svg));
});
test('reference caption reconciles displayed three-decimal cards',()=>{
 const p=V.referenceParts({eq:2.5952,avg:2.5157,gap:-.0031});assert.equal(p.diff,.079);assert.equal(p.price,.003);assert.equal(p.extra,.076);
});
test('comparison chart uses combined customer volume for 99.5 percent cutoff',()=>{
 const ctx=vm.createContext({...V,st:{unit:'share'},S:{steps:[]},isAll:()=>false,bandOf:()=>-1,colorOf:()=> 'red'});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/v34-charts.js'),'utf8'),ctx);
 ctx.ds=Array.from({length:61},()=>({n:0,fee:0}));ctx.ds[20]={n:10000,fee:20000};ctx.dt=Array.from({length:61},()=>({n:0,fee:0}));ctx.dt[60]={n:10,fee:100};
 assert.equal(vm.runInContext('rangeU(ds)',ctx),20);assert.equal(vm.runInContext('rangeU(dt)',ctx),60);
 const svg=vm.runInContext("overlayChart([{name:'A',d:ds,t:{n:10000}},{name:'B',d:dt,t:{n:10}}])",ctx);assert.ok(!svg.includes('6.0'));assert.ok(svg.includes('2.0'));
});

test('quote subsidy default requires the same single customer and own basis',()=>{
 assert.equal(V.ownQuoteCustomer(['甲'],'甲','selection'),true);
 for(const [cs,target,basis] of [[['甲'],'乙','selection'],[['甲','乙'],'甲','selection'],['all','甲','selection'],[['甲'],'甲','network']])assert.equal(V.ownQuoteCustomer(cs,target,basis),false);
});
test('quote bulk selection covers row-only, column-only, intersection and hidden flows',()=>{
 const result={bands:[{},{}],rows:[{flow:'A',cells:[{key:'A0',price:2},{key:'A1',price:null}]},{flow:'B',cells:[{key:'B0',price:3},{key:'B1',price:4}]}]};
 const keys=(rows,cols)=>V.quoteSelection(result,new Set(rows),new Set(cols));
 assert.deepEqual(keys([],[]).cells,[]);
 assert.deepEqual(keys(['A'],[]).cells.map(x=>x.key),['A0']);assert.equal(keys(['A'],[]).cols,2);
 assert.deepEqual(keys([],[0]).cells.map(x=>x.key),['A0','B0']);assert.equal(keys([],[0]).rows,2);
 assert.deepEqual(keys(['B'],[1]).cells.map(x=>x.key),['B1']);
});

test('cost curves break at absent or undersampled intermediate weights',()=>{
 const ctx=vm.createContext({...V,st:{unit:'share'},S:{steps:[]},isAll:()=>false,bandOf:()=>-1,colorOf:()=> 'red'});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/v34-charts.js'),'utf8'),ctx);
 ctx.ds=Array.from({length:61},()=>({n:0,fee:0}));ctx.ds[5]={n:30,fee:60};ctx.ds[7]={n:30,fee:90};ctx.ds[8]={n:30,fee:90};
 const svg=vm.runInContext('distChart(ds,ds,[])',ctx),paths=[...svg.matchAll(/<path d="([^"]*)"/g)].map(x=>x[1]);
 assert.equal(paths.length,2);for(const path of paths){assert.equal((path.match(/M/g)||[]).length,2);assert.equal((path.match(/L/g)||[]).length,1)}
});
test('decimal drafts accept decimal intermediates and reject invalid commits',()=>{
 for(const n of ['0.','.5','0.35','1.044','0','12.'])assert.equal(V.validNumber(n),true,n);
 for(const n of ['',' ','-1','NaN','Infinity','1e309','0..3','.'])assert.equal(V.validNumber(n),false,n);
});
test('history shows sparse-month notice only when needed and uses ISO months',()=>{
 const ctx=vm.createContext({...V,V3:V});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/v34-history.js'),'utf8'),ctx);
 const month=(m,n)=>({month:m,n,daily:n,avgW:1,avg:2,criticalShare:0,mainShare:1,distribution:[1,...Array(11).fill(0)]});
 const current=month('2026-09',4000),previous=month('2026-08',7);
 ctx.r={history:{months:[previous,current],current,previous,mainBand:{lo:0,hi:.5},delta:null,hiddenMonthCount:4}};
 let html=vm.runInContext('historyPanel(r)',ctx);
 assert.ok(html.includes('另有 4 个零星月份（不足当月 10%，多为补录件）未列出'));
 assert.ok(html.includes('2026-08 仅 7 票，样本太少，不做比较'));
 assert.ok(html.includes('<td>2026-09</td>'));assert.ok(!html.includes('2026 年'));
 ctx.r.history.hiddenMonthCount=0;html=vm.runInContext('historyPanel(r)',ctx);
 assert.ok(!html.includes('零星月份'));
});

test('交件业务日快捷日期与本次导入范围',()=>{
 const core=require('../web/v34-core.js');
 assert.equal(core.today('handover',new Date('2026-10-16T03:59:59Z')),'2026-10-15');
 assert.equal(core.today('handover',new Date('2026-10-16T04:00:00Z')),'2026-10-16');
 assert.equal(core.today('send',new Date('2026-10-16T03:59:59Z')),'2026-10-16');
 const imported={start:'2026-09-01',end:'2026-09-30',dateRanges:{handover:{start:'2026-08-31',end:'2026-09-30'}}};
 assert.deepEqual(core.dateRange('import','2026-10-17',core.importRange(imported,'handover')),{start:'2026-08-31',end:'2026-09-30'});
 assert.equal(core.importRange(imported,'send').start,'2026-09-01');
});

test('仍运行v3.2的服务不能被新静态页面误标为业务日',()=>{
 assert.equal(V.dateLabel('handover',false),'交件（自然日）');
 assert.equal(V.dateLabel('handover',true),'交件（业务日 12:00–次日 12:00）');
 assert.equal(V.dateLabel('send',true),'寄件');
});

test('bulk quote selection includes separately quoted remote rows',()=>{
 const r={bands:[{}],rows:[{flow:'浙江',cells:[{key:'a',price:2}]}],remoteRows:[{flow:'新疆',cells:[{key:'b',price:9}]}]};
 assert.deepEqual(V.quoteSelection(r,new Set(['新疆']),new Set()).cells.map(x=>x.key),['b']);
 assert.deepEqual(V.quoteSelection(r,new Set(),new Set([0])).cells.map(x=>x.key),['a','b']);
});
