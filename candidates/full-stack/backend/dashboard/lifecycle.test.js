"use strict";
// Lifecycle checks run against BOTH dashboard copies (local app.js and the hosted
// railway_candidate/dashboard-app.js) with the simulated DOM/HTTP harness from
// app.test.js: deferred responses, stale operations, slow bodies, timeouts and
// failures after a successful connect. Not a browser (see browser.test.js); no
// network, credentials or real health data.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const { setup, connect, idle } = require("./app.test.js");

const hosted = path.join(__dirname, "../railway_candidate");
const VARIANTS = [
  ["local", {}],
  ["hosted", { html: fs.readFileSync(path.join(hosted, "dashboard-index.html"), "utf8"), source: fs.readFileSync(path.join(hosted, "dashboard-app.js"), "utf8") }],
];
const A = "synthetic-watch-a", B = "synthetic-watch-b";
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
const tick = () => new Promise(resolve => setImmediate(resolve));
async function ticks(count = 20) { for (let i = 0; i < count; i++) await tick(); }

function sample(device, model, minutesAgo, bpm) {
  return { sample_id: randomUUID(), device_id: device, session_id: randomUUID(), model, source: "live", synthetic: true,
    received_at: new Date(Date.now() - minutesAgo * 60000).toISOString(), measured_at: null, heart_rate_bpm: bpm, missing_reason: null };
}
const rows = { [A]: [sample(A, "V8", 1, 70), sample(A, "V8", 2, 71), sample(A, "V8", 3, 72)], [B]: [sample(B, "V1", 1, 80), sample(B, "V1", 2, 81)] };
const summaries = [{ device_id: A, sample_count: 3, latest: rows[A][0] }, { device_id: B, sample_count: 2, latest: rows[B][0] }];
const report = { schema_version: 1, device_id: A, model: "V8", reported_at: new Date().toISOString(), last_observation_at: null, battery_percent: 47,
  connection: "connected", pending_samples: 0, uploaded_samples: 3, recording_enabled: true, synthetic: true };

// A synthetic backend whose responses can be held per request and released later.
function backend() {
  const state = { held: [], hold: () => false, override: () => null };
  state.answer = url => {
    const forced = state.override(url);
    if (forced) return forced;
    if (url === "/v1/health") return json({ status: "ready", schema_version: 1, synthetic_only: true });
    if (url.startsWith("/v1/devices?")) return json({ devices: summaries, next_cursor: null });
    const device = new URL(url, "http://synthetic.invalid").searchParams.get("device_id");
    if (url.startsWith("/v1/samples?")) return json({ samples: rows[device], next_cursor: null });
    if (url.startsWith("/v1/gateway-status?")) return json(device === A ? { status: report, server_received_at: new Date().toISOString() } : { status: null, server_received_at: null });
    throw new Error("Unexpected simulated request " + url);
  };
  state.handler = (url, options) => {
    if (!state.hold(url)) return state.answer(url);
    return new Promise(resolve => state.held.push({ url, options, release: () => resolve(state.answer(url)) }));
  };
  return state;
}

const shown = ui => ui.elements.historyRows.children.length === 1 && ui.elements.historyRows.children[0].children[0].textContent === "No observations in this view." ? 0 : ui.elements.historyRows.children.length;
let passed = 0;
async function test(name, action) { await action(); passed++; console.log("PASS " + name); }

