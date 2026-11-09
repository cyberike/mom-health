"use strict";
// Shared helpers for the real-browser checks (browser.test.js, hosted-browser.test.js).
// Starts ../tests/browser_backend.py as "python" (put the backend's Python
// environment first on PATH), always on 127.0.0.1, and kills only processes it
// started. Gives headless Chromium and Playwright a temporary directory of the
// run's own, and removes it, by its exact path, as the run ends. Not a test file
// by itself.
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const { createInterface } = require("node:readline");
const path = require("node:path");
const util = require("node:util");

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
// run removes it as it ends (see end()). A run killed outright (SIGKILL) leaves
// that one directory, with what Chromium and Playwright had in it. Not so on
// Windows, where node's os.tmpdir() and Chromium follow TEMP and TMP, not
// TMPDIR: there Playwright makes its directories in %TEMP% itself, and removes
// them as the run ends, and a run ended outright leaves them there, beside its
// own directory, empty (not run on Windows: from node's and Chromium's sources).
let browserTemp = null;
function launchOptions(args) {
  if (browserTemp === null) {
    browserTemp = fs.mkdtempSync(path.join(os.tmpdir(), "mom-health-chromium-"));
    process.on("exit", removeBrowserTemp);
    process.env.TMPDIR = browserTemp; // node's os.tmpdir() from now on (not on Windows): Playwright's, and every later backend's
  }
  return { args, env: { ...process.env, TMPDIR: browserTemp } };
}

// Removes the run's directory, trying again for up to a second while a browser
// just killed may still be ending, and never throws: an "exit" listener that
// throws keeps the ones after it from running.
function removeBrowserTemp() {
  const pause = new Int32Array(new SharedArrayBuffer(4));
  for (let attempt = 1; ; attempt++) {
    try {
      fs.rmSync(browserTemp, { recursive: true, force: true });
      if (!fs.existsSync(browserTemp)) return;
    } catch (error) {
      if (attempt >= 40) { console.error("could not remove " + browserTemp + ": " + error.message); return; }
    }
    if (attempt >= 40) { console.error("could not remove " + browserTemp + ": it was made again"); return; }
    Atomics.wait(pause, 0, 0, 25);
  }
}

const children = new Set();
process.on("exit", () => { for (const child of children) child.kill("SIGKILL"); });

// Moves the removal of the run's directory after every other "exit" listener, so
// after the one that Playwright adds while a browser of its runs or starts, which
// kills that browser: until then Chromium writes in the directory, and a removal
// before it could leave the directory behind.
function removalLast() {
  if (browserTemp !== null) {
    process.removeListener("exit", removeBrowserTemp);
    process.on("exit", removeBrowserTemp);
  }
}

// An error that escapes the checks' .catch(failed) (thrown, or a rejection left
// unhandled, in a callback that nothing awaits, such as a page's route handler)
// ends the run through node's own handling, with status 1: the removal comes last
// on that ending too.
process.on("uncaughtExceptionMonitor", removalLast);

// libuv makes the pipe or socket that stdout or stderr writes to non-blocking,
// and that pipe is shared with whatever else writes to it (the next command of a
// shell pipeline, say). Node makes it blocking again as it ends, also when its
// own handling of SIGINT or SIGTERM ends it, but not when the run kills itself as
// end() does, so that is done here first; a later writer would otherwise fail
// with EAGAIN, or lose output, once its reader fell behind. _handle and its
// setBlocking() are node's own; an output without them (a file) needs nothing.
function blockingAgain() {
  for (const stream of [process.stdout, process.stderr]) {
    const handle = stream._handle;
    if (handle && typeof handle.setBlocking === "function") handle.setBlocking(true);
  }
}

// Ends the run with this exit status, or by this signal, the removal of the run's
// directory last (see removalLast()). Ended by a signal, the run then ends by that
// signal itself (handled no more), so that what started it sees a run the signal
// ended (Python sees -15, not 143), and bash running a script stops there at
// Ctrl-C, as it does not for an exit with status 130; a signal sent to the run
// alone (kill, timeout) ends the run only, either way. Not on Windows, where node
// only emulates signals (one it sends itself ends it at once with status 1, and
// SIGHUP cannot be sent): there the run exits with status 128 + n.
function end(status, signal) {
  removalLast();
  if (signal && process.platform !== "win32") {
    process.on("exit", () => { blockingAgain(); process.removeAllListeners(signal); process.kill(process.pid, signal); });
  }
  process.exit(status); // with status 128 + n, should the signal not end it
}

// Ctrl-C, SIGTERM (timeout) or SIGHUP: node's own ending on such a signal runs
// no "exit" listener (none of the ones above, nor Playwright's). On Windows only
// Ctrl-C and the console closing reach these: a SIGTERM sent there ends the run
// at once, as SIGKILL does, its directory left.
for (const name of ["SIGINT", "SIGTERM", "SIGHUP"]) process.on(name, () => end(128 + os.constants.signals[name], name));

// A failed run ends once its error is written in full (process.exit drops what
// a pipe has not taken yet), or after 10 s, so that a browser or backend still
// running (one whose start or launch failed part-way, say) cannot keep it alive.
function failed(error) {
  process.exitCode = 1;
  setTimeout(() => end(1), 10000);
  process.stderr.write(util.format(error) + "\n", () => end(1));
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
