"use strict";
// Real headless Chromium against the hosted dashboard variant
// (../railway_candidate/dashboard-index.html + dashboard-app.js) served by the
// unmodified Railway gateway.py and by the inactive direct_candidate.py, each on
// 127.0.0.1. The fictional host synthetic.example.com resolves to loopback inside
// this browser only (every other name fails to resolve). For gateway.py the test
// supplies the x-forwarded-proto header its trusted proxy would set. Basic sign-in
// is real Chromium behavior. Nothing here verifies TLS, a proxy, Railway edge
// identity, a public domain or a deployment; credentials and records are fictional.
// Run from this folder with the backend's Python environment first on PATH:
//   node hosted-browser.test.js
const assert = require("node:assert/strict");
const http = require("node:http");
const { randomUUID } = require("node:crypto");
const { requireBrowser, loopbackArgs, startBackend, stopBackend } = require("./browser-support.js");

const chromium = requireBrowser();
// Test constants shared with ../tests/browser_backend.py; never deployed.
const HOST = "synthetic.example.com";
const PHONE_TOKEN = "synthetic-browser-token-not-for-deployment-0001";
const CAREGIVER = { username: "fictional-caregiver", password: "fictional-caregiver-password-not-for-use-1234" };
const WRONG = { username: "fictional-caregiver", password: "fictional-caregiver-password-that-is-wrong-01" };
const DEVICE = "synthetic-hosted-v8";

function phonePost(backend, mode, route, body) {
  return new Promise((resolve, reject) => {
    const payload = Buffer.from(JSON.stringify(body));
    const headers = { Host: HOST, Authorization: "Bearer " + PHONE_TOKEN, "Content-Type": "application/json", "Content-Length": payload.length };
    if (mode === "gateway") headers["X-Forwarded-Proto"] = "https";
    const request = http.request({ host: "127.0.0.1", port: backend.port, method: "POST", path: route, headers, agent: false }, response => {
      const chunks = []; response.on("data", chunk => chunks.push(chunk));
      response.on("end", () => resolve({ status: response.statusCode, body: Buffer.concat(chunks).toString() }));
    });
    request.on("error", reject); request.end(payload);
  });
}

function unsignedGet(backend, route) {
  // A caregiver read with no Authorization header: one failure for gateway.py's role-wide budget.
  return new Promise((resolve, reject) => {
    const request = http.request({ host: "127.0.0.1", port: backend.port, method: "GET", path: route, agent: false,
      headers: { Host: HOST, "X-Forwarded-Proto": "https" } }, response => { response.resume(); response.on("end", () => resolve(response.statusCode)); });
    request.on("error", reject); request.end();
  });
}

async function seed(backend, mode) {
  const samples = [72, 74, null].map((bpm, i) => ({ sample_id: randomUUID(), device_id: DEVICE, session_id: randomUUID(), model: "V8", source: "live",
    received_at: new Date(Date.now() - (i + 1) * 60000).toISOString(), measured_at: null, heart_rate_bpm: bpm, missing_reason: bpm === null ? "no_result" : null, synthetic: true }));
  const result = await phonePost(backend, mode, "/v1/batches", { schema_version: 1, batch_id: randomUUID(), samples });
  assert.equal(result.status, 200, result.body);
  assert.equal(JSON.parse(result.body).accepted_ids.length, 3);
}

async function signIn(browser, mode, credentials) {
  const context = await browser.newContext({ httpCredentials: credentials, extraHTTPHeaders: mode === "gateway" ? { "x-forwarded-proto": "https" } : {} });
  const page = await context.newPage();
  const view = { context, page, problems: [], requests: [] };
  page.on("console", message => { if (message.type() === "error" || message.type() === "warning") view.problems.push(message.text()); });
  page.on("pageerror", error => view.problems.push("pageerror: " + error.message));
  page.on("request", request => view.requests.push(request.url()));
  view.navigation = await page.goto("http://" + HOST + "/dashboard/");
  return view;
}

async function finish(view, expectedResourceError = null) {
  for (const url of view.requests) assert(url.startsWith("http://" + HOST + "/"), "unexpected request " + url);
  assert.deepEqual(view.problems.filter(p => !(expectedResourceError && /^Failed to load resource: /.test(p) && expectedResourceError.test(p))), []);
  await view.context.close();
}