(async () => {
  for (const [variant, fixture] of VARIANTS) {
    await test(variant + ": a stale sample page after clear session neither renders nor ends the next operation", async () => {
      const api = backend(); const ui = setup(api.handler, fixture);
      api.hold = url => url.startsWith("/v1/samples?");
      connect(ui); await ticks();
      assert.equal(api.held.length, 1);
      const stale = api.held.shift();
      ui.elements.clearSession.click();
      assert.equal(stale.options.signal.aborted, true);
      assert.match(ui.elements.announcement.textContent, /cleared/);
      api.hold = url => url === "/v1/health";
      connect(ui); await ticks();
      assert.equal(api.held.length, 1, "reconnect should be waiting on health");
      stale.release(); await ticks();
      assert.equal(ui.elements.connectButton.disabled, true, "the stale operation must not end the newer one");
      assert.equal(shown(ui), 0);
      assert.match(ui.elements.announcement.textContent, /cleared/);
      api.hold = () => false;
      api.held.shift().release(); await idle(ui);
      assert.equal(shown(ui), 3);
      assert.equal(ui.elements.sampleCount.textContent, "3");
      assert.equal(ui.elements.connectButton.disabled, false);
    });

    await test(variant + ": a stale gateway report after the synthetic preview starts is discarded", async () => {
      const api = backend(); const ui = setup(api.handler, fixture);
      api.hold = url => url.startsWith("/v1/gateway-status?");
      connect(ui); await ticks();
      assert.equal(api.held.length, 1);
      const stale = api.held.shift();
      ui.elements.demoButton.click();
      assert.equal(stale.options.signal.aborted, true);
      stale.release(); await ticks();
      assert.equal(ui.elements.modeBadge.textContent, "Synthetic preview");
      assert.equal(ui.elements.watchLink.textContent, "Not reported");
      assert.equal(ui.elements.watchBattery.textContent, "Not reported");
      assert.match(ui.elements.announcement.textContent, /SYNTHETIC PREVIEW/);
      assert.equal(ui.elements.refreshButton.disabled, false);
    });

    await test(variant + ": a body that finishes streaming after clear session is discarded", async () => {
      const api = backend(); let finish, began;
      const reading = new Promise(resolve => { began = resolve; });
      api.override = url => {
        if (!url.startsWith("/v1/samples?")) return null;
        const text = JSON.stringify({ samples: rows[A], next_cursor: null }), half = Math.floor(text.length / 2);
        const encoder = new TextEncoder();
        const body = new ReadableStream({ start(controller) {
          controller.enqueue(encoder.encode(text.slice(0, half))); began();
          finish = () => { controller.enqueue(encoder.encode(text.slice(half))); controller.close(); };
        } });
        return new Response(body, { status: 200, headers: { "content-type": "application/json" } });
      };
      const ui = setup(api.handler, fixture);
      connect(ui); await reading; await ticks();
      ui.elements.clearSession.click();
      finish(); await ticks();
      assert.equal(shown(ui), 0);
      assert.equal(ui.elements.modeBadge.textContent, "Not connected");
      assert.equal(ui.elements.sampleCount.textContent, "—");
      assert.match(ui.elements.announcement.textContent, /cleared/);
      assert.equal(ui.elements.connectButton.disabled, false);
    });

    await test(variant + ": an unanswered request times out after 12 s with an explicit message and recovers", async () => {
      const timers = new Map(); let next = 1;
      const fake = { setTimeout: (fn, ms) => { timers.set(next, { fn, ms }); return next++; }, clearTimeout: id => { timers.delete(id); } };
      const api = backend(); let silent = false;
      const ui = setup((url, options) => {
        if (!silent) return api.handler(url, options);
        // Like fetch, settle only when the dashboard aborts the request.
        return new Promise((_, reject) => options.signal.addEventListener("abort", () => reject(new DOMException("The operation was aborted.", "AbortError"))));
      }, { ...fixture, ...fake });
      connect(ui); await idle(ui);
      assert.equal(timers.size, 0, "completed requests must clear their timers");
      silent = true;
      ui.elements.refreshButton.click(); await ticks();
      const pending = [...timers.values()];
      assert.deepEqual(pending.map(t => t.ms), [12000]);
      pending[0].fn(); await idle(ui);
      assert.match(ui.elements.announcement.textContent, /timed out or was cancelled/);
      assert.equal(ui.elements.refreshButton.disabled, false);
      assert.equal(ui.elements.connectButton.disabled, false);
      silent = false;
      ui.elements.refreshButton.click(); await idle(ui);
      assert.match(ui.elements.announcement.textContent, /Refreshed saved backend records/);
      assert.equal(shown(ui), 3);
    });

    await test(variant + ": switching watches never shows the previous watch's rows or report, even if the new load fails", async () => {
      const api = backend(); const ui = setup(api.handler, fixture);
      connect(ui); await idle(ui);
      assert.equal(ui.elements.watchBattery.textContent, "47% - last reported");
      api.override = url => url.startsWith("/v1/samples?") && url.includes(B) ? json({ detail: "Storage unavailable" }, 503) : null;
      ui.elements.deviceSelect.value = B; ui.elements.deviceSelect.listeners.change(); await idle(ui);
      assert.equal(shown(ui), 0);
      assert.equal(ui.elements.watchBattery.textContent, "Not reported");
      assert.equal(ui.elements.sampleCount.textContent, "2");
      assert.match(ui.elements.announcement.textContent, /HTTP 503/);
      api.override = () => null;
      ui.elements.deviceSelect.value = A; ui.elements.deviceSelect.listeners.change(); await idle(ui);
      assert.equal(shown(ui), 3);
      assert.equal(ui.elements.watchBattery.textContent, "47% - last reported");
    });

    for (const [code, message] of [[401, /Access was refused/], [403, /denied this request/], [429, /HTTP 429/], [503, /HTTP 503/]]) {
      await test(variant + ": HTTP " + code + " on a later refresh is reported, and reconnecting fully recovers", async () => {
        const api = backend(); const ui = setup(api.handler, fixture);
        connect(ui); await idle(ui);
        assert.equal(shown(ui), 3);
        api.override = url => url.startsWith("/v1/devices?") ? json({ detail: "refused" }, code) : null;
        ui.elements.refreshButton.click(); await idle(ui);
        assert.match(ui.elements.announcement.textContent, message);
        assert.equal(ui.elements.refreshButton.disabled, false);
        assert.equal(ui.elements.watchLink.textContent, "Not reported", "an unconfirmed refresh must not keep showing the old gateway report");
        api.override = () => null;
        connect(ui); await idle(ui);
        assert.match(ui.elements.announcement.textContent, /Backend records loaded/);
        assert.equal(shown(ui), 3);
        assert.equal(ui.elements.watchBattery.textContent, "47% - last reported");
      });
    }
  }
  console.log("\n" + passed + " simulated lifecycle checks passed across the local and hosted dashboards. Not a browser; no network or real data.");
})().catch(error => { console.error(error); process.exitCode = 1; });
