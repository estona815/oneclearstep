export const SCHEMA = 'oneclearstep/plan-v1';
const states = new Set(['pending', 'done', 'uncertain']);
const idPattern = /^[a-zA-Z0-9_-]{1,64}$/;
export const clone = value => JSON.parse(JSON.stringify(value));
export function require(ok, message) { if (!ok) throw new Error(message); }
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join('|') === [...keys].sort().join('|');
function text(value, max) { return typeof value === 'string' && value.length <= max && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value); }
export function validate(plan) {
  require(exact(plan, ['schema','title','tasks']) && plan.schema === SCHEMA && text(plan.title,160) && plan.title.trim(), 'Invalid plan title or schema.');
  require(Array.isArray(plan.tasks) && plan.tasks.length > 0 && plan.tasks.length <= 100, 'Use 1 to 100 tasks.');
  const tasks = new Map();
  for (const t of plan.tasks) {
    require(exact(t,['id','title','depends_on','status','context','priority']), 'Invalid task fields.');
    require(typeof t.id === 'string' && idPattern.test(t.id) && !tasks.has(t.id), 'Invalid or duplicate task ID.');
    require(text(t.title,240) && t.title.trim() && text(t.context,2000) && states.has(t.status), 'Invalid task title, context or state.');
    require(Number.isInteger(t.priority) && t.priority >= 0 && t.priority <= 100, 'Invalid task priority.');
    require(Array.isArray(t.depends_on) && t.depends_on.length <= 20 && t.depends_on.every(d=>typeof d==='string') && new Set(t.depends_on).size===t.depends_on.length, 'Invalid dependencies.');
    tasks.set(t.id,t);
  }
  const visiting = new Set(), visited = new Set();
  function visit(id) {
    require(tasks.has(id), 'A dependency does not exist.'); require(!visiting.has(id),'Dependency cycle.');
    if (visited.has(id)) return;
    visiting.add(id); tasks.get(id).depends_on.forEach(visit); visiting.delete(id); visited.add(id);
  }
  tasks.forEach(t=>visit(t.id)); return plan;
}
export function nextAction(plan) {
  validate(plan); const byId=new Map(plan.tasks.map(t=>[t.id,t])), effective=new Map();
  function satisfied(id) { if (!effective.has(id)) { const t=byId.get(id); effective.set(id,t.status==='done' && t.depends_on.every(satisfied)); } return effective.get(id); }
  const tasks=plan.tasks.map(t=>{const blocked_by=t.depends_on.filter(d=>!satisfied(d)); return {id:t.id,title:t.title,recorded_status:t.status,state:satisfied(t.id)?'done':blocked_by.length?'waiting':['done','uncertain'].includes(t.status)?'check':'ready',blocked_by,context:t.context};});
  const candidates=tasks.filter(t=>['check','ready'].includes(t.state));
  candidates.sort((a,b)=>Number(a.state!=='check')-Number(b.state!=='check') || byId.get(b.id).priority-byId.get(a.id).priority);
  return {complete:tasks.every(t=>t.state==='done'),next:candidates[0]||null,tasks,boundary:'Recorded task states do not verify external completion.'};
}
export function canonical(value) {
  if (Array.isArray(value)) return '['+value.map(canonical).join(',')+']';
  if (value && typeof value==='object') return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonical(value[k])).join(',')+'}';
  return JSON.stringify(value);
}
export async function digest(value) { const bytes=new TextEncoder().encode(canonical(value)); return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join(''); }
export function createJournal(plan) { return {schema:'oneclearstep/browser-v1',initial_plan:clone(validate(plan)),events:[]}; }
function apply(plan, kind, payload) {
  if (kind==='reset') return clone(validate(payload));
  if (kind==='add') { const changed=clone(plan); changed.tasks.push(clone(payload)); return validate(changed); }
  require(kind==='state' && exact(payload,['task_id','status','context']) && states.has(payload.status) && text(payload.context,2000), 'Invalid state event.');
  const changed=clone(plan), task=changed.tasks.find(t=>t.id===payload.task_id); require(task,'Unknown task.');
  task.status=payload.status; task.context=payload.context; return validate(changed);
}
export async function replay(journal) {
  require(exact(journal,['schema','initial_plan','events']) && journal.schema==='oneclearstep/browser-v1','Invalid browser journal.');
  require(Array.isArray(journal.events) && journal.events.length<=500 && new TextEncoder().encode(JSON.stringify(journal)).length<=2*1024*1024,'Journal limit exceeded.');
  let plan=clone(validate(journal.initial_plan)), previous=await digest(plan); const seen=new Map();
  for (let i=0;i<journal.events.length;i++) {
    const e=journal.events[i]; require(exact(e,['revision','op_id','kind','payload','prev_hash','hash']),'Invalid journal event.');
    const {hash,...body}=e;
    require(e.revision===i+1 && typeof e.op_id==='string' && idPattern.test(e.op_id) && !seen.has(e.op_id) && e.prev_hash===previous && hash===await digest(body),'Journal integrity mismatch.');
    plan=apply(plan,e.kind,e.payload); seen.set(e.op_id,e); previous=hash;
  }
  return {plan,revision:journal.events.length,previous,seen};
}
export async function append(journal, operation, expectedRevision) {
  const current=await replay(journal);
  require(exact(operation,['op_id','kind','payload']) && typeof operation.op_id==='string' && idPattern.test(operation.op_id),'Invalid operation.');
  const prior=current.seen.get(operation.op_id);
  if (prior) { require(prior.kind===operation.kind && canonical(prior.payload)===canonical(operation.payload),'Operation ID reused with different content.'); return {journal:clone(journal),...current,duplicate:true}; }
  require(Number.isInteger(expectedRevision) && expectedRevision===current.revision,'Revision conflict. Reload before changing state.');
  const plan=apply(current.plan,operation.kind,operation.payload);
  const body={revision:current.revision+1,...clone(operation),prev_hash:current.previous};
  const changed=clone(journal); changed.events.push({...body,hash:await digest(body)});
  await replay(changed); return {journal:changed,plan,revision:body.revision,duplicate:false};
}
