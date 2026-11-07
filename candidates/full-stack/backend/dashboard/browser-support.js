"use strict";
// Shared helpers for the real-browser checks (browser.test.js, hosted-browser.test.js).
// Starts ../tests/browser_backend.py as "python" (put the backend's Python
// environment first on PATH), always on 127.0.0.1, and kills only processes it
// started. Not a test file by itself.
const { spawn } = require("node:child_process");
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

const children = new Set();
process.on("exit", () => { for (const child of children) child.kill("SIGKILL"); });

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

module.exports = { requireBrowser, loopbackArgs, withDeadline, startBackend, stopBackend };
