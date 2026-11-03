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

  console.log(passed + " hosted real-browser checks passed. Headless Chromium with real Basic sign-in against loopback gateway.py and direct_candidate.py; fictional host, credentials and records. Not TLS, proxy, Railway edge, public-domain or deployment validation.");
})().catch(error => { console.error(error); process.exitCode = 1; });
