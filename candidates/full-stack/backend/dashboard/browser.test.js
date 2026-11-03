"use strict";
// Real headless Chromium (Playwright) driving the actual dashboard served by the
// actual FastAPI/SQLite backend on 127.0.0.1. Fictional synthetic records and a
// disposable database only; no phone, watch, deployment, external host or real
// health data. Chromium is the only engine checked here. Run from this folder with
// the backend's Python environment first on PATH (the launcher is spawned as
// "python", like integration.test.js):  node browser.test.js
const assert = require("node:assert/strict");
const { randomUUID } = require("node:crypto");
const { requireBrowser, loopbackArgs, startBackend, stopBackend } = require("./browser-support.js");

const chromium = requireBrowser();
// Test constant shared with ../tests/browser_backend.py; never deployed.
const TOKEN = "synthetic-browser-token-not-for-deployment-0001";
const WRONG_TOKEN = "synthetic-browser-token-that-is-wrong-000000001";
const ALPHA = "synthetic-alpha-v8", BRAVO = "synthetic-bravo-v1", CHARLIE = "synthetic-charlie-status";
const NOW = Date.now();

async function api(backend, method, route, body) {
  const response = await fetch(backend.origin + route, { method, headers: { Authorization: "Bearer " + TOKEN, "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  assert.equal(response.status, 200, method + " " + route);
  return response.json();
}

function sample(device, model, minutesAgo, bpm) {
  return { sample_id: randomUUID(), device_id: device, session_id: randomUUID(), model, source: "live",
    received_at: new Date(NOW - minutesAgo * 60000).toISOString(), measured_at: null,
    heart_rate_bpm: bpm, missing_reason: bpm === null ? "no_result" : null, synthetic: true };
}

function report(device, model, fields) {
  return { schema_version: 1, device_id: device, model, reported_at: new Date(NOW - 30000).toISOString(),
    last_observation_at: null, battery_percent: null, connection: "disconnected", pending_samples: 0,
    uploaded_samples: 0, recording_enabled: false, synthetic: true, ...fields };
}

async function seed(backend) {
  const alpha = Array.from({ length: 60 }, (_, i) => sample(ALPHA, "V8", i + 1, i === 7 ? null : 68 + (i % 9)));
  const bravo = [sample(BRAVO, "V1", 5, 81), sample(BRAVO, "V1", 6, 79), sample(BRAVO, "V1", 7, 80)];
  const first = await api(backend, "POST", "/v1/batches", { schema_version: 1, batch_id: randomUUID(), samples: alpha });
  assert.equal(first.accepted_ids.length, 60);
  await api(backend, "POST", "/v1/batches", { schema_version: 1, batch_id: randomUUID(), samples: bravo });
  await api(backend, "POST", "/v1/gateway-status", report(ALPHA, "V8", { battery_percent: 63, connection: "connected", pending_samples: 2, uploaded_samples: 60, recording_enabled: true }));
  await api(backend, "POST", "/v1/gateway-status", report(CHARLIE, "V8", {}));
  return { alpha, bravo };
}

let browser;
async function open(backend, contextOptions = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, ...contextOptions });
  const page = await context.newPage();
  const problems = [], requests = [], failed = [];
  page.on("console", message => { if (message.type() === "error" || message.type() === "warning") problems.push(message.type() + ": " + message.text()); });
  page.on("pageerror", error => problems.push("pageerror: " + error.message));
  page.on("request", request => requests.push(request.url()));
  page.on("requestfailed", request => failed.push({ url: request.url(), error: request.failure() && request.failure().errorText }));
  await page.goto(backend.origin + "/dashboard/");
  await page.waitForFunction(() => document.getElementById("connectForm").getAttribute("aria-busy") === "false");
  const view = { context, page, problems, requests, failed, backend };
  view.api = suffix => requests.filter(url => new URL(url).pathname === suffix).length;
  return view;
}

async function close(view, expected = null) {
  // Every request must stay on the loopback origin, and the page must not log
  // console errors, CSP violations or uncaught exceptions. A check that
  // deliberately provokes an HTTP or connection failure names the resulting
  // Chromium "Failed to load resource" message; nothing else is tolerated.
  for (const url of view.requests) assert(url.startsWith(view.backend.origin + "/"), "non-loopback request " + url);
  assert.deepEqual(view.problems.filter(p => !(expected && /^error: Failed to load resource: /.test(p) && expected.test(p))), []);
  await view.context.close();
}

const idle = page => page.waitForFunction(() => document.getElementById("connectForm").getAttribute("aria-busy") === "false");
const text = (page, id) => page.locator("#" + id).textContent();
const active = page => page.evaluate(() => document.activeElement === document.body || !document.activeElement ? "BODY" : document.activeElement.id || document.activeElement.className || document.activeElement.tagName);

async function connect(page, token = TOKEN) {
  await page.fill("#accessToken", token);
  await page.click("#connectButton");
  await idle(page);
}

function snapshot(page) {
  return page.evaluate(() => {
    const t = id => document.getElementById(id).textContent;
    const select = document.getElementById("deviceSelect");
    return { badge: t("modeBadge"), latestBpm: t("latestBpm"), latestLabel: t("latestLabel"), sampleCount: t("sampleCount"),
      watchLink: t("watchLink"), watchBattery: t("watchBattery"), phoneQueue: t("phoneQueue"), pageNote: t("pageNote"),
      options: [...select.options].map(o => o.value + "=" + o.textContent), selected: select.value, selectDisabled: select.disabled,
      refreshDisabled: document.getElementById("refreshButton").disabled, loadMoreDisabled: document.getElementById("loadMore").disabled,
      chartHidden: document.getElementById("chartWrap").hidden, rows: [...document.querySelectorAll("#historyRows tr")].map(tr => tr.textContent),
      ids: [...document.querySelectorAll("#historyRows td[title]")].map(td => td.title).filter(v => !v.includes("T")),
      token: document.getElementById("accessToken").value };
  });
}

// Holds matching requests inside the browser until released. Used only to make
// "late response" and "repeated click" timing deterministic; the request still
// reaches the real loopback backend after release unless the page aborted it.
async function hold(page, matcher) {
  let release, arrived;
  const released = new Promise(resolve => { release = resolve; });
  const seen = new Promise(resolve => { arrived = resolve; });
  const handler = async route => {
    if (!matcher(new URL(route.request().url()))) return route.continue();
    arrived(route.request().url());
    await released;
    try { await route.continue(); } catch (_) { /* the page already aborted this request */ }
  };
  await page.route("**/v1/**", handler);
  return { seen, release, stop: () => page.unroute("**/v1/**", handler) };
}

async function overflow(page) {
  return page.evaluate(() => {
    const width = window.innerWidth;
    const offenders = [...document.querySelectorAll("body *")].filter(e => {
      if (e.closest(".table-scroll, .sidebar nav, .sr-only, [hidden]")) return false;
      const box = e.getBoundingClientRect();
      return box.width > 0 && (box.right > width + 0.5 || box.left < -0.5);
    }).slice(0, 5).map(e => (e.id || e.className || e.tagName) + ":" + Math.round(e.getBoundingClientRect().right));
    return { scrollWidth: document.documentElement.scrollWidth, width, offenders };
  });
}

let passed = 0;
async function check(name, action) { await action(); passed++; console.log("PASS " + name); }

(async () => {
  browser = await chromium.launch({ args: loopbackArgs() });
  const empty = await startBackend();
  const seeded = await startBackend();
  try {
    const { alpha, bravo } = await seed(seeded);

    await check("initial empty state makes no API request and offers only connect or preview", async () => {
      const view = await open(seeded);
      const state = await snapshot(view.page);
      assert.equal(state.badge, "Not connected");
      assert.equal(state.latestBpm, "—");
      assert.equal(state.sampleCount, "—");
      assert.deepEqual(state.options, ["=No watch data loaded"]);
      assert(state.selectDisabled && state.refreshDisabled && state.loadMoreDisabled && state.chartHidden);
      assert.deepEqual(state.rows, ["No observations in this view."]);
      assert.equal(await text(view.page, "watchLink"), "Not reported");
      assert.equal(await view.page.locator("#moreDevices").isHidden(), true);
      assert.equal(view.requests.filter(url => new URL(url).pathname.startsWith("/v1/")).length, 0);
      await close(view);
    });

    await check("connecting to an empty backend shows an honest empty state", async () => {
      const view = await open(empty);
      await connect(view.page);
      const state = await snapshot(view.page);
      assert.match(await text(view.page, "announcement"), /no permitted watch records have arrived/);
      assert.equal(state.badge, "Backend · synthetic only");
      assert.deepEqual(state.options, ["=No watch data loaded"]);
      assert(state.selectDisabled && !state.refreshDisabled && state.loadMoreDisabled && state.chartHidden);
      assert.equal(state.sampleCount, "—");
      assert.equal(state.watchLink, "Not reported");
      assert.equal(view.api("/v1/samples") + view.api("/v1/gateway-status"), 0);
      await close(view);
    });

    await check("connect, clear session and reconnect restore identical state with no leftovers", async () => {
      const view = await open(seeded);
      await connect(view.page);
      const connected = await snapshot(view.page);
      assert.equal(connected.badge, "Backend · synthetic only");
      assert.equal(connected.selected, ALPHA);
      assert.equal(connected.sampleCount, "60");
      assert.equal(connected.watchBattery, "63% - last reported");
      assert.equal(connected.phoneQueue, "2 pending / 60 uploaded");
      assert.equal(connected.rows.length, 50);
      assert.equal(connected.token, "");
      await view.page.click("#clearSession");
      const cleared = await snapshot(view.page);
      assert.match(await text(view.page, "announcement"), /Session cleared/);
      assert.equal(cleared.badge, "Not connected");
      assert.deepEqual(cleared.options, ["=No watch data loaded"]);
      assert.deepEqual(cleared.rows, ["No observations in this view."]);
      assert(cleared.selectDisabled && cleared.refreshDisabled && cleared.chartHidden);
      assert.equal(cleared.watchBattery, "Not reported");
      assert.deepEqual(await view.page.evaluate(() => [localStorage.length, sessionStorage.length, document.cookie]), [0, 0, ""]);
      await connect(view.page);
      assert.deepEqual(await snapshot(view.page), connected);
      await close(view);
    });

    await check("wrong token is refused without data and the correct token then recovers", async () => {
      const view = await open(seeded);
      await connect(view.page, WRONG_TOKEN);
      assert.match(await text(view.page, "announcement"), /Access was refused/);
      assert.equal(await view.page.locator("#announcement.error").count(), 1);
      const refused = await snapshot(view.page);
      assert.deepEqual(refused.rows, ["No observations in this view."]);
      assert.equal(refused.sampleCount, "—");
      assert.equal(view.api("/v1/devices"), 0);
      await connect(view.page);
      const recovered = await snapshot(view.page);
      assert.equal(recovered.sampleCount, "60");
      assert.equal(await view.page.locator("#announcement.error").count(), 0);
      await close(view, /status of 401 \(Unauthorized\)/);
    });

    await check("repeated clicks start one operation and never duplicate rows", async () => {
      const view = await open(seeded);
      await view.page.fill("#accessToken", TOKEN);
      const connecting = await hold(view.page, url => url.pathname === "/v1/health");
      await view.page.dblclick("#connectButton");
      await connecting.seen;
      await view.page.click("#connectButton", { force: true, clickCount: 3 }).catch(() => {});
      await view.page.locator("#accessToken").press("Enter");
      connecting.release(); await idle(view.page); await connecting.stop();
      assert.equal(view.api("/v1/health"), 1);
      assert.equal(view.api("/v1/devices"), 1);

      const refreshing = await hold(view.page, url => url.pathname === "/v1/devices");
      await view.page.click("#refreshButton", { clickCount: 3 });
      await refreshing.seen;
      await view.page.click("#refreshButton", { force: true }).catch(() => {});
      refreshing.release(); await idle(view.page); await refreshing.stop();
      assert.equal(view.api("/v1/devices"), 2);

      const older = await hold(view.page, url => url.pathname === "/v1/samples" && url.searchParams.has("cursor"));
      await view.page.click("#loadMore", { clickCount: 3 });
      await older.seen;
      await view.page.click("#loadMore", { force: true }).catch(() => {});
      older.release(); await idle(view.page); await older.stop();
      const state = await snapshot(view.page);
      assert.equal(view.requests.filter(url => new URL(url).searchParams.has("cursor")).length, 1);
      assert.equal(state.ids.length, 60);
      assert.equal(new Set(state.ids).size, 60);
      assert.deepEqual(new Set(state.ids), new Set(alpha.map(s => s.sample_id)));
      assert.equal(state.loadMoreDisabled, true);

      const before = view.requests.length;
      for (let i = 0; i < 4; i++) await view.page.click("#demoButton");
      assert.equal(view.requests.length, before);
      assert.equal(await text(view.page, "modeBadge"), "Synthetic preview");
      assert.equal((await snapshot(view.page)).rows.length, 24);
      for (let i = 0; i < 3; i++) await view.page.click("#clearSession");
      assert.equal(view.requests.length, before);
      assert.equal(await text(view.page, "modeBadge"), "Not connected");
      await close(view);
    });

    await check("device changes keep readings and gateway status isolated per watch", async () => {
      const view = await open(seeded);
      await connect(view.page);
      assert.deepEqual((await snapshot(view.page)).options, [ALPHA + "=V8 · " + ALPHA, BRAVO + "=V1 · " + BRAVO, CHARLIE + "=Watch · " + CHARLIE]);
      await view.page.selectOption("#deviceSelect", BRAVO); await idle(view.page);
      let state = await snapshot(view.page);
      assert.equal(state.sampleCount, "3");
      assert.deepEqual(new Set(state.ids), new Set(bravo.map(s => s.sample_id)));
      assert(state.rows.every(row => row.includes("V1 · synthetic · live")));
      assert.equal(state.watchBattery, "Not reported");
      assert.equal(state.watchLink, "Not reported");
      await view.page.selectOption("#deviceSelect", CHARLIE); await idle(view.page);
      state = await snapshot(view.page);
      assert.equal(state.sampleCount, "0");
      assert.deepEqual(state.rows, ["No observations in this view."]);
      assert.equal(state.watchLink, "Disconnected when reported");
      assert.equal(state.watchBattery, "Not reported");
      await view.page.selectOption("#deviceSelect", ALPHA); await idle(view.page);
      await view.page.click("#loadMore"); await idle(view.page);
      state = await snapshot(view.page);
      assert.deepEqual(new Set(state.ids), new Set(alpha.map(s => s.sample_id)));
      assert.equal(state.watchBattery, "63% - last reported");
      await close(view);
    });

    await check("late responses after clear session, preview or a device change are discarded", async () => {
      const view = await open(seeded);
      await view.page.fill("#accessToken", TOKEN);
      let held = await hold(view.page, url => url.pathname === "/v1/samples");
      await view.page.click("#connectButton");
      await held.seen;
      assert.equal(await view.page.locator("#deviceSelect").isDisabled(), true);
      await view.page.click("#clearSession");
      held.release(); await held.stop();
      await view.page.waitForTimeout(300);
      let state = await snapshot(view.page);
      assert.equal(state.badge, "Not connected");
      assert.deepEqual(state.rows, ["No observations in this view."]);
      assert.match(await text(view.page, "announcement"), /Session cleared/);
      assert.equal(await view.page.locator("#announcement.error").count(), 0);
      assert(view.failed.some(f => new URL(f.url).pathname === "/v1/samples" && /ABORTED/.test(f.error)), JSON.stringify(view.failed));

      await connect(view.page);
      held = await hold(view.page, url => url.pathname === "/v1/samples" && url.searchParams.get("device_id") === BRAVO);
      await view.page.selectOption("#deviceSelect", BRAVO);
      await held.seen;
      assert.equal(await view.page.locator("#deviceSelect").isDisabled(), true);
      await view.page.click("#demoButton");
      held.release(); await held.stop();
      await view.page.waitForTimeout(300);
      state = await snapshot(view.page);
      assert.equal(state.badge, "Synthetic preview");
      assert.equal(state.rows.length, 24);
      assert(state.rows.every(row => !row.includes("V1")));
      assert.match(await text(view.page, "announcement"), /SYNTHETIC PREVIEW/);
      assert.equal(state.refreshDisabled, false);
      await close(view);
    });

    await check("backend restart: failed refresh is reported, then the restarted backend serves the same records", async () => {
      const own = await startBackend();
      try {
        await seed(own);
        const view = await open(own);
        await connect(view.page);
        const before = await snapshot(view.page);
        await stopBackend(own, "SIGKILL");
        await view.page.click("#refreshButton"); await idle(view.page);
        assert.equal(await view.page.locator("#announcement.error").count(), 1);
        assert.equal(await view.page.locator("#refreshButton").isDisabled(), false);
        const restarted = await startBackend({ port: own.port, db: own.db });
        own.child = restarted.child;
        await view.page.click("#refreshButton"); await idle(view.page);
        assert.match(await text(view.page, "announcement"), /Refreshed saved backend records/);
        const after = await snapshot(view.page);
        assert.deepEqual(after, before);
        await close(view, /net::ERR_CONNECTION_REFUSED/);
      } finally { await stopBackend(own); }
    });

    const phones = [[320, 640], [360, 740], [375, 812], [414, 896], [768, 1024]];
    await check("mobile and tablet layouts have no horizontal page overflow and usable targets", async () => {
      for (const [width, height] of phones) {
        const view = await open(seeded, { viewport: { width, height }, isMobile: width < 768, hasTouch: true, deviceScaleFactor: 2 });
        for (const state of ["empty", "preview", "connected"]) {
          if (state === "preview") await view.page.click("#demoButton");
          if (state === "connected") await connect(view.page);
          const result = await overflow(view.page);
          assert(result.scrollWidth <= result.width, width + "px " + state + ": " + JSON.stringify(result));
          assert.deepEqual(result.offenders, [], width + "px " + state);
          for (const id of ["clearSession", "connectButton", "demoButton", "deviceSelect", "refreshButton", "rangeSelect", "loadMore", "accessToken"]) {
            const box = await view.page.locator("#" + id).boundingBox();
            assert(box && box.height >= 44, width + "px " + id + " target height " + (box && box.height));
          }
        }
        await close(view);
      }
    });

    await check("200% and 400% zoom reflow without horizontal scrolling and keep controls reachable", async () => {
      // Browser zoom on a 1280x800 window gives the page a 640x400 (200%) or
      // 320x200 (400%) CSS-pixel viewport at 2x/4x device pixels; media queries
      // and layout respond to that CSS viewport exactly as emulated here.
      for (const [width, height, scale] of [[640, 400, 2], [320, 200, 4]]) {
        const view = await open(seeded, { viewport: { width, height }, deviceScaleFactor: scale });
        await connect(view.page);
        const result = await overflow(view.page);
        assert(result.scrollWidth <= result.width, scale * 100 + "%: " + JSON.stringify(result));
        assert.deepEqual(result.offenders, [], scale * 100 + "%");
        const seen = new Set();
        for (let i = 0; i < 30; i++) {
          await view.page.keyboard.press("Tab");
          // styles.css uses smooth scrolling, so wait until the browser's
          // scroll-into-view animation has settled before measuring.
          const focus = await view.page.evaluate(() => new Promise(resolve => {
            let last = -1, stable = 0;
            const step = () => {
              if (scrollY === last) stable++; else { stable = 0; last = scrollY; }
              if (stable < 5) return requestAnimationFrame(step);
              const e = document.activeElement, box = e.getBoundingClientRect();
              resolve({ id: e.id || e.className || e.tagName, inNav: Boolean(e.closest(".sidebar nav")), left: box.left, right: box.right, top: box.top, bottom: box.bottom, vw: innerWidth, vh: innerHeight });
            };
            requestAnimationFrame(step);
          }));
          seen.add(focus.id);
          // WCAG 2.4.11: a focused control is never entirely hidden. Chromium only
          // scrolls the horizontally scrolling mobile nav for fully hidden links, so
          // a partly visible nav link may stay partly clipped; every other control
          // must be fully visible horizontally.
          assert(focus.bottom > 0 && focus.top < focus.vh && focus.right > 0 && focus.left < focus.vw, scale * 100 + "% focused control hidden " + JSON.stringify(focus));
          if (!focus.inNav) assert(focus.left >= -1 && focus.right <= focus.vw + 1, scale * 100 + "% focused control clipped horizontally " + JSON.stringify(focus));
        }
        for (const id of ["clearSession", "accessToken", "connectButton", "demoButton", "deviceSelect", "refreshButton", "rangeSelect", "loadMore"]) assert(seen.has(id), scale * 100 + "% never focused " + id);
        await close(view);
      }
    });

    console.log(passed + " real-browser checks passed. Headless Chromium " + browser.version() + " against a loopback FastAPI/SQLite backend with fictional data; not a phone, other-browser, screen-reader or hosted-deployment validation.");
  } finally {
    await browser.close();
    await stopBackend(seeded);
    await stopBackend(empty);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
