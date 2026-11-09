"use strict";
// Shared helpers for the real-browser checks (browser.test.js, hosted-browser.test.js).
// Starts ../tests/browser_backend.py as "python" (put the backend's Python
// environment first on PATH), always on 127.0.0.1, and kills only processes it
// started. Gives headless Chromium and Playwright a temporary directory of the
// run's own, and removes it, by its exact path, as the run exits. Not a test file
// by itself.
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const { createInterface } = require("node:readline");
const path = require("node:path");

let chromium = null, unavailable = null;
try { ({ chromium } = require("playwright")); } catch (error) { unavailable = error.message; }

function requireBrowser() {
  if (!chromium) {
    // Never report a pass when the real-browser layer could not run.
    console.error("BROWSER CHECKS NOT RUN: Playwright is not available (" + unavailable + ").");
    process.exit(2);
  }
  return chromium;
}

// Chromium arguments that keep every page on loopback: no proxy, and every
// hostname fails to resolve except the explicit mappings passed in.
function loopbackArgs(mappings = []) {
  return ["--no-proxy-server", "--host-resolver-rules=" + [...mappings.map(([name, port]) => "MAP " + name + " 127.0.0.1:" + port), "MAP * ~NOTFOUND", "EXCLUDE 127.0.0.1"].join(", ")];
}

// Headless Chromium leaves a .org.chromium.Chromium.* file in its TMPDIR now and
// then, passing runs included (most of them empty), and Playwright makes its
// browser profile and artifacts directories in node's, so the first launch gives
// both a directory of the run's own under the system's temporary directory: the
// run removes it as it exits. A run killed outright (SIGKILL) leaves that one
// directory, with what Chromium and Playwright had in it.
let browserTemp = null;
function launchOptions(args) {
  if (browserTemp === null) {
    browserTemp = fs.mkdtempSync(path.join(os.tmpdir(), "mom-health-chromium-"));
    const made = browserTemp;
    process.on("exit", () => fs.rmSync(made, { recursive: true, force: true }));
    process.env.TMPDIR = made; // node's os.tmpdir() from now on: Playwright's, and every later backend's
  }
  return { args, env: { ...process.env, TMPDIR: browserTemp } };
}

const children = new Set();
process.on("exit", () => { for (const child of children) child.kill("SIGKILL"); });
// Ended by Ctrl-C, SIGTERM (timeout) or SIGHUP, a run exits with 128 + that
// signal's number (what a shell reports for a process the signal ended), so that
// the "exit" listeners above, and Playwright's, which kills its browsers and
// removes their directories, still run: node's own ending on such a signal runs
// none of them.
for (const name of ["SIGINT", "SIGTERM", "SIGHUP"]) process.on(name, () => process.exit(128 + os.constants.signals[name]));

// A failed run exits at once, so that a browser or backend still running (one
// whose start or launch failed part-way, say) cannot keep it alive.
function failed(error) {
  console.error(error);
  process.exit(1);
}

function withDeadline(promise, ms, label) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(label + " deadline")), ms); })]).finally(() => clearTimeout(timer));
}

function startBackend({ port, db, mode } = {}) {
  const args = [path.join(__dirname, "../tests/browser_backend.py")];
  if (port) args.push("--port", String(port));
  if (db) args.push("--db", db);
  if (mode) args.push("--mode", mode);
  const child = spawn("python", args, { stdio: ["pipe", "pipe", "inherit"] });
  children.add(child);
  child.once("exit", () => children.delete(child));
  const ready = new Promise((resolve, reject) => {
    createInterface({ input: child.stdout }).once("line", line => resolve(JSON.parse(line)));
    child.once("exit", code => reject(new Error("synthetic backend exited early: " + code)));
  });
  return withDeadline(ready, 20000, "backend start").then(info => ({ child, port: info.port, db: info.db, origin: "http://127.0.0.1:" + info.port }));
}

async function stopBackend(backend, signal = "SIGTERM") {
  if (backend.child.exitCode !== null || backend.child.signalCode !== null) return;
  const exited = new Promise(resolve => backend.child.once("exit", resolve));
  if (signal === "SIGTERM") backend.child.stdin.end(); else backend.child.kill(signal);
  await withDeadline(exited, 15000, "backend stop");
}

module.exports = { requireBrowser, loopbackArgs, launchOptions, failed, withDeadline, startBackend, stopBackend };
