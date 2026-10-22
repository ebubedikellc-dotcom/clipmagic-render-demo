const test=require('node:test'),assert=require('node:assert/strict');
require('./job-client.js');
function fixture(responses){
  let calls=0,updated=[],retry=[],stopped=[],scheduled=[];
  const stop=ClipJobs.watch('abc',{updated:x=>updated.push(x),retrying:x=>retry.push(x),stopped:x=>stopped.push(x)},{
    fetch:async()=>{calls++;const result=responses.shift();if(result instanceof Error)throw result;return result},
    schedule:(fn,delay)=>{const t={fn,delay};scheduled.push(t);return t},unschedule:t=>{scheduled=scheduled.filter(x=>x!==t)}
  });
  return {stop,updated,retry,stopped,get calls(){return calls},next:async()=>{await new Promise(setImmediate);const t=scheduled.find(x=>x.delay!==20000);assert.ok(t);scheduled=scheduled.filter(x=>x!==t);await t.fn()},flush:()=>new Promise(setImmediate)};
}
test('temporary network failure resumes the same job and completes',async()=>{
  const f=fixture([Error('network'),{ok:true,json:async()=>({status:'processing'})},{ok:true,json:async()=>({status:'completed'})}]);
  await f.flush();assert.equal(f.retry.length,1);assert.equal(f.stopped.length,0);
  await f.next();await f.next();assert.equal(f.calls,3);assert.equal(f.updated.at(-1).status,'completed');f.stop();
});
test('missing job stops without claiming a server restart',async()=>{
  const f=fixture([{ok:false,status:404,json:async()=>({detail:'Job not found'})}]);await f.flush();
  assert.equal(f.stopped[0].status,404);assert.equal(f.retry.length,0);f.stop();
});
test('cancelling a pending check ignores late results',async()=>{
  let resolve;const pending=new Promise(r=>resolve=r);let updates=0;
  const stop=ClipJobs.watch('abc',{updated:()=>updates++,retrying:()=>{},stopped:()=>{}},{fetch:()=>pending});stop();
  resolve({ok:true,json:async()=>({status:'completed'})});await new Promise(setImmediate);assert.equal(updates,0);
});
