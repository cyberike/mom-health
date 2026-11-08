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
    return new Promise(resolve => state.held.push({ url, options, release: () => resolve(state.answer(url)), respond: response => resolve(response) }));
  };
  return state;
}

const shown = ui => ui.elements.historyRows.children.length === 1 && ui.elements.historyRows.children[0].children[0].textContent === "No observations in this view." ? 0 : ui.elements.historyRows.children.length;
// A Refresh that fails at its health read or device request keeps watch A's loaded rows, the backend's count and the
// status line: as rendered, 5 ms later (a clear deferred by less would show by then; one deferred longer would not,
// nor one queued on a case's fake timers, which the timeout cases therefore require empty), and rendered again from
// the dashboard's own state (a Refresh that emptied that state but left the page as it was would show then).
async function assertKept(ui, why) {
  for (const when of ["at once", "5 ms later", "re-rendered"]) {
    if (when === "5 ms later") await new Promise(resolve => setTimeout(resolve, 5));
    if (when === "re-rendered") { ui.elements.rangeSelect.value = "all"; ui.elements.rangeSelect.listeners.change(); }
    assert.equal(shown(ui), 3, why + ": the loaded rows, " + when);
    assert.equal(ui.elements.sampleCount.textContent, "3", why + ": the backend's count, " + when);
    assert.match(ui.elements.loadedStatus.textContent, /^3 shown · 3 loaded · 3 in source · /, why + ": the status line, " + when);
  }
}
const refusal = (status, headers) => new Response(JSON.stringify({ detail: "refused" }), { status, headers: { "content-type": "application/json", ...headers } });
// Ways a request can fail, as the backend or the network answers it: refusals with and without a wait (gateway.py's
// lockout is a 429 with Retry-After), a network failure, a body that breaks off or does not parse, and a page that is
// not JSON (a proxy's error page, for example).
const FAILURES = [
  ["HTTP 401", () => json({ detail: "Authorization required" }, 401), /Access was refused/],
  ["HTTP 403", () => json({ detail: "refused" }, 403), /denied this request/],
  ["HTTP 429", () => json({ detail: "refused" }, 429), /HTTP 429/],
  ["HTTP 429 asking for a wait", () => refusal(429, { "retry-after": "60" }), /HTTP 429\).* asked to wait about 60 seconds/],
  ["HTTP 503", () => json({ detail: "refused" }, 503), /HTTP 503/],
  ["HTTP 503 asking for a wait", () => refusal(503, { "retry-after": "5" }), /HTTP 503\).* asked to wait about 5 seconds/],
  ["a network failure", () => Promise.reject(new TypeError("Failed to fetch")), /could not be reached/],
  ["a body that breaks off", () => new Response(new ReadableStream({ start(controller) {
    controller.enqueue(new TextEncoder().encode('{"status":')); controller.error(new TypeError("network error"));
  } }), { status: 200, headers: { "content-type": "application/json" } }), /^The backend's answer was cut off\. No current data was confirmed\.$/],
  ["a body that does not parse", () => new Response('{"status": "ready", "schema', { status: 200, headers: { "content-type": "application/json" } }),
    /^Backend returned an unsupported response\. No current data was confirmed\.$/],
  ["a page that is not JSON", () => new Response("<html>synthetic proxy page</html>", { status: 200, headers: { "content-type": "text/html" } }),
    /^Backend returned an unsupported response\. No current data was confirmed\.$/],
];
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

    // The third is answered, but its body stops part-way: the timeout still ends it as a timeout, not as a body cut off.
    for (const [where, unanswered, headed] of [["its health read", url => url === "/v1/health"], ["its device request", url => url.startsWith("/v1/devices?")],
      ["the body of its device request", url => url.startsWith("/v1/devices?"), true]]) {
      await test(variant + ": an unanswered request (" + where + ") times out after 12 s with an explicit message and recovers", async () => {
        const timers = new Map(); let next = 1;
        const fake = { setTimeout: (fn, ms) => { timers.set(next, { fn, ms }); return next++; }, clearTimeout: id => { timers.delete(id); } };
        const api = backend(); let silent = false;
        const aborted = () => new DOMException("The operation was aborted.", "AbortError");
        const ui = setup((url, options) => {
          if (!silent || !unanswered(url)) return api.handler(url, options);
          // Like fetch, a body still being read errors once the dashboard aborts the request.
          if (headed) return new Response(new ReadableStream({ start(controller) {
            controller.enqueue(new TextEncoder().encode('{"devices":'));
            options.signal.addEventListener("abort", () => controller.error(aborted()));
          } }), { status: 200, headers: { "content-type": "application/json" } });
          // Like fetch, settle only when the dashboard aborts the request.
          return new Promise((_, reject) => options.signal.addEventListener("abort", () => reject(aborted())));
        }, { ...fixture, ...fake });
        connect(ui); await idle(ui);
        assert.equal(timers.size, 0, "completed requests must clear their timers");
        silent = true;
        ui.elements.refreshButton.click(); await ticks();
        const pending = [...timers.values()];
        assert.deepEqual(pending.map(t => t.ms), [12000]);
        pending[0].fn(); await idle(ui);
        assert.match(ui.elements.announcement.textContent, /timed out or was cancelled/);
        await assertKept(ui, "a Refresh that times out at " + where);
        assert.equal(timers.size, 0, "nothing is left queued to run later");
        assert.equal(ui.elements.refreshButton.disabled, false);
        assert.equal(ui.elements.connectButton.disabled, false);
        silent = false;
        ui.elements.refreshButton.click(); await idle(ui);
        assert.match(ui.elements.announcement.textContent, /Refreshed saved backend records/);
        assert.equal(shown(ui), 3);
      });
    }

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

    await test(variant + ": a later successful Load older or watch change replaces an earlier error banner", async () => {
      const api = backend(); const ui = setup(api.handler, fixture); let failOlder = false;
      api.override = url => {
        if (!url.startsWith("/v1/samples?") || !url.includes(A)) return null;
        if (!url.includes("cursor=")) return json({ samples: rows[A].slice(0, 2), next_cursor: "synthetic-older-page" });
        return failOlder ? json({ detail: "refused" }, 401) : json({ samples: rows[A].slice(2), next_cursor: null });
      };
      connect(ui); await idle(ui);
      failOlder = true; ui.elements.loadMore.click(); await idle(ui);
      assert.match(ui.elements.announcement.textContent, /Access was refused/);
      failOlder = false; ui.elements.loadMore.click(); await idle(ui);
      assert.equal(shown(ui), 3);
      assert.doesNotMatch(ui.elements.announcement.textContent, /refused|HTTP/, "a stale error must not sit beside freshly loaded rows");
      const previous = api.override;
      api.override = url => url.startsWith("/v1/samples?") && url.includes(B) ? json({ detail: "Storage unavailable" }, 503) : previous(url);
      ui.elements.deviceSelect.value = B; ui.elements.deviceSelect.listeners.change(); await idle(ui);
      assert.match(ui.elements.announcement.textContent, /HTTP 503/);
      ui.elements.deviceSelect.value = A; ui.elements.deviceSelect.listeners.change(); await idle(ui);
      assert.equal(ui.elements.sampleCount.textContent, "3");
      assert.doesNotMatch(ui.elements.announcement.textContent, /HTTP 503/);
    });

    await test(variant + ": a connect that fails after the backend answered never claims 'not connected' beside a backend badge", async () => {
      for (const failing of ["/v1/devices?", "/v1/gateway-status?"]) {
        const api = backend(); const ui = setup(api.handler, fixture);
        api.override = url => url.startsWith(failing) ? json({ detail: "Storage unavailable" }, 503) : null;
        connect(ui); await idle(ui);
        assert.match(ui.elements.announcement.textContent, /HTTP 503/);
        assert.equal(ui.elements.modeBadge.textContent, "Backend · synthetic only");
        assert.doesNotMatch(ui.elements.sourceStatus.textContent, /not connected/, failing);
        assert.match(ui.elements.sourceStatus.textContent, /Backend reached/, failing);
        assert.equal(ui.elements.refreshButton.disabled, false);
      }
    });

    await test(variant + ": a connect that times out names an action that is actually available", async () => {
      const timers = new Map(); let next = 1;
      const fake = { setTimeout: (fn, ms) => { timers.set(next, { fn, ms }); return next++; }, clearTimeout: id => { timers.delete(id); } };
      const ui = setup((url, options) => new Promise((_, reject) => options.signal.addEventListener("abort", () => reject(new DOMException("The operation was aborted.", "AbortError")))), { ...fixture, ...fake });
      connect(ui); await ticks();
      const pending = [...timers.values()];
      assert.deepEqual(pending.map(t => t.ms), [12000]);
      pending[0].fn(); await idle(ui);
      assert.match(ui.elements.announcement.textContent, /timed out/);
      assert.equal(ui.elements.refreshButton.disabled, true);
      assert.doesNotMatch(ui.elements.announcement.textContent, /Refresh/, "Refresh is disabled before a backend was reached");
      assert.match(ui.elements.announcement.textContent, /Connect again/);
      assert.equal(ui.elements.connectButton.disabled, false);
    });

    await test(variant + ": a change event forced while a load is pending never leaves the selector on another watch", async () => {
      // Unreachable by users (the selector is disabled while busy); guards the
      // coupling between the busy early return and the displayed watch.
      const api = backend(); const ui = setup(api.handler, fixture);
      connect(ui); await idle(ui);
      api.hold = url => url.startsWith("/v1/samples?");
      ui.elements.refreshButton.click(); await ticks();
      assert.equal(api.held.length, 1);
      ui.elements.deviceSelect.value = B; ui.elements.deviceSelect.listeners.change();
      api.hold = () => false; api.held.shift().release(); await idle(ui);
      assert.equal(ui.elements.deviceSelect.value, A);
      assert.equal(ui.elements.sampleCount.textContent, "3");
      assert.equal(ui.calls.filter(call => call.url.includes(B)).length, 0);
    });

    await test(variant + ": a cursor from a cleared session is never reused or appended after reconnecting", async () => {
      const api = backend(); const ui = setup(api.handler, fixture); let session = 1;
      api.override = url => url.startsWith("/v1/samples?") && url.includes(A) && !url.includes("cursor=") ? json({ samples: rows[A].slice(0, 2), next_cursor: "synthetic-session-" + session + "-cursor" }) : null;
      connect(ui); await idle(ui);
      api.hold = url => url.includes("cursor=");
      ui.elements.loadMore.click(); await ticks();
      const stale = api.held.shift();
      assert(stale.url.includes("synthetic-session-1-cursor"));
      ui.elements.clearSession.click();
      session = 2; api.hold = () => false;
      connect(ui); await idle(ui);
      stale.respond(json({ samples: rows[A].slice(2), next_cursor: null })); await ticks();
      assert.equal(shown(ui), 2, "the abandoned page must not be appended");
      api.hold = url => url.includes("cursor=");
      ui.elements.loadMore.click(); await ticks();
      assert(api.held[0].url.includes("synthetic-session-2-cursor"));
      api.hold = () => false; api.held.shift().release(); await idle(ui);
      assert.equal(shown(ui), 3);
    });

    await test(variant + ": pagehide during a connect aborts it, discards the late reply and forgets the token", async () => {
      const api = backend(); const ui = setup(api.handler, fixture);
      api.hold = url => url === "/v1/health";
      connect(ui, variant === "local" ? "synthetic-lifecycle-token-one-0000000000001" : ""); await ticks();
      const stale = api.held.shift();
      ui.lifecycle.pagehide();
      assert.equal(stale.options.signal.aborted, true);
      stale.release(); await ticks();
      assert.equal(ui.elements.modeBadge.textContent, "Not connected");
      assert.equal(shown(ui), 0);
      assert.equal(ui.elements.accessToken.value, "");
      api.hold = () => false; const before = ui.calls.length;
      connect(ui); await idle(ui);
      assert(ui.calls.slice(before).every(call => !call.options.headers.Authorization));
    });

    if (variant === "local") await test("local: a request abandoned by Clear session never mixes into a session with another token", async () => {
      const first = "synthetic-lifecycle-token-one-0000000000001", second = "synthetic-lifecycle-token-two-0000000000002";
      const api = backend(); const ui = setup(api.handler, fixture);
      api.hold = url => url.startsWith("/v1/samples?");
      connect(ui, first); await ticks();
      const stale = api.held.shift();
      assert.equal(stale.options.headers.Authorization, "Bearer " + first);
      ui.elements.clearSession.click();
      const before = ui.calls.length;
      api.hold = url => url.startsWith("/v1/gateway-status?");
      connect(ui, second); await ticks();
      const fresh = api.held.shift();
      stale.respond(json({ detail: "Authorization required" }, 401)); await ticks();
      assert.equal(ui.elements.connectButton.disabled, true, "the abandoned request must not end the new session's operation");
      assert.doesNotMatch(ui.elements.announcement.textContent, /refused/);
      api.hold = () => false; fresh.release(); await idle(ui);
      assert(ui.calls.slice(before).every(call => call.options.headers.Authorization === "Bearer " + second));
      assert.equal(shown(ui), 3);
    });

    await test(variant + ": a failed refresh says the status and observations were not loaded instead of claiming none exist", async () => {
      const api = backend(); const ui = setup(api.handler, fixture);
      assert.doesNotMatch(ui.elements.pageNote.textContent, /Refresh/, "Refresh is disabled before connecting");
      connect(ui); await idle(ui);
      assert.equal(ui.elements.watchBattery.textContent, "47% - last reported");
      api.override = url => url.startsWith("/v1/devices?") ? json({ detail: "Storage unavailable" }, 503) : null;
      ui.elements.refreshButton.click(); await idle(ui);
      assert.notEqual(ui.elements.statusReportTime.textContent, "No gateway status report received.");
      assert.match(ui.elements.statusReportTime.textContent, /not loaded/);
      await assertKept(ui, "a Refresh that fails (HTTP 503) at its device request");
      api.override = url => url.startsWith("/v1/samples?") && url.includes(B) ? json({ detail: "Storage unavailable" }, 503) : null;
      ui.elements.deviceSelect.value = B; ui.elements.deviceSelect.listeners.change(); await idle(ui);
      assert.equal(shown(ui), 0);
      assert.doesNotMatch(ui.elements.pageNote.textContent, /End of the currently available page sequence/);
      assert.match(ui.elements.pageNote.textContent, /not loaded/);
      api.override = () => null;
      ui.elements.refreshButton.click(); await idle(ui);
      assert.match(ui.elements.pageNote.textContent, /End of the currently available page sequence/);
    });

    await test(variant + ": a network failure is described in caregiver terms", async () => {
      const api = backend(); let offline = false;
      const ui = setup((url, options) => offline ? Promise.reject(new TypeError("Failed to fetch")) : api.handler(url, options), fixture);
      connect(ui); await idle(ui);
      offline = true; ui.elements.refreshButton.click(); await idle(ui);
      assert.notEqual(ui.elements.announcement.textContent, "Failed to fetch");
      assert.match(ui.elements.announcement.textContent, /could not be reached/);
      assert.match(ui.elements.announcement.textContent, /No current data was confirmed/);
      await assertKept(ui, "a Refresh that cannot reach the backend at all");
      assert.equal(ui.elements.refreshButton.disabled, false);
    });

    await test(variant + ": an empty backend's chart never suggests loading older observations", async () => {
      const api = backend(); api.override = url => url.startsWith("/v1/devices?") ? json({ devices: [], next_cursor: null }) : null;
      const ui = setup(api.handler, fixture);
      connect(ui); await idle(ui);
      assert.equal(ui.elements.loadMore.disabled, true);
      assert.doesNotMatch(ui.elements.chartEmpty.querySelector("p").textContent, /load older/i);
    });

    await test(variant + ": Refresh says so when the chosen watch is not on the first device page", async () => {
      const api = backend();
      api.override = url => !url.startsWith("/v1/devices?") ? null : url.includes("cursor=")
        ? json({ devices: [summaries[1]], next_cursor: null }) : json({ devices: [summaries[0]], next_cursor: A });
      const ui = setup(api.handler, fixture);
      connect(ui); await idle(ui);
      ui.elements.moreDevices.click(); await idle(ui);
      ui.elements.deviceSelect.value = B; ui.elements.deviceSelect.listeners.change(); await idle(ui);
      assert.equal(ui.elements.sampleCount.textContent, "2");
      ui.elements.refreshButton.click(); await idle(ui);
      assert.equal(ui.elements.deviceSelect.value, A);
      assert.match(ui.elements.announcement.textContent, /previously selected watch/);
      assert.match(ui.elements.announcement.textContent, /so another watch is shown; use Load more devices/);
    });

    await test(variant + ": Refresh never claims another watch is shown when none is listed any more", async () => {
      const api = backend(); const ui = setup(api.handler, fixture);
      connect(ui); await idle(ui);
      assert.equal(ui.elements.deviceSelect.value, A);
      api.override = url => url.startsWith("/v1/devices?") ? json({ devices: [], next_cursor: null }) : null;
      ui.elements.refreshButton.click(); await idle(ui);
      assert.equal(ui.elements.deviceSelect.value, "");
      assert.match(ui.elements.announcement.textContent, /The previously selected watch is no longer listed, so no watch is shown\.$/);
      assert.doesNotMatch(ui.elements.announcement.textContent, /another watch/);
    });

    await test(variant + ": Refresh that finds no watch on a first device page with more says no watch is shown and offers Load more devices", async () => {
      const api = backend(); const ui = setup(api.handler, fixture);
      connect(ui); await idle(ui);
      assert.equal(ui.elements.deviceSelect.value, A);
      // Not a page this repository's backends send (they give a cursor only with a full page), but the message must still hold.
      api.override = url => url.startsWith("/v1/devices?") && !url.includes("cursor=") ? json({ devices: [], next_cursor: "synthetic-watch-z" }) : null;
      ui.elements.refreshButton.click(); await idle(ui);
      assert.equal(ui.elements.deviceSelect.value, "");
      assert.equal(ui.elements.moreDevices.hidden, false);
      assert.equal(ui.elements.moreDevices.disabled, false);
      assert.match(ui.elements.announcement.textContent, /The previously selected watch is not on the first page of sources, so no watch is shown; use Load more devices to find it\.$/);
      assert.doesNotMatch(ui.elements.announcement.textContent, /another watch/);
    });

    await test(variant + ": a Refresh that keeps the watch and fails at its sample request has emptied the table", async () => {
      const api = backend(); const ui = setup(api.handler, fixture);
      connect(ui); await idle(ui);
      assert.equal(shown(ui), 3);
      api.override = url => url.startsWith("/v1/samples?") ? json({ detail: "synthetic storage failure" }, 500) : null;
      ui.elements.refreshButton.click(); await idle(ui);
      assert.equal(ui.elements.deviceSelect.value, A);
      assert.match(ui.elements.announcement.textContent, /Backend request failed \(HTTP 500\)/);
      // The reload set the old rows aside before its request, so none of them is left on screen as if current.
      assert.equal(shown(ui), 0);
      assert.match(ui.elements.loadedStatus.textContent, /^0 shown · 0 loaded/);
      assert.equal(ui.elements.pageNote.textContent, "Observations for this watch are not loaded.");
    });

    await test(variant + ": a Refresh that fails at its gateway-status request shows the newly loaded rows", async () => {
      const api = backend(); const ui = setup(api.handler, fixture);
      connect(ui); await idle(ui);
      assert.equal(shown(ui), 3);
      const newest = sample(A, "V8", 0, 99);
      api.override = url => url.startsWith("/v1/samples?") ? json({ samples: [newest, ...rows[A]], next_cursor: null })
        : url.startsWith("/v1/gateway-status?") ? json({ detail: "synthetic status failure" }, 500) : null;
      ui.elements.refreshButton.click(); await idle(ui);
      assert.match(ui.elements.announcement.textContent, /Backend request failed \(HTTP 500\)/);
      assert.equal(shown(ui), 4);
      assert.match(ui.elements.historyRows.children[0].children[1].textContent, /^99\b/, "the newest row is the newly loaded one");
      assert.match(ui.elements.statusReportTime.textContent, /not loaded/);
    });

    if (variant === "local") await test("local: a second Connect with the emptied token field never wipes a token session", async () => {
      const token = "synthetic-lifecycle-token-one-0000000000001";
      const api = backend(); const ui = setup(api.handler, fixture);
      connect(ui, token); await idle(ui);
      assert.equal(ui.elements.accessToken.value, "");
      const before = ui.calls.length;
      connect(ui, ""); await idle(ui);
      assert.equal(ui.calls.length, before, "an empty resubmit must not reconnect without the token");
      assert.equal(shown(ui), 3);
      assert.equal(ui.elements.modeBadge.textContent, "Backend · synthetic only");
      assert.doesNotMatch(ui.elements.announcement.textContent, /refused/);
      assert.match(ui.elements.announcement.textContent, /Already connected/);
    });

    for (const [failure, answer, message] of FAILURES) {
      await test(variant + ": " + failure + " at a later refresh's device request is reported, and reconnecting fully recovers", async () => {
        const api = backend(); const ui = setup(api.handler, fixture);
        connect(ui); await idle(ui);
        assert.equal(shown(ui), 3);
        api.override = url => url.startsWith("/v1/devices?") ? answer() : null;
        ui.elements.refreshButton.click(); await idle(ui);
        assert.match(ui.elements.announcement.textContent, message);
        await assertKept(ui, "a Refresh that fails (" + failure + ") at its device request");
        assert.equal(ui.elements.refreshButton.disabled, false);
        assert.equal(ui.elements.watchLink.textContent, "Not reported", "an unconfirmed refresh must not keep showing the old gateway report");
        api.override = () => null;
        connect(ui); await idle(ui);
        assert.match(ui.elements.announcement.textContent, /Backend records loaded/);
        assert.equal(shown(ui), 3);
        assert.equal(ui.elements.watchBattery.textContent, "47% - last reported");
      });
    }

    await test(variant + ": a 429 says how long the backend asked to wait, and nothing retries automatically", async () => {
      // Fake timers expose a retry scheduled for later as well as an immediate one.
      const timers = new Map(); let next = 1;
      const fake = { setTimeout: (fn, ms) => { timers.set(next, { fn, ms }); return next++; }, clearTimeout: id => { timers.delete(id); } };
      const api = backend(); const ui = setup(api.handler, { ...fixture, ...fake });
      connect(ui); await idle(ui);
      const later = new Date(Date.now() + 120000).toUTCString();
      const answer = (status, headers) => new Response(JSON.stringify({ detail: "synthetic refusal" }), { status, headers: { "content-type": "application/json", ...headers } });
      for (const [headers, wait] of [[{ "retry-after": "45" }, /asked to wait about 45 seconds before trying again/], [{ "retry-after": later }, /asked to wait about 2 minutes before trying again/],
        [{}, /Wait before trying again/], [{ "retry-after": "soon" }, /Wait before trying again/], [{ "retry-after": "Sat, 31 Feb 2026 12:00:00 GMT" }, /Wait before trying again/]]) {
        api.override = url => url.startsWith("/v1/devices?") ? answer(429, headers) : null;
        const before = ui.calls.length;
        ui.elements.refreshButton.click(); await idle(ui);
        assert.match(ui.elements.announcement.textContent, /HTTP 429/);
        assert.match(ui.elements.announcement.textContent, wait);
        assert.match(ui.elements.announcement.textContent, /no automatic retry occurs/);
        assert.equal(ui.calls.slice(before).filter(c => c.url.startsWith("/v1/devices")).length, 1, "the refused request is not repeated");
        assert.equal(timers.size, 0, "no retry is scheduled for later");
        await assertKept(ui, "a Refresh refused (429) at its device request");
      }
      api.override = url => url.startsWith("/v1/devices?") ? answer(503, { "retry-after": "5" }) : null;
      ui.elements.refreshButton.click(); await idle(ui);
      assert.match(ui.elements.announcement.textContent, /HTTP 503\)\. No current data was confirmed\. The backend asked to wait about 5 seconds/);
      assert.equal(timers.size, 0);
      await assertKept(ui, "a Refresh that fails (HTTP 503 asking for a wait) at its device request");
    });

    await test(variant + ": every new session starts from the widest time range", async () => {
      const api = backend(); const ui = setup(api.handler, fixture);
      const choose = value => { ui.elements.rangeSelect.value = value; ui.elements.rangeSelect.listeners.change(); };
      connect(ui); await idle(ui);
      choose("24h"); ui.elements.clearSession.click();
      assert.equal(ui.elements.rangeSelect.value, "all");
      choose("7d"); connect(ui); await idle(ui);
      assert.equal(ui.elements.rangeSelect.value, "all");
      assert.equal(shown(ui), 3);
      choose("24h"); ui.elements.demoButton.click();
      assert.equal(ui.elements.rangeSelect.value, "all");
      assert.equal(shown(ui), 24);
    });

    await test(variant + ": a chosen time range stays in place for the rest of the session", async () => {
      const api = backend(); const ui = setup(api.handler, fixture);
      const choose = value => { ui.elements.rangeSelect.value = value; ui.elements.rangeSelect.listeners.change(); };
      // Watch A's first page offers an older page, so Load older runs too.
      api.override = url => url.startsWith("/v1/samples?") && url.includes("device_id=" + A)
        ? json(url.includes("cursor=") ? { samples: rows[A].slice(2), next_cursor: null } : { samples: rows[A].slice(0, 2), next_cursor: "synthetic-older-page" }) : null;
      connect(ui); await idle(ui);
      choose("24h");
      ui.elements.refreshButton.click(); await idle(ui);
      assert.equal(ui.elements.rangeSelect.value, "24h");
      ui.elements.loadMore.click(); await idle(ui);
      assert.equal(ui.elements.rangeSelect.value, "24h");
      assert.equal(shown(ui), 3);
      ui.elements.deviceSelect.value = B; ui.elements.deviceSelect.listeners.change(); await idle(ui);
      assert.equal(ui.elements.rangeSelect.value, "24h");
      assert.equal(shown(ui), 2);
    });

    await test(variant + ": Refresh re-checks the data mode and requests no records after it changed", async () => {
      const api = backend(); const ui = setup(api.handler, fixture);
      const flags = []; ui.elements.announcement.classList = { toggle: (name, on) => flags.push([name, on]) };
      connect(ui, variant === "local" ? "synthetic-lifecycle-token-mode-00000000000001" : ""); await idle(ui);
      assert.equal(shown(ui), 3);
      api.override = url => url === "/v1/health" ? json({ status: "ready", schema_version: 1, synthetic_only: false }) : null;
      const before = ui.calls.length;
      ui.elements.refreshButton.click(); await idle(ui);
      assert.deepEqual(ui.calls.slice(before).map(c => c.url), ["/v1/health"]);
      assert.match(ui.elements.announcement.textContent, variant === "local"
        ? /data mode changed since you connected: it now accepts real watch data\. Loaded records and the token were removed from this page\. Enter the token and connect again to continue\.$/
        : /now reports that it accepts real watch data\. This hosted candidate requires a synthetic-only backend, so loaded records were removed from this page and no records were requested\. Connect again once the backend is synthetic-only\.$/);
      assert.deepEqual(flags[flags.length - 1], ["error", true], "the change is announced as an error");
      const form = variant === "local" ? ui.elements.accessToken : ui.elements.connectButton;
      assert.equal(form.focusCount, 1, "keyboard focus continues at the Connect form");
      assert.equal(ui.document.activeElement, form);
      assert.equal(form.getAttribute("aria-describedby"), "announcement", "the message describes the control that now has focus");
      assert.equal(ui.elements.modeBadge.textContent, "Not connected");
      assert.equal(shown(ui), 0);
      assert.equal(ui.elements.watchLink.textContent, "Not reported");
      assert.equal(ui.elements.refreshButton.disabled, true);
      connect(ui, variant === "local" ? "synthetic-lifecycle-token-mode-00000000000001" : ""); await idle(ui);
      assert.equal(form.getAttribute("aria-describedby"), null, "a later message no longer describes it");
    });

    await test(variant + ": after a mode change, focus the user moved stays put; on the emptied table or the disabled Refresh button it is handed on", async () => {
      // While Refresh waits for /v1/health, focus is on the range selector (the user moved it; still usable), on
      // the table scroller that clearSession then empties, or still on the disabled Refresh button, which
      // Chromium blurs only at its next frame: a fast answer can be handled before that.
      for (const [where, moved] of [["range selector", ui => ui.elements.rangeSelect],
                                    ["table scroller", ui => Object.assign(ui.document.createElement("div"), { className: "table-scroll" })],
                                    ["disabled Refresh button", ui => ui.elements.refreshButton]]) {
        const api = backend(); const ui = setup(api.handler, fixture);
        connect(ui, variant === "local" ? "synthetic-lifecycle-token-mode-00000000000002" : ""); await idle(ui);
        api.override = url => url === "/v1/health" ? json({ status: "ready", schema_version: 1, synthetic_only: false }) : null;
        api.hold = url => url === "/v1/health";
        ui.elements.refreshButton.click(); await ticks();
        assert.equal(api.held.length, 1, where);
        const target = moved(ui); ui.document.activeElement = target;
        if (where === "disabled Refresh button") assert.equal(target.disabled, true, where);
        api.hold = () => false; api.held.shift().release(); await idle(ui);
        const form = variant === "local" ? ui.elements.accessToken : ui.elements.connectButton;
        assert.match(ui.elements.announcement.textContent, /real watch data/, where);
        if (where === "range selector") {
          assert.equal(ui.document.activeElement, target, where + ": focus stays where the user put it");
          assert.equal(form.focusCount || 0, 0, where);
          assert.equal(form.getAttribute("aria-describedby"), null, where);
        } else {
          assert.equal(ui.document.activeElement, form, where + ": focus continues at the Connect form");
          assert.equal(form.focusCount, 1, where);
          assert.equal(form.getAttribute("aria-describedby"), "announcement", where);
        }
      }
    });

    if (variant === "local") await test("local: Refresh after the backend left real-data mode ends the session too", async () => {
      // app.py serves real-data mode only with a token, so this session has one.
      const api = backend(); const ui = setup(api.handler, fixture);
      let synthetic = false;
      api.override = url => url === "/v1/health" ? json({ status: "ready", schema_version: 1, synthetic_only: synthetic }) : null;
      connect(ui, "synthetic-lifecycle-token-mode-00000000000003"); await idle(ui);
      assert.equal(ui.elements.modeBadge.textContent, "Private backend data");
      assert.equal(shown(ui), 3);
      ui.elements.refreshButton.click(); await idle(ui);
      assert.match(ui.elements.announcement.textContent, /Refreshed saved backend records/, "an unchanged mode refreshes as before");
      synthetic = true;
      ui.elements.refreshButton.click(); await idle(ui);
      assert.match(ui.elements.announcement.textContent, /it now accepts synthetic test data only\. Loaded records and the token were removed from this page\. Enter the token and connect again to continue\.$/);
      assert.equal(ui.elements.accessToken.focusCount, 1);
      assert.equal(ui.elements.modeBadge.textContent, "Not connected");
      assert.equal(shown(ui), 0);
    });

    if (variant === "local") await test("local (defensive; app.py cannot serve this): a mode change seen without a token mentions no token", async () => {
      // app.py refuses real-data mode without a token, so a tokenless session meets a switch to it as a 401.
      // Another backend might answer /v1/health without one; the message then names no removed token.
      const api = backend(); const ui = setup(api.handler, fixture);
      let synthetic = false;
      api.override = url => url === "/v1/health" ? json({ status: "ready", schema_version: 1, synthetic_only: synthetic }) : null;
      connect(ui); await idle(ui);
      synthetic = true;
      ui.elements.refreshButton.click(); await idle(ui);
      assert.match(ui.elements.announcement.textContent, /it now accepts synthetic test data only\. Loaded records were removed from this page\. Connect again to continue\.$/);
      assert.equal(ui.elements.accessToken.focusCount, 1);
    });

    await test(variant + ": a null /v1/health body is refused on Connect and on Refresh", async () => {
      const api = backend(); const ui = setup(api.handler, fixture);
      let blank = true;
      api.override = url => url === "/v1/health" && blank ? json(null) : null;
      connect(ui); await idle(ui);
      assert.match(ui.elements.announcement.textContent, /Backend contract is not supported/);
      assert.equal(ui.calls.length, 1, "nothing else is requested");
      blank = false; connect(ui); await idle(ui);
      assert.equal(shown(ui), 3);
      blank = true; ui.elements.refreshButton.click(); await idle(ui);
      assert.match(ui.elements.announcement.textContent, /Backend contract is not supported/);
      await assertKept(ui, "a Refresh refused at its health read");
    });

    for (const [failure, answer, message] of FAILURES) {
      await test(variant + ": a Refresh that fails (" + failure + ") at its health read keeps the loaded rows and count", async () => {
        const api = backend(); const ui = setup(api.handler, fixture);
        connect(ui); await idle(ui);
        assert.equal(shown(ui), 3);
        // HTTP 401: as when a token-less local session meets a backend that switched to real-data mode, which then requires a token.
        api.override = url => url === "/v1/health" ? answer() : null;
        const before = ui.calls.length;
        ui.elements.refreshButton.click(); await idle(ui);
        assert.match(ui.elements.announcement.textContent, message);
        assert.equal(ui.calls.length - before, 1, "nothing is requested after the failed health read");
        await assertKept(ui, "a Refresh that fails (" + failure + ") at its health read");
        assert.equal(ui.elements.watchLink.textContent, "Not reported", "an unconfirmed refresh must not keep showing the old gateway report");
        assert.match(ui.elements.statusReportTime.textContent, /not loaded/);
      });
    }
  }
  console.log("\n" + passed + " simulated lifecycle checks passed across the local and hosted dashboards. Not a browser; no network or real data.");
})().catch(error => { console.error(error); process.exitCode = 1; });
