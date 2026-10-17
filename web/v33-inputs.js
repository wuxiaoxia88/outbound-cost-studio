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
