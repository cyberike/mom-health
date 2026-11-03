"use strict";
// Hosted candidate scripts with a simulated DOM/HTTP transport, not a browser.
// No listener, network request, credentials, device or real health data.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const core = require("./core.js");
const { setup, connect, idle } = require("./app.test.js");
const hosted = path.join(__dirname, "../railway_candidate");
const fixture = {
  html: fs.readFileSync(path.join(hosted, "dashboard-index.html"), "utf8"),
  source: fs.readFileSync(path.join(hosted, "dashboard-app.js"), "utf8")
};
const json = value => new Response(JSON.stringify(value), { status: 200, headers: { "content-type": "application/json" } });
let passed = 0;
async function test(name, action) { await action(); passed++; console.log("PASS " + name); }

(async () => {
  await test("hosted candidate requests synthetic records using same-origin browser sign-in", async () => {
    const data = core.demoData();
    const ui = setup(url => {
      if (url === "/v1/health") return json({ status: "ready", schema_version: 1, synthetic_only: true });
      if (url.startsWith("/v1/devices?")) return json({ devices: data.devices, next_cursor: null });
      if (url.startsWith("/v1/samples?")) return json({ samples: data.samples, next_cursor: null });
      if (url.startsWith("/v1/gateway-status?")) return json({ status: null, server_received_at: null });
      throw new Error("Unexpected endpoint");
    }, fixture);
    connect(ui); await idle(ui);
    assert.equal(ui.calls.length, 4);
    assert(ui.calls.every(call => call.options.credentials === "same-origin" && !call.options.headers.Authorization));
    assert.equal(ui.elements.modeBadge.textContent, "Backend · synthetic only");
    assert.equal(ui.elements.historyRows.children.length, 24);
    assert.equal(ui.elements.watchLink.textContent, "Not reported");
  });
  await test("real-data backend mode is refused before any record request", async () => {
    const ui = setup(url => {
      if (url === "/v1/health") return json({ status: "ready", schema_version: 1, synthetic_only: false });
      throw new Error("Must not request records");
    }, fixture);
    connect(ui); await idle(ui);
    assert.equal(ui.calls.length, 1);
    assert.match(ui.elements.announcement.textContent, /requires a synthetic-only backend/);
    assert.equal(ui.elements.modeBadge.textContent, "Not connected");
    assert.equal(ui.elements.historyRows.children.length, 1);
  });
  await test("real observation in a synthetic-only response is rejected", async () => {
    const data = core.demoData();
    const ui = setup(url => {
      if (url === "/v1/health") return json({ status: "ready", schema_version: 1, synthetic_only: true });
      if (url.startsWith("/v1/devices?")) return json({ devices: [{ ...data.devices[0], latest: { ...data.devices[0].latest, synthetic: false } }], next_cursor: null });
      throw new Error("Must not request further records");
    }, fixture);
    connect(ui); await idle(ui);
    assert.equal(ui.calls.length, 2);
    assert.match(ui.elements.announcement.textContent, /real data in synthetic-only mode/);
    assert.equal(ui.elements.historyRows.children.length, 1);
  });
  await test("rejected caregiver request never loads records", async () => {
    const ui = setup(() => new Response("{}", { status: 401, headers: { "content-type": "application/json" } }), fixture);
    connect(ui); await idle(ui);
    assert.equal(ui.calls.length, 1);
    assert.match(ui.elements.announcement.textContent, /refused/);
    assert.equal(ui.elements.historyRows.children.length, 1);
  });
  await test("synthetic records and reports are labeled as such in the hero and status cards", async () => {
    const data = core.demoData(); const now = new Date().toISOString();
    const ui = setup(url => {
      if (url === "/v1/health") return json({ status: "ready", schema_version: 1, synthetic_only: true });
      if (url.startsWith("/v1/devices?")) return json({ devices: data.devices, next_cursor: null });
      if (url.startsWith("/v1/samples?")) return json({ samples: data.samples, next_cursor: null });
      return json({ status: { schema_version: 1, device_id: data.devices[0].device_id, model: "V8", connection: "connected", battery_percent: 50, pending_samples: 0, uploaded_samples: 24, reported_at: now, last_observation_at: null, synthetic: true, recording_enabled: true }, server_received_at: now });
    }, fixture);
    connect(ui); await idle(ui);
    assert.match(ui.elements.latestLabel.textContent, /Synthetic test record/);
    assert.match(ui.elements.statusReportTime.textContent, /^Synthetic test report/);
  });
  console.log(passed + " hosted dashboard checks passed using simulated DOM and transport; not browser or hosted-service validation.");
})().catch(error => { console.error(error); process.exitCode = 1; });
