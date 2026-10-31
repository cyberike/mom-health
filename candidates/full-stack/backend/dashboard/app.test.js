"use strict";
// Minimal DOM/HTTP simulation, not a browser or rendering test. No network requests are made.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const core = require("./core.js");
class Element {
  constructor(name) { this.name = name; this.value = ""; this.children = []; this.listeners = {}; this.attributes = {}; this.disabled = false; this.hidden = false; this.classList = { toggle() {} }; this._text = ""; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(event, handler) { this.listeners[event] = handler; }
  querySelector(selector) { this.parts ||= {}; return this.parts[selector] ||= new Element(selector); }
  click() { if (!this.disabled && this.listeners.click) this.listeners.click({ preventDefault() {} }); }
}
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const source = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
function setup(handler, fixture = {}) {
  const page = fixture.html === undefined ? html : fixture.html;
  const script = fixture.source === undefined ? source : fixture.source;
  const elements = Object.fromEntries([...page.matchAll(/\bid="([^"]+)"/g)].map(m => [m[1], new Element(m[1])]));
  elements.rangeSelect.value = "all";
  const calls = [], lifecycle = {};
  const window = { MomHealthDashboard: core, addEventListener(event, action) { lifecycle[event] = action; } };
  const context = { window, document: { getElementById: id => elements[id], createElement: tag => new Element(tag), createElementNS: (namespace, tag) => new Element(tag) }, location: { protocol: "http:" }, URLSearchParams, AbortController, TextDecoder, Uint8Array, Intl, Date, setTimeout, clearTimeout,
    fetch: async (url, options) => { calls.push({ url, options }); return handler(url, options); } };
  vm.runInNewContext(script, context, { filename: "app.js" });
  return { elements, calls, lifecycle };
}
const json = value => new Response(JSON.stringify(value), { status: 200, headers: { "content-type": "application/json" } });
async function idle(ui) { for (let i = 0; i < 100; i++) { if (!ui.elements.connectButton.disabled) return; await new Promise(resolve => setImmediate(resolve)); } throw new Error("UI operation did not finish"); }
function connect(ui, token = "") { ui.elements.accessToken.value = token; ui.elements.connectForm.listeners.submit({ preventDefault() {} }); }
let passed = 0;
async function test(name, action) { await action(); passed++; process.stdout.write("PASS " + name + "\n"); }
module.exports = { setup, connect, idle };
if (require.main === module) (async () => {
  await test("initial state is empty and makes no request", async () => {
    const ui = setup(() => { throw new Error("Unexpected network simulation"); }); assert.equal(ui.calls.length, 0); assert.equal(ui.elements.latestBpm.textContent, "—"); assert.equal(ui.elements.watchLink.textContent, "Not reported"); assert(ui.elements.refreshButton.disabled);
  });
  await test("explicit demo renders fictional records without fetch", async () => {
    const ui = setup(() => { throw new Error("Unexpected network simulation"); }); ui.elements.demoButton.click(); assert.equal(ui.calls.length, 0); assert.equal(ui.elements.modeBadge.textContent, "Synthetic preview"); assert.equal(ui.elements.historyRows.children.length, 24); assert(ui.elements.announcement.textContent.includes("SYNTHETIC PREVIEW")); assert(!ui.elements.chartWrap.hidden); ui.elements.clearSession.click(); assert.equal(ui.elements.modeBadge.textContent, "Not connected"); assert.equal(ui.elements.historyRows.children.length, 1);
  });
  await test("invalid token fails before a request", async () => {
    const ui = setup(() => { throw new Error("Unexpected network simulation"); }); connect(ui, "short"); await idle(ui); assert.equal(ui.calls.length, 0); assert(ui.elements.announcement.textContent.includes("Token format is invalid")); assert.equal(ui.elements.accessToken.value, "");
  });
  await test("backend connection reads same-origin data and status with token only in memory", async () => {
    const data = core.demoData(); const device = data.devices[0];
    const ui = setup(url => { if (url === "/v1/health") return json({ status: "ready", schema_version: 1, synthetic_only: true }); if (url.startsWith("/v1/devices?")) return json({ devices: [device], next_cursor: null }); if (url.startsWith("/v1/samples?")) return json({ samples: data.samples, next_cursor: null }); if (url.startsWith("/v1/gateway-status?")) return json({ status: { schema_version: 1, device_id: device.device_id, model: "V8", connection: "connected", battery_percent: 64, pending_samples: 21, uploaded_samples: 0, reported_at: new Date().toISOString(), last_observation_at: null, synthetic: true, recording_enabled: false }, server_received_at: new Date().toISOString() }); throw new Error("Unexpected endpoint"); });
    const token = "synthetic-fixture-token-not-a-secret"; connect(ui, token); await idle(ui); assert.equal(ui.calls.length, 4); assert(ui.calls.every(c => c.options.method === "GET" && c.options.headers.Authorization === "Bearer " + token)); assert.equal(ui.elements.accessToken.value, ""); assert.equal(ui.elements.watchLink.textContent, "Connected when reported"); assert.equal(ui.elements.watchBattery.textContent, "64% - last reported"); assert.equal(ui.elements.phoneQueue.textContent, "21 pending / 0 uploaded"); assert.equal(ui.elements.historyRows.children.length, 24); ui.lifecycle.pagehide(); assert.equal(ui.elements.latestBpm.textContent, "—"); assert.equal(ui.elements.watchLink.textContent, "Not reported");
  });
  await test("status-only device works without invented HR", async () => {
    const ui = setup(url => { if (url === "/v1/health") return json({ status: "ready", schema_version: 1, synthetic_only: true }); if (url.startsWith("/v1/devices?")) return json({ devices: [{ device_id: "synthetic-status", sample_count: 0, latest: null }], next_cursor: null }); if (url.startsWith("/v1/samples?")) return json({ samples: [], next_cursor: null }); return json({ status: null, server_received_at: null }); }); connect(ui); await idle(ui); assert.equal(ui.elements.latestBpm.textContent, "—"); assert.equal(ui.elements.sampleCount.textContent, "0"); assert.equal(ui.elements.watchLink.textContent, "Not reported");
  });
  await test("foreign observation page is rejected without rendering data", async () => {
    const data = core.demoData(); const ui = setup(url => { if (url === "/v1/health") return json({ status: "ready", schema_version: 1, synthetic_only: true }); if (url.startsWith("/v1/devices?")) return json({ devices: data.devices, next_cursor: null }); return json({ samples: [{ ...data.samples[0], device_id: "foreign-device" }], next_cursor: null }); }); connect(ui); await idle(ui); assert(ui.elements.announcement.textContent.includes("another watch")); assert.equal(ui.elements.historyRows.children.length, 1);
  });
  await test("refresh removing selected watch clears old readings and gateway status", async () => {
    const data = core.demoData(); let available = true;
    const ui = setup(url => {
      if (url === "/v1/health") return json({status:"ready",schema_version:1,synthetic_only:true});
      if (url.startsWith("/v1/devices?")) return json({devices:available?data.devices:[],next_cursor:null});
      if (url.startsWith("/v1/samples?")) return json({samples:data.samples,next_cursor:null});
      return json({status:{schema_version:1,device_id:data.devices[0].device_id,model:"V8",connection:"connected",battery_percent:64,pending_samples:0,uploaded_samples:24,reported_at:new Date().toISOString(),last_observation_at:null,synthetic:true,recording_enabled:false},server_received_at:new Date().toISOString()});
    });
    connect(ui); await idle(ui); assert.equal(ui.elements.historyRows.children.length,24); assert.equal(ui.elements.watchBattery.textContent,"64% - last reported");
    available=false; ui.elements.refreshButton.click(); await idle(ui);
    assert.equal(ui.elements.historyRows.children.length,1); assert.equal(ui.elements.watchBattery.textContent,"Not reported"); assert.equal(ui.elements.latestLabel.textContent,"No reading loaded");
  });
  await test("refresh changing watch cannot show former readings if new page fails", async () => {
    const data=core.demoData(); let switched=false;
    const ui=setup(url=>{
      if(url==="/v1/health")return json({status:"ready",schema_version:1,synthetic_only:true});
      if(url.startsWith("/v1/devices?"))return json({devices:switched?[{device_id:"new-watch",sample_count:0,latest:null}]:data.devices,next_cursor:null});
      if(url.startsWith("/v1/samples?"))return switched?new Response("{}",{status:503,headers:{"content-type":"application/json"}}):json({samples:data.samples,next_cursor:null});
      return json({status:null,server_received_at:null});
    });
    connect(ui);await idle(ui);assert.equal(ui.elements.historyRows.children.length,24);
    switched=true;ui.elements.refreshButton.click();await idle(ui);
    assert.equal(ui.elements.historyRows.children.length,1);assert.equal(ui.elements.latestLabel.textContent,"No reading loaded");assert.match(ui.elements.announcement.textContent,/HTTP 503/);
  });
  await test("clear session aborts pending request and excludes late callbacks", async () => {
    let release; const ui = setup(() => new Promise(resolve => { release = resolve; })); connect(ui); assert(ui.elements.connectButton.disabled); const signal = ui.calls[0].options.signal; ui.elements.clearSession.click(); assert(signal.aborted); release(json({ status: "ready", schema_version: 1, synthetic_only: true })); await new Promise(resolve => setImmediate(resolve)); assert.equal(ui.elements.modeBadge.textContent, "Not connected"); assert.equal(ui.elements.latestBpm.textContent, "—"); assert.equal(ui.calls.length, 1);
  });
  process.stdout.write("\n" + passed + " simulated UI-flow checks passed. Not visual/browser QA; no network or real data.\n");
})().catch(error => { console.error(error); process.exitCode = 1; });