const idle = page => page.waitForFunction(() => document.getElementById("connectForm").getAttribute("aria-busy") === "false");
const text = (page, id) => page.locator("#" + id).textContent();
const active = page => page.evaluate(() => document.activeElement === document.body || !document.activeElement ? "BODY" : document.activeElement.id || document.activeElement.className || document.activeElement.tagName);
const frames = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));

// The computed accessible description of the focused element, as Chromium exposes it to screen readers.
async function focusedDescription(page) {
  const client = await page.context().newCDPSession(page);
  try {
    const { nodes } = await client.send("Accessibility.getFullAXTree");
    // The document's root node reports focus too; the focused control is the deepest node that does.
    const focused = nodes.filter(node => (node.properties || []).some(property => property.name === "focused" && property.value.value)).pop();
    return focused && focused.description ? focused.description.value : "";
  } finally { await client.detach(); }
}

async function connected(view) {
  await view.page.click("#connectButton");
  await idle(view.page);
  return { count: await text(view.page, "sampleCount"), announcement: await text(view.page, "announcement") };
}

async function withHosted(mode, action) {
  const backend = await startBackend({ mode });
  const browser = await chromium.launch({ args: loopbackArgs([[HOST, backend.port]]) });
  try { await seed(backend, mode); await action(browser, backend); } finally { await browser.close(); await stopBackend(backend); }
}

let passed = 0;
async function check(name, action) { await action(); passed++; console.log("PASS " + name); }

