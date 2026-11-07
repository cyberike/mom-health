"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const core = require("./core.js");
let passed = 0;
function test(name, action) { action(); passed++; process.stdout.write("PASS " + name + "\n"); }
const now = Date.parse("2026-10-24T18:00:00Z");
const demo = core.demoData(now);
const first = demo.samples[0];
test("preview is explicitly synthetic and bounded", () => { assert.equal(demo.samples.length, 24); assert(demo.samples.every(s => s.synthetic)); assert.equal(demo.devices[0].sample_count, 24); });
test("sample is immutable", () => { assert(Object.isFrozen(first)); assert.throws(() => { first.heart_rate_bpm = 99; }); });
test("preview has honest nullable values", () => { const nil = demo.samples.find(s => s.heart_rate_bpm === null); assert.equal(nil.missing_reason, "no_result"); assert.equal(nil.measured_at, null); });
test("bad HR values rejected", () => { for (const bpm of [0, 251, true, "75", Infinity, NaN]) assert.throws(() => core.sample({ ...first, heart_rate_bpm: bpm })); });
test("missing reason required for null", () => { assert.throws(() => core.sample({ ...first, heart_rate_bpm: null, missing_reason: null })); });
test("value with reason rejected", () => { assert.throws(() => core.sample({ ...first, missing_reason: "unknown" })); });
test("unknown model or provenance rejected", () => { for (const changes of [{ model: "V99" }, { source: "inferred" }, { synthetic: "true" }]) assert.throws(() => core.sample({ ...first, ...changes })); });
test("unverified real-model and history values cannot be displayed", () => { for (const changes of [{ model: "V4", source: "live" }, { model: "V8", source: "history" }, { model: "V1", source: "history" }]) assert.throws(() => core.sample({ ...first, ...changes, synthetic: false })); assert.equal(core.sample({ ...first, model: "V4", synthetic: false, heart_rate_bpm: null, missing_reason: "unsupported" }).heart_rate_bpm, null); assert.equal(core.sample({ ...first, model: "V8", source: "live", synthetic: false }).heart_rate_bpm, first.heart_rate_bpm); });
test("timestamp requires timezone", () => { assert.throws(() => core.sample({ ...first, received_at: "2026-10-24T18:00:00" })); assert.throws(() => core.sample({ ...first, received_at: "not-a-dateZ" })); });
test("identifier lengths bounded", () => { assert.throws(() => core.sample({ ...first, sample_id: "x".repeat(65) })); assert.throws(() => core.sample({ ...first, device_id: "" })); });
test("valid sample page preserves identity", () => { assert.equal(core.samplePage({ samples: [first], next_cursor: null }, first.device_id).samples.length, 1); });
test("foreign device page is rejected atomically", () => { assert.throws(() => core.samplePage({ samples: [first], next_cursor: null }, "another-watch")); });
test("oversized or malformed pages rejected", () => { assert.throws(() => core.samplePage({ samples: Array(101).fill(first), next_cursor: null }, first.device_id)); assert.throws(() => core.samplePage({ samples: [first] }, first.device_id)); });
test("device summary validates latest identity", () => { assert.equal(core.devicePage({ devices: demo.devices, next_cursor: null }).devices.length, 1); assert.throws(() => core.devicePage({ devices: [{ ...demo.devices[0], device_id: "wrong" }], next_cursor: null })); });
test("device counts bounded to safe integers", () => { for (const count of [-1, 1.1, Number.MAX_SAFE_INTEGER + 1, "12"]) assert.throws(() => core.devicePage({ devices: [{ ...demo.devices[0], sample_count: count }], next_cursor: null })); });
const report = { status: { schema_version: 1, device_id: first.device_id, model: "V8", reported_at: "2026-10-24T17:59:00Z", last_observation_at: null, battery_percent: 0, connection: "connected", pending_samples: 21, uploaded_samples: 0, recording_enabled: true, synthetic: true }, server_received_at: "2026-10-24T18:00:00Z" };
test("gateway status preserves real zero battery and counts", () => { const status = core.gatewayStatus(report, first.device_id); assert.equal(status.battery_percent, 0); assert.equal(status.pending_samples, 21); assert.equal(status.uploaded_samples, 0); assert(Object.isFrozen(status)); });
test("absent gateway status stays unavailable", () => { assert.equal(core.gatewayStatus({ status: null, server_received_at: null }, first.device_id), null); assert.throws(() => core.gatewayStatus({ status: null, server_received_at: "2026-10-24T18:00:00Z" }, first.device_id)); });
test("gateway status rejects foreign identity", () => { assert.throws(() => core.gatewayStatus(report, "another-watch")); });
test("gateway status rejects invalid battery and queue counts", () => { for (const changes of [{ battery_percent: -1 }, { battery_percent: 101 }, { battery_percent: "64" }, { pending_samples: -1 }, { uploaded_samples: 1.5 }]) assert.throws(() => core.gatewayStatus({ ...report, status: { ...report.status, ...changes } }, first.device_id)); });
test("gateway status rejects false provenance and invalid timestamps", () => { for (const changes of [{ synthetic: "true" }, { recording_enabled: 1 }, { connection: "live" }, { model: "other" }, { reported_at: "yesterday" }]) assert.throws(() => core.gatewayStatus({ ...report, status: { ...report.status, ...changes } }, first.device_id)); });
test("nullable battery is never inferred from HR", () => { assert.equal(core.gatewayStatus({ ...report, status: { ...report.status, battery_percent: null } }, first.device_id).battery_percent, null); });
test("null device summary accepted without invented value", () => { assert.equal(core.devicePage({ devices: [{ device_id: "empty", sample_count: 0, latest: null }], next_cursor: null }).devices[0].latest, null); });
test("cursors bounded", () => { assert.throws(() => core.devicePage({ devices: [], next_cursor: "x".repeat(257) })); });
test("exact duplicate ID is not displayed twice", () => { assert.equal(core.appendUnique([first], [first]).length, 1); });
test("conflicting duplicate ID is rejected", () => { assert.throws(() => core.appendUnique([first], [{ ...first, heart_rate_bpm: 90 }])); });
test("equal time and value preserve independent observations", () => { assert.equal(core.appendUnique([first], [{ ...first, sample_id: "another-id" }]).length, 2); });
test("page merge does not mutate original or silently evict", () => { const original = [first]; assert.throws(() => core.appendUnique(original, [{ ...first, sample_id: "other" }], 1)); assert.equal(original.length, 1); });
test("range filtering and ordering use receipt time", () => { const old = { ...first, sample_id: "old", received_at: "2026-10-01T00:00:00Z" }; const sorted = core.filterRows([...demo.samples, old], "24h", now); assert.equal(sorted.length, 24); assert(Date.parse(sorted[0].received_at) > Date.parse(sorted[23].received_at)); });
test("future receipt outside current range stays visible only under all", () => { const future = { ...first, received_at: "2026-10-25T00:00:00Z" }; assert.equal(core.filterRows([future], "24h", now).length, 0); assert.equal(core.filterRows([future], "all", now).length, 1); });
test("unknown range rejected", () => { assert.throws(() => core.filterRows([first], "arbitrary", now)); });
test("age labels do not claim live or medical validity", () => { assert.equal(core.ageLabel(null, now), "Unknown"); assert.equal(core.ageLabel("2026-10-24T18:05:00Z", now), "Clock ahead"); assert.equal(core.ageLabel("2026-10-24T17:58:00Z", now), "2 min ago"); });
test("no external libraries or asset requests", () => { const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8"); assert(!/(?:src|href)=["']https?:\/\//i.test(html)); assert(!/<script(?![^>]*\bsrc=)[^>]*>/i.test(html)); assert(!/\son\w+=/i.test(html)); });
test("rendering avoids HTML injection and browser persistence", () => { const app = fs.readFileSync(path.join(__dirname, "app.js"), "utf8"); assert(!/innerHTML|outerHTML|insertAdjacentHTML|localStorage|sessionStorage|document\.cookie|indexedDB/.test(app)); assert(app.includes("textContent")); assert(app.includes('redirect: "error"')); assert(app.includes('credentials: "omit"')); });
test("API requests are same-origin GET only", () => { const app = fs.readFileSync(path.join(__dirname, "app.js"), "utf8"); assert(app.includes('method: "GET"')); assert(!/https?:\/\//.test(app.replace("http://www.w3.org/2000/svg", ""))); assert(!/setInterval|sendBeacon|WebSocket/.test(app)); });
test("Retry-After waits are worded plainly and never invented", () => {
  const at = Date.parse("2026-11-07T12:00:00Z");
  const cases = [[null, ""], ["", ""], ["soon", ""], ["-5", ""], ["0", "a moment"], ["1", "a moment"], ["2", "about 2 seconds"], ["45", "about 45 seconds"],
    ["89", "about 89 seconds"], ["90", "about 2 minutes"], ["5399", "about 90 minutes"], ["5400", "about 2 hours"], ["86400", "about 24 hours"], ["86401", "more than a day"],
    ["Sat, 07 Nov 2026 12:00:30 GMT", "about 30 seconds"], ["Sat, 07 Nov 2026 11:00:00 GMT", "a moment"], ["Saturday, 07-Nov-26 12:00:30 GMT", ""],
    // Date-shaped but impossible, wrong weekday or misread by Date.parse: ignored, never rolled over.
    ["Sat, 31 Feb 2026 12:00:00 GMT", ""], ["Sat, 07 Nov 2026 24:30:00 GMT", ""], ["Sat, 07 Nov 2026 12:61:00 GMT", ""], ["Sun, 07 Nov 2026 12:00:30 GMT", ""],
    ["Thu, 01 Jan 0049 00:00:00 GMT", ""]];
  for (const [value, label] of cases) assert.equal(core.retryAfterLabel(value, at), label, String(value));
  // Waits round up: 89.4 s is "about 2 minutes", never fewer seconds than asked.
  assert.equal(core.retryAfterLabel("Sat, 07 Nov 2026 12:01:30 GMT", at + 600), "about 2 minutes");
});
test("clear session and lifecycle cancel outstanding work", () => { const app = fs.readFileSync(path.join(__dirname, "app.js"), "utf8"); assert(app.includes('window.addEventListener("pagehide"')); assert(app.includes("controller.abort()")); assert(app.includes('token = ""')); assert(app.includes("epoch === generation")); });
test("honest missing telemetry and explicit preview labels", () => { const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8"); assert((html.match(/Not reported/g) || []).length >= 3); assert(html.includes("Try synthetic preview")); assert(html.includes("Accuracy is unverified")); assert(html.includes("Hands-off collection is still being built")); });
test("all app element IDs exist in the HTML", () => { const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8"); const app = fs.readFileSync(path.join(__dirname, "app.js"), "utf8"); const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1])); const objectList = app.match(/Object\.fromEntries\(\[([^\]]+)\]/)[1]; for (const match of objectList.matchAll(/"([^"]+)"/g)) assert(ids.has(match[1]), match[1]); });
test("HTML has entity hearts and no control characters", () => { const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8"); assert(html.includes("&#9829;")); assert(!/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(html)); });
process.stdout.write("\n" + passed + " dashboard checks passed. No browser, server, device or real data used.\n");
