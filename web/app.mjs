import {append,clone,createJournal,nextAction,replay,require,SCHEMA,validate} from './engine.mjs';
import {sample} from './sample.mjs';
const $=id=>document.getElementById(id), KEY='oneclearstep-journal-v1', LOCK='oneclearstep-writer-v1';
let journal, view, selected=null, busy=false, stale=false, mode='add', saved=false;
const writable=Boolean(navigator.locks?.request && crypto.subtle);
function notice(message, reload=false) {
  $('notice').replaceChildren(document.createTextNode(message));
  if(reload) { const button=document.createElement('button'); button.textContent='Reload latest'; button.addEventListener('click',()=>load().catch(fail)); $('notice').append(' ',button); }
}
function fail(error) { notice(error.message || String(error)); }
function sizeContext() { $('context').style.height='auto';$('context').style.height=Math.min(200,Math.max(44,$('context').scrollHeight+4))+'px'; }
function stateLabel(row) {
  if(row.state==='waiting') return 'Waiting for '+row.blocked_by.map(id=>view.plan.tasks.find(t=>t.id===id).title).join(', ');
  return {done:'Done',check:'Uncertain',ready:'Ready'}[row.state];
}
function render() {
  const result=nextAction(view.plan), chosen=result.tasks.find(t=>t.id===selected) || result.next;
  selected=chosen?.id || null;
  $('plan-title').textContent=view.plan.title;
  const synthetic=journal.events.length===1 && journal.events[0].op_id==='synthetic-seed';
  $('plan-meta').textContent=(synthetic?'Synthetic example':'Local plan')+' · Revision '+view.revision;
  const completeView=result.complete&&!chosen;
  $('focus').classList.toggle('complete',completeView);
  $('focus-state').textContent=completeView?'All tasks recorded done':chosen.state==='check'?'Needs a check':chosen.state==='waiting'?'Waiting for a prerequisite':chosen.state==='done'?'Recorded done':'One next action';
  $('action-title').textContent=completeView?'Your plan is recorded complete.':chosen.title;
  let explanation=completeView?'These are your records, not proof of external completion.':chosen.state==='waiting'?stateLabel(chosen)+'. Resolve the prerequisite before continuing.':chosen.state==='check'&&selected==='booking'?'Before sending invitations, check whether the booking actually went through.':chosen.state==='check'?'Check what actually happened before recording a result.':'Use the context below, then record what happened.';
  $('action-explanation').textContent=explanation;
  $('context').value=chosen?.context || '';
  sizeContext();
  $('context').disabled=!chosen || busy || stale || !writable;
  for(const id of ['done','pending','uncertain']) $(id).disabled=!chosen || busy || stale || !writable || (id==='done'&&chosen.state==='waiting');
  $('uncertain').hidden=chosen?.state==='check';
  $('tasks').replaceChildren();
  for(const row of result.tasks) {
    const li=document.createElement('li'), button=document.createElement('button'), title=document.createElement('span'), status=document.createElement('span');
    button.className='task-row'; button.setAttribute('aria-current',String(row.id===selected));
    title.textContent=row.title; status.textContent=stateLabel(row); status.className='task-state';
    button.append(title,status); button.addEventListener('click',()=>{selected=row.id;render();}); li.append(button); $('tasks').append(li);
  }
  $('trail').replaceChildren();
  let trailPlan=clone(journal.initial_plan);const history=[];
  for(const e of journal.events) {
    history.push(e.op_id==='synthetic-seed'?'Booking marked uncertain':e.kind==='reset'?'Plan replaced; previous history retained':e.kind==='add'?'Added: '+e.payload.title:(trailPlan.tasks.find(t=>t.id===e.payload.task_id)?.title||e.payload.task_id)+' → '+e.payload.status);
    if(e.kind==='reset')trailPlan=clone(e.payload);else if(e.kind==='add')trailPlan.tasks.push(clone(e.payload));
  }
  const labels=['Revision '+view.revision,...history.slice(-4).reverse()];
  if(synthetic) labels.push('Resume without guessing.');
  for(const text of labels) { const li=document.createElement('li');li.textContent=text;$('trail').append(li); }
  $('storage').textContent=saved?'Saved in this browser. Export a copy to keep it.':'Not saved. Export a copy before closing this page.';
  for(const id of ['new','add','import']) $(id).disabled=busy || stale || !writable;
}
async function seed() {
  const base=createJournal(sample);
  return (await append(base,{op_id:'synthetic-seed',kind:'state',payload:{task_id:'booking',status:'uncertain',context:'Booking request sent. No confirmation recorded.'}},0)).journal;
}
async function load() {
  const raw=localStorage.getItem(KEY);
  if(raw) { journal=JSON.parse(raw);view=await replay(journal);saved=true; }
  else {
    journal=await seed();view=await replay(journal);saved=false;
    if(writable) await navigator.locks.request(LOCK,async()=>{
      const latest=localStorage.getItem(KEY);
      if(latest) journal=JSON.parse(latest);
      else localStorage.setItem(KEY,JSON.stringify(journal));
      view=await replay(journal);saved=true;
    });
  }
  stale=false;selected=null;notice(writable?'':'This browser cannot coordinate safe local writes. The example is read-only; export remains available.');render();
}
async function commit(kind,payload) {
  require(writable && !busy && !stale,'Reload the current plan before editing.');
  const expected=view.revision, operation={op_id:crypto.randomUUID(),kind,payload};
  let failed=false;const previousSelection=selected;
  busy=true;render();
  try {
    await navigator.locks.request(LOCK,async()=>{
      const raw=localStorage.getItem(KEY); require(raw,'Stored journal is missing. Reload before editing.');
      const changed=await append(JSON.parse(raw),operation,expected);
      // Commit browser storage before changing visible application state.
      localStorage.setItem(KEY,JSON.stringify(changed.journal));
      journal=changed.journal;view=await replay(journal);saved=true;selected=null;
    });
    notice('Saved revision '+view.revision+'. No external action was performed.');
  } catch(error) {
    failed=true;
    if(error.message.includes('Revision conflict')) {stale=true;notice(error.message,true);} else fail(error);
    throw error;
  } finally {busy=false;if(failed)selected=previousSelection;render();if(failed&&kind==='state')$('context').value=payload.context;}
}
for(const status of ['done','pending','uncertain']) $(status).addEventListener('click',()=>{
  const payload={task_id:selected,status,context:$('context').value};commit('state',payload).catch(()=>{});
});
$('context').addEventListener('input',sizeContext);
$('export').addEventListener('click',()=>{
  const data=new Blob([JSON.stringify(journal,null,2)+'\n'],{type:'application/json'}),url=URL.createObjectURL(data),a=document.createElement('a');
  a.href=url;a.download='oneclearstep-journal-r'+view.revision+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  notice('Export contains your task contexts and local history. Keep it private if they are sensitive.');
});
function openEditor(newPlan) {
  mode=newPlan?'reset':'add';$('task-form').reset();$('editor-error').textContent='';
  $('editor-title').textContent=newPlan?'New plan':'Add task';
  $('plan-name').hidden=$('plan-name-label').hidden=!newPlan;$('plan-name').required=newPlan;
  $('deps').hidden=$('deps-label').hidden=newPlan;$('deps').replaceChildren();
  for(const task of view.plan.tasks) {const option=document.createElement('option');option.value=task.id;option.textContent=task.title;$('deps').append(option);}
  $('editor').showModal();(newPlan?$('plan-name'):$('task-title')).focus();
}
$('new').addEventListener('click',()=>openEditor(true));$('add').addEventListener('click',()=>openEditor(false));
$('cancel').addEventListener('click',()=>$('editor').close());
$('task-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const task={id:'t-'+crypto.randomUUID(),title:$('task-title').value.trim(),context:$('task-context').value,status:'pending',priority:Number($('priority').value),depends_on:mode==='reset'?[]:[...$('deps').selectedOptions].map(o=>o.value)};
  try {await commit(mode,mode==='reset'?{schema:SCHEMA,title:$('plan-name').value.trim(),tasks:[task]}:task);$('editor').close();}catch(error){$('editor-error').textContent=error.message;}
});
$('import').addEventListener('click',()=>$('file').click());
$('file').addEventListener('change',async()=>{
  const file=$('file').files[0];if(!file)return;
  try {require(file.size<=2*1024*1024,'Import is limited to 2 MiB.');const data=JSON.parse(await file.text());const plan=data.schema==='oneclearstep/browser-v1'?(await replay(data)).plan:validate(data);await commit('reset',clone(plan));notice('Imported the current plan. Imported history stays in the original file; prior browser history is retained.');}catch(error){fail(error);}finally{$('file').value='';}
});
window.addEventListener('storage',event=>{if(event.key===KEY){stale=true;notice('Another tab changed this plan. Reload the latest revision before editing.',true);for(const id of ['context','done','pending','uncertain','new','add','import'])$(id).disabled=true;}});
load().catch(async error=>{
  saved=false; journal=await seed();view=await replay(journal);stale=true;render();
  notice('Stored history could not be loaded safely: '+error.message+'. Existing storage was preserved. This example is read-only; Export contains the example, not the damaged history.');
});
