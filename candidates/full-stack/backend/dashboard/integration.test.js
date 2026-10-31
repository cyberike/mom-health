"use strict";
// Actual FastAPI + SQLite + dashboard app.js, with simulated DOM and pipe transport.
// No listener, phone, browser, deployment, credentials or real health records.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const { randomUUID } = require('node:crypto');
const path = require('node:path');
const { setup, connect } = require('./app.test.js');
const token = 'synthetic-integration-token-not-for-deployment';
const headers = { Authorization: 'Bearer ' + token };
const child = spawn('python', [path.join(__dirname, '../tests/dashboard_bridge.py')], { stdio: ['pipe', 'pipe', 'inherit'] });
let queue = [], passed = 0;
createInterface({input: child.stdout}).on('line', line => { const item = queue.shift(); if(item) { clearTimeout(item.timer); item.resolve(JSON.parse(line)); } });
child.on('exit', code => { for(const item of queue) { clearTimeout(item.timer); item.reject(new Error('Bridge exited: ' + code)); } queue=[]; });
function request(method, url, body, auth=headers) {
  return new Promise((resolve,reject) => {
    const timer=setTimeout(()=>{reject(new Error('Local bridge deadline')); child.kill();},15000);
    queue.push({resolve,reject,timer}); child.stdin.write(JSON.stringify({method,path:url,body,headers:auth})+'\n');
  });
}
async function settle(ui) {
  for(let i=0;i<1000;i++){if(!ui.elements.connectButton.disabled)return;await new Promise(r=>setTimeout(r,10));}
  throw new Error('Dashboard did not settle');
}
async function check(name, action){await action();passed++;console.log('PASS '+name);}
const device = randomUUID(), session = randomUUID();
const rows = Array.from({length:55},(_,i)=>({sample_id:randomUUID(),device_id:device,session_id:session,model:'V8',source:'live',
  received_at:new Date(Date.UTC(2026,9,26,12,0,i)).toISOString(),measured_at:null,heart_rate_bpm:i===0?null:70+i%5,missing_reason:i===0?'no_result':null,synthetic:true}));
const batch = {schema_version:1,batch_id:randomUUID(),samples:rows};
const ui = setup(async(url,options)=>{const r=await request(options.method,url,undefined,options.headers);return new Response(r.body,{status:r.status,headers:r.headers});});
(async()=>{try{
  await check('actual API rejects unauthenticated dashboard access',async()=>{connect(ui);await settle(ui);assert.match(ui.elements.announcement.textContent,/refused/);assert.equal(ui.elements.latestBpm.textContent,'\u2014');});
  await check('empty real backend renders honest empty dashboard with refresh available',async()=>{connect(ui,token);await settle(ui);assert.match(ui.elements.announcement.textContent,/no permitted watch records/);assert.equal(ui.elements.refreshButton.disabled,false);});
  await check('synthetic wire batch committed and replayed idempotently',async()=>{
    const a=await request('POST','/v1/batches',batch);assert.equal(a.status,200);assert.equal(JSON.parse(a.body).accepted_ids.length,55);
    const b=await request('POST','/v1/batches',batch);assert.deepEqual(JSON.parse(b.body),JSON.parse(a.body));
  });
  await check('actual gateway report and API pages populate dashboard',async()=>{
    const report={schema_version:1,device_id:device,model:'V8',reported_at:rows[54].received_at,last_observation_at:rows[54].received_at,battery_percent:61,connection:'disconnected',pending_samples:0,uploaded_samples:55,recording_enabled:false,synthetic:true};
    assert.equal((await request('POST','/v1/gateway-status',report)).status,200);
    ui.elements.refreshButton.click();await settle(ui);assert.equal(ui.elements.historyRows.children.length,50);assert.equal(ui.elements.sampleCount.textContent,'55');
    assert.equal(ui.elements.watchBattery.textContent,'61% - last reported');assert.equal(ui.elements.phoneQueue.textContent,'0 pending / 55 uploaded');
    assert.match(ui.elements.watchLink.textContent,/Disconnected/);assert.equal(ui.elements.loadMore.disabled,false);
    ui.elements.loadMore.click();await settle(ui);assert.equal(ui.elements.historyRows.children.length,55);assert.equal(ui.elements.loadMore.disabled,true);
    assert(ui.elements.historyRows.children.some(row=>row.children[1].textContent.includes('Unavailable')));
  });
  await check('collision rejected without changing displayed source count',async()=>{
    const collision={schema_version:1,batch_id:randomUUID(),samples:[{...rows[1],heart_rate_bpm:99}]};
    const r=await request('POST','/v1/batches',collision);assert.equal(r.status,200);assert.equal(JSON.parse(r.body).rejected[0].reason,'sample_id_collision');
    ui.elements.refreshButton.click();await settle(ui);assert.equal(ui.elements.sampleCount.textContent,'55');
  });
  await check('real-data policy stays closed and session clear removes displayed data',async()=>{
    const r=await request('POST','/v1/batches',{schema_version:1,batch_id:randomUUID(),samples:[{...rows[2],sample_id:randomUUID(),synthetic:false}]});
    assert.equal(JSON.parse(r.body).rejected[0].reason,'real_data_disabled');
    ui.elements.clearSession.click();assert.equal(ui.elements.latestBpm.textContent,'\u2014');assert.equal(ui.elements.watchLink.textContent,'Not reported');
  });
  console.log(passed+' cross-component integration checks passed. Actual API/SQLite/dashboard JS; simulated DOM/transport, no phone or browser validation.');
}finally{child.stdin.end(JSON.stringify({close:true})+'\n');}})().catch(e=>{console.error(e);process.exitCode=1;});