(async () => {
  await check("gateway.py: real Basic sign-in loads synthetic records; clear session keeps the documented cached sign-in", () => withHosted("gateway", async browser => {
    const view = await signIn(browser, "gateway", CAREGIVER);
    assert.equal(view.navigation.status(), 200);
    assert.equal(await text(view.page, "modeBadge"), "Not connected");
    const first = await connected(view);
    assert.equal(first.count, "3");
    assert.equal(await text(view.page, "modeBadge"), "Backend · synthetic only");
    assert.equal(await view.page.locator("#historyRows tr").count(), 3);
    await view.page.click("#clearSession");
    assert.match(await text(view.page, "announcement"), /Browser sign-in may remain cached/);
    assert.equal(await text(view.page, "sampleCount"), "—");
    assert.equal((await connected(view)).count, "3");
    await finish(view);
  }));

  await check("gateway.py: a wrong caregiver password never renders the dashboard", () => withHosted("gateway", async browser => {
    const view = await signIn(browser, "gateway", WRONG);
    assert.equal(view.navigation.status(), 401);
    assert.equal(await view.page.locator("#connectForm").count(), 0);
    await finish(view, /status of 401/);
  }));

  await check("gateway.py documented limitation: ordinary fresh sign-ins exhaust the shared caregiver failure budget", () => withHosted("gateway", async browser => {
    // railway_candidate/README.md documents a role-wide lockout after eight
    // failures. Measured here: a browser's unauthenticated first requests count
    // as failures, so a few legitimate sign-ins lock out the caregiver.
    let refusedAt = null;
    for (let attempt = 1; attempt <= 8 && refusedAt === null; attempt++) {
      const view = await signIn(browser, "gateway", CAREGIVER);
      if (view.navigation.status() === 429) refusedAt = attempt;
      else if (/HTTP 429/.test((await connected(view)).announcement)) refusedAt = attempt;
      await finish(view, /status of 429/);
    }
    assert.notEqual(refusedAt, null, "expected the documented role-wide lockout within eight legitimate sign-ins");
    console.log("     observed: legitimate fresh sign-in #" + refusedAt + " was refused with 429 (no wrong password was used)");
  }));

  await check("gateway.py: a lockout after connecting says how long the gateway asked to wait", () => withHosted("gateway", async (browser, backend) => {
    const view = await signIn(browser, "gateway", CAREGIVER);
    assert.equal((await connected(view)).count, "3");
    const statuses = [];
    while (!statuses.includes(429) && statuses.length < 12) statuses.push(await unsignedGet(backend, "/v1/devices"));
    assert(statuses.includes(429) && statuses.every(code => code === 401 || code === 429), String(statuses));
    await view.page.click("#refreshButton");
    await idle(view.page);
    const announcement = await text(view.page, "announcement");
    assert.match(announcement, /HTTP 429\)\. No current data was confirmed\. The backend asked to wait about (\d+) seconds before trying again; no automatic retry occurs\./);
    const seconds = Number(/about (\d+) seconds/.exec(announcement)[1]);
    assert(seconds >= 45 && seconds <= 60, announcement);
    await finish(view, /status of 429/);
  }));

  await check("direct_candidate.py: repeated legitimate fresh sign-ins are never throttled", () => withHosted("direct", async browser => {
    for (let attempt = 1; attempt <= 10; attempt++) {
      const view = await signIn(browser, "direct", CAREGIVER);
      assert.equal(view.navigation.status(), 200, "sign-in " + attempt);
      assert.equal((await connected(view)).count, "3", "sign-in " + attempt);
      await finish(view);
    }
  }));

  await check("direct_candidate.py: a repeated wrong password is throttled without locking out the caregiver", () => withHosted("direct", async browser => {
    let throttledAt = null;
    for (let attempt = 1; attempt <= 12 && throttledAt === null; attempt++) {
      const view = await signIn(browser, "direct", WRONG);
      const status = view.navigation.status();
      if (status === 429) throttledAt = attempt; else assert.equal(status, 401);
      assert.equal(await view.page.locator("#connectForm").count(), 0);
      await finish(view, /status of (401|429)/);
    }
    assert.notEqual(throttledAt, null);
    const view = await signIn(browser, "direct", CAREGIVER);
    assert.equal(view.navigation.status(), 200);
    assert.equal((await connected(view)).count, "3");
    await finish(view);
  }));

  await check("direct_candidate.py: keyboard focus stays on Connect, the watch selector and Refresh in the hosted copy", () => withHosted("direct", async (browser, backend) => {
    const second = { sample_id: randomUUID(), device_id: "synthetic-hosted-v1", session_id: randomUUID(), model: "V1", source: "live",
      received_at: new Date(Date.now() - 120000).toISOString(), measured_at: null, heart_rate_bpm: 79, missing_reason: null, synthetic: true };
    assert.equal((await phonePost(backend, "direct", "/v1/batches", { schema_version: 1, batch_id: randomUUID(), samples: [second] })).status, 200);
    const view = await signIn(browser, "direct", CAREGIVER);
    const page = view.page;
    const focused = () => page.evaluate(() => document.activeElement === document.body ? "BODY" : document.activeElement.id);
    await page.focus("#connectButton");
    await page.keyboard.press("Enter"); await idle(page);
    // Devices are listed by ID, so the one-sample V1 watch is selected first.
    assert.equal(await page.locator("#deviceSelect").inputValue(), "synthetic-hosted-v1");
    assert.equal(await text(page, "sampleCount"), "1");
    assert.equal(await focused(), "connectButton");
    await page.focus("#deviceSelect");
    await page.keyboard.press("ArrowDown"); await idle(page);
    assert.equal(await page.locator("#deviceSelect").inputValue(), DEVICE);
    assert.equal(await text(page, "sampleCount"), "3");
    assert.equal(await focused(), "deviceSelect");
    await page.keyboard.press("ArrowUp"); await idle(page);
    assert.equal(await page.locator("#deviceSelect").inputValue(), "synthetic-hosted-v1");
    assert.equal(await text(page, "sampleCount"), "1");
    assert.equal(await focused(), "deviceSelect");
    await page.focus("#refreshButton");
    await page.keyboard.press("Enter"); await idle(page);
    assert.equal(await focused(), "refreshButton");
    await finish(view);
  }));

  await check("direct_candidate.py: a switch to real-data mode on Refresh clears the hosted session and returns focus to Connect, described by that message only", () => withHosted("direct", async browser => {
    const view = await signIn(browser, "direct", CAREGIVER);
    const page = view.page;
    assert.equal((await connected(view)).count, "3");
    await page.route("**/v1/health", route => route.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ status: "ready", schema_version: 1, synthetic_only: false }) }));
    const before = view.requests.length;
    await page.focus("#refreshButton");
    await page.keyboard.press("Enter"); await idle(page);
    assert.deepEqual(view.requests.slice(before).map(url => new URL(url).pathname), ["/v1/health"]);
    assert.match(await text(page, "announcement"), /This hosted candidate requires a synthetic-only backend, so loaded records were removed from this page and no records were requested\. Connect again once the backend is synthetic-only\./);
    assert.equal(await page.evaluate(() => document.activeElement.id), "connectButton");
    assert.match(await focusedDescription(page), /Connect again once the backend is synthetic-only/, "the Connect button is described by the message");
    await page.click("#clearSession");  // the next message
    assert.match(await text(page, "announcement"), /^Displayed session cleared\./);
    assert.equal(await page.locator("#connectButton").getAttribute("aria-describedby"), null);
    await page.focus("#connectButton");
    assert.equal(await focusedDescription(page), "", "a later message does not describe the Connect button");
    await finish(view);
  }));

  await check("direct_candidate.py: after a mode change on Refresh, focus moved to the range selector stays and focus on the emptied table goes to Connect", () => withHosted("direct", async (browser, backend) => {
    // Enough fictional rows for the table to scroll, which makes it a keyboard stop in Chromium.
    const samples = Array.from({ length: 12 }, (_, i) => ({ sample_id: randomUUID(), device_id: DEVICE, session_id: randomUUID(), model: "V8", source: "live",
      received_at: new Date(Date.now() - (i + 5) * 60000).toISOString(), measured_at: null, heart_rate_bpm: 70 + i, missing_reason: null, synthetic: true }));
    assert.equal((await phonePost(backend, "direct", "/v1/batches", { schema_version: 1, batch_id: randomUUID(), samples })).status, 200);
    for (const [tabs, moved, expected] of [[1, "rangeSelect", "rangeSelect"], [2, "table-scroll", "connectButton"]]) {
      const view = await signIn(browser, "direct", CAREGIVER);
      const page = view.page;
      assert.equal((await connected(view)).count, "15");
      let release;
      const held = new Promise(resolve => { release = resolve; });
      await page.route("**/v1/health", async route => { await held; await route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ status: "ready", schema_version: 1, synthetic_only: false }) }); });
      await page.focus("#refreshButton");
      await page.keyboard.press("Enter");
      for (let i = 0; i < tabs; i++) await page.keyboard.press("Tab");
      assert.equal(await active(page), moved, "Tab x" + tabs + " while Refresh waits");
      release(); await idle(page); await frames(page);
      assert.match(await text(page, "announcement"), /requires a synthetic-only backend/);
      assert.equal(await active(page), expected, "focus after the mode change, moved to " + moved);
      const description = await focusedDescription(page);
      if (expected === "connectButton") assert.match(description, /Connect again once the backend is synthetic-only/);
      else assert.doesNotMatch(description, /Connect again/);
      await finish(view);
    }
  }));

  await check("direct_candidate.py: a Refresh after which no watch is listed returns focus from the emptied table to Refresh", () => withHosted("direct", async (browser, backend) => {
    // Enough fictional rows for the table to scroll, which makes it a keyboard stop in Chromium.
    const samples = Array.from({ length: 12 }, (_, i) => ({ sample_id: randomUUID(), device_id: DEVICE, session_id: randomUUID(), model: "V8", source: "live",
      received_at: new Date(Date.now() - (i + 5) * 60000).toISOString(), measured_at: null, heart_rate_bpm: 70 + i, missing_reason: null, synthetic: true }));
    assert.equal((await phonePost(backend, "direct", "/v1/batches", { schema_version: 1, batch_id: randomUUID(), samples })).status, 200);
    const view = await signIn(browser, "direct", CAREGIVER);
    const page = view.page;
    assert.equal((await connected(view)).count, "15");
    let release;
    const held = new Promise(resolve => { release = resolve; });
    await page.route("**/v1/health", async route => { await held; await route.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ status: "ready", schema_version: 1, synthetic_only: true }) }); });
    await page.route(url => url.pathname === "/v1/devices", route => route.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ devices: [], next_cursor: null }) }));
    await page.focus("#refreshButton");
    await page.keyboard.press("Enter");
    for (let i = 0; i < 2; i++) await page.keyboard.press("Tab");
    assert.equal(await active(page), "table-scroll", "Tab x2 while Refresh waits");
    release(); await idle(page); await frames(page);
    assert.match(await text(page, "announcement"), /The previously selected watch is no longer listed, so no watch is shown\.$/);
    assert.equal(await active(page), "refreshButton", "focus after a Refresh that emptied the table");
    await finish(view);
  }));

  console.log(passed + " hosted real-browser checks passed. Headless Chromium with real Basic sign-in against loopback gateway.py and direct_candidate.py; fictional host, credentials and records. Not TLS, proxy, Railway edge, public-domain or deployment validation.");
})().catch(error => { console.error(error); process.exitCode = 1; });
