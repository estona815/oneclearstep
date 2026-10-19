import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {append,clone,createJournal,digest,nextAction,replay,validate} from './web/engine.mjs';
const sample=JSON.parse(readFileSync(new URL('./evidence/synthetic-plan.json',import.meta.url),'utf8'));
let passed=0;
async function check(name,run){await run();passed++;console.log('PASS '+name);}
await check('known SHA-256 of canonical empty object',async()=>assert.equal(await digest({}),'44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'));
await check('Python and browser return identical resumption decisions',()=>{
  const python=JSON.parse(execFileSync('python3',['-c','import json,oneclearstep; p=json.load(open("evidence/synthetic-plan.json")); print(json.dumps(oneclearstep.next_action(p)))'],{cwd:new URL('.',import.meta.url)}));
  assert.deepEqual(nextAction(sample),python);
});
const seed=createJournal(sample);
const operation={op_id:'confirm-booking',kind:'state',payload:{task_id:'booking',status:'done',context:'Synthetic confirmation'}};
const first=await append(seed,operation,0);
await check('confirmation unlocks invitations',()=>assert.equal(nextAction(first.plan).next.id,'invites'));
await check('idempotent operation retry does not append',async()=>{const retry=await append(first.journal,operation,0);assert.equal(retry.duplicate,true);assert.deepEqual(retry.journal,first.journal);});
await check('reused operation ID with changed content is rejected',async()=>{const changed=clone(operation);changed.payload.status='pending';await assert.rejects(append(first.journal,changed,1),/different content/);});
await check('stale revision is rejected with no input mutation',async()=>{const before=JSON.stringify(first.journal);await assert.rejects(append(first.journal,{...operation,op_id:'another'},0),/Revision conflict/);assert.equal(JSON.stringify(first.journal),before);});
await check('tampered journal is rejected',async()=>{const changed=clone(first.journal);changed.events[0].payload.context='changed';await assert.rejects(replay(changed),/integrity/);});
await check('add task validates missing and cyclic dependencies',async()=>{const bad={id:'new',title:'New',status:'pending',context:'',priority:50,depends_on:['unknown']};await assert.rejects(append(first.journal,{op_id:'add',kind:'add',payload:bad},1),/dependency/);bad.depends_on=['new'];await assert.rejects(append(first.journal,{op_id:'add',kind:'add',payload:bad},1),/cycle/);});
await check('reset preserves earlier history',async()=>{const plan=clone(sample);plan.title='New local plan';const reset=await append(first.journal,{op_id:'reset',kind:'reset',payload:plan},1);assert.equal(reset.journal.events.length,2);assert.equal((await replay(reset.journal)).plan.title,plan.title);assert.equal(reset.journal.initial_plan.title,'Community workshop');});
await check('reopening a prerequisite blocks recorded-done descendants',async()=>{let changed=await append(first.journal,{op_id:'invites',kind:'state',payload:{task_id:'invites',status:'done',context:''}},1);changed=await append(changed.journal,{op_id:'reopen',kind:'state',payload:{task_id:'booking',status:'uncertain',context:''}},2);assert.equal(nextAction(changed.plan).tasks.find(t=>t.id==='invites').state,'waiting');});
await check('untrusted titles are preserved as data and malformed structures rejected',()=>{const plan=clone(sample);plan.tasks[0].title='<img src=x onerror=alert(1)>';assert.equal(validate(plan).tasks[0].title,plan.tasks[0].title);plan.tasks.push(clone(plan.tasks[0]));assert.throws(()=>validate(plan),/duplicate/);});
console.log(passed+' checks passed');
