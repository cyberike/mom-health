"use strict";
// How a real-browser run ends, through browser-support.js, without a browser:
// each case is a node run that makes the run's directory as a launch does, then
// adds an "exit" listener where Playwright adds its own at a launch (it kills
// the browser, which writes in that directory until then). Checks that the
// directory is removed only after that listener, also where removing it fails
// for a while at first (a browser just killed may still be writing) or it is
// made again; that a run ended by Ctrl-C, SIGTERM or SIGHUP ends by that signal
// once it has done so, writing nothing, and leaves an output it shares with what
// comes next blocking again; that a failed run writes its error in full, stack
// included, also to a reader that waits before it reads; that an error escaping
// the checks ends it the same way; and that a run whose runner is gone ends. No
// network, browser or real data. POSIX only: on Windows it runs nothing, says
// so, and exits with status 2, as the browser suites do without Playwright.
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

if (process.platform === "win32") {
  // node only emulates signals there (one sent to a run ends it at once, with none of its "exit" listeners), and
  // its os.tmpdir() reads TEMP and TMP, not the TMPDIR each run is given, so not even the checks without a signal hold.
  console.error("browser-support ending checks NOT RUN: they need POSIX signals, and a node whose temporary directory follows TMPDIR; Windows has neither.");
  process.exit(2);
}

const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "mom-health-support-check-"));
process.on("exit", () => fs.rmSync(scratch, { recursive: true, force: true }));
// Ended by a signal itself, the runner still removes its scratch directory; each run it started that waits ends as its
// stdin closes, and a failed one once its write fails.
for (const name of ["SIGINT", "SIGTERM", "SIGHUP"]) process.on(name, () => process.exit(128 + os.constants.signals[name]));

// An error larger than any socket buffer a spawn gets on a usual host (Linux
// allows one twice net.core.wmem_max at most; node asks for 64 KiB): a run
// cannot write it all before a reader takes most of it.
const LONG = 16 * 1024 * 1024;

// The run under check. Its arguments: how it ends (a signal's name, sent by the
// runner; "self-SIGTERM", sent by itself; "fail"; "throw"; "reject"; "pass";
// anything else: it waits), then "flaky" to make removals fail for 300 ms and
// the directory come back once removed. It prints the run's directory, then
// "ready" where it waits, and ends with status 3 should its stdin close first
// (its runner gone).
const run = path.join(scratch, "run.js");
fs.writeFileSync(run, `"use strict";
const fs = require("node:fs");
const path = require("node:path");
const [ending, flaky] = process.argv.slice(2);
if (flaky === "flaky") {
  // A browser just killed that still writes: removals fail for the first 300 ms, and the first one after leaves the directory made again.
  const real = fs.rmSync;
  let first = null, back = false;
  fs.rmSync = (target, options) => {
    if (first === null) first = Date.now();
    if (Date.now() - first < 300) throw Object.assign(new Error("ENOTEMPTY: directory not empty, rmdir '" + target + "' (synthetic)"), { code: "ENOTEMPTY" });
    real(target, options);
    if (!back) { back = true; fs.mkdirSync(target); }
  };
}
const support = require(${JSON.stringify(path.join(__dirname, "browser-support.js"))});
const made = support.launchOptions([]).env.TMPDIR;
fs.writeFileSync(path.join(made, "profile"), "");
// Where Playwright adds its "exit" listener at a launch, which kills the browser: the directory must still be there.
const playwright = () => fs.writeFileSync(${JSON.stringify(path.join(scratch, "seen"))}, String(fs.existsSync(path.join(made, "profile"))));
process.on("exit", playwright);
console.log(made);
if (ending === "fail") {
  support.failed(new Error("synthetic failure " + "x".repeat(${LONG})));
} else if (ending === "throw") {
  setTimeout(() => { throw new Error("synthetic uncaught failure"); }, 50);
} else if (ending === "reject") {
  setTimeout(() => Promise.reject(new Error("synthetic unhandled rejection")), 50);
} else if (ending === "pass") {
  process.removeListener("exit", playwright); // the browser closed, as the suites close theirs
  fs.writeFileSync(${JSON.stringify(path.join(scratch, "seen"))}, "closed");
  setTimeout(() => {}, 100);
} else {
  process.stdin.on("end", () => process.exit(3)).resume();
  console.log("ready"); // stdout written to: libuv has made its pipe non-blocking
  if (ending === "self-SIGTERM") { console.error("ready"); process.kill(process.pid, "SIGTERM"); } // and stderr's
}
`);

const env = { ...process.env, TMPDIR: scratch };

// One run. Its stderr is read at once, or (slow) only after a second, or
// (unread) never: then a long error is never all written.
function attempt(ending, { flaky, slow, unread, gone } = {}) {
  return new Promise((resolve, reject) => {
    fs.rmSync(path.join(scratch, "seen"), { force: true });
    const child = spawn(process.execPath, [run, ending, flaky ? "flaky" : ""], { env, stdio: ["pipe", "pipe", "pipe"] });
    let out = "", errors = "";
    const started = Date.now();
    child.stdout.on("data", data => {
      out += data;
      if (!out.includes("ready\n")) return;
      if (/^SIG/.test(ending)) child.kill(ending);
      if (gone) child.stdin.end();
    });
    const read = () => child.stderr.on("data", data => { errors += data; });
    if (slow) setTimeout(read, 1000); else if (!unread) read();
    const deadline = setTimeout(() => { child.kill("SIGKILL"); reject(new Error(ending + ": the run did not end")); }, 30000);
    const done = (status, signal) => {
      clearTimeout(deadline);
      const seen = fs.existsSync(path.join(scratch, "seen")) ? fs.readFileSync(path.join(scratch, "seen"), "utf8") : "never";
      fs.rmSync(path.join(scratch, "seen"), { force: true });
      resolve({ status, signal, made: out.split("\n")[0], seen, errors, seconds: (Date.now() - started) / 1000 });
    };
    // Done once all it wrote is read, or, never read, once it has exited.
    if (unread) child.on("exit", (status, signal) => { child.stderr.destroy(); done(status, signal); });
    else child.on("close", done);
  });
}

function removed(ended) {
  assert.ok(ended.made.startsWith(path.join(scratch, "mom-health-chromium-")), ended.made);
  assert.equal(fs.existsSync(ended.made), false, "the run's directory was left");
}

// A check whose action gives { skipped: why } could not run here: it is counted apart, never as passed.
let passed = 0, skipped = 0;
async function check(name, action) {
  const outcome = await action();
  if (outcome && outcome.skipped) { skipped++; console.log("SKIP " + name + " (" + outcome.skipped + ")"); return; }
  passed++;
  console.log("PASS " + name);
}

(async () => {
  for (const name of ["SIGINT", "SIGTERM", "SIGHUP"]) {
    for (const flaky of [false, true]) {
      await check(name + (flaky ? ", removals failing for 300 ms" : "") + ": the run ends by that signal, writing nothing, its directory removed after Playwright's exit listener", async () => {
        const ended = await attempt(name, { flaky });
        assert.deepEqual([ended.status, ended.signal], [null, name], "ended by the signal itself, not an exit with a status");
        assert.equal(ended.seen, "true", "Playwright's exit listener ran, the directory still there");
        removed(ended);
        assert.equal(ended.errors, "");
      });
    }
  }
  await check("a run ended by a signal leaves an output it shares with what comes next blocking again", async () => {
    if (!fs.existsSync("/proc/self/fdinfo")) return { skipped: "needs Linux /proc to read the outputs' flags" };
    // The shell's stdout and stderr, which the run writes to and the next command then: sockets node has made non-blocking.
    const shell = spawn("sh", ["-c", '"$0" "$1" self-SIGTERM; echo "status $?"; grep -H "^flags:" /proc/self/fdinfo/1 /proc/self/fdinfo/2', process.execPath, run], { env, stdio: ["pipe", "pipe", "pipe"] });
    let out = "";
    shell.stdout.on("data", data => { out += data; });
    const status = await new Promise(resolve => shell.on("close", resolve));
    const seen = fs.existsSync(path.join(scratch, "seen")) ? fs.readFileSync(path.join(scratch, "seen"), "utf8") : "never";
    fs.rmSync(path.join(scratch, "seen"), { force: true });
    assert.equal(status, 0, out);
    assert.equal(seen, "true", "Playwright's exit listener ran, the directory still there");
    assert.match(out, /^status 143$/m, out);
    for (const fd of [1, 2]) {
      const flags = parseInt(new RegExp("^/proc/self/fdinfo/" + fd + ":flags:\\s*([0-7]+)$", "m").exec(out)[1], 8);
      assert.equal(flags & fs.constants.O_NONBLOCK, 0, "its output " + fd + " was left non-blocking: " + out);
    }
    assert.equal(fs.existsSync(out.split("\n")[0]), false, "the run's directory was left");
  });
  await check("a failed run: status 1, its error in full, stack included, to a reader that waits, its directory removed after Playwright's exit listener", async () => {
    const ended = await attempt("fail", { flaky: true, slow: true });
    assert.deepEqual([ended.status, ended.signal], [1, null]);
    assert.equal(ended.seen, "true");
    removed(ended);
    assert.ok(ended.errors.includes("synthetic failure " + "x".repeat(LONG)), "the error was cut at " + ended.errors.length + " bytes");
    assert.match(ended.errors, /\n {4}at /, "no stack written");
  });
  await check("a failed run whose error is never read: it ends after 10 s all the same, status 1, its directory removed", async () => {
    const ended = await attempt("fail", { unread: true });
    assert.deepEqual([ended.status, ended.signal, ended.seen], [1, null, "true"]);
    assert.ok(ended.seconds > 9 && ended.seconds < 20, ended.seconds + " s");
    removed(ended);
  });
  for (const [ending, text] of [["throw", "synthetic uncaught failure"], ["reject", "synthetic unhandled rejection"]]) {
    await check("an error that escapes the checks (" + ending + "): status 1, the error written, the directory removed after Playwright's exit listener", async () => {
      const ended = await attempt(ending);
      assert.deepEqual([ended.status, ended.signal, ended.seen], [1, null, "true"]);
      removed(ended);
      assert.ok(ended.errors.includes(text), ended.errors);
    });
  }
  await check("a run whose runner is gone (its stdin closed): it ends, its directory removed", async () => {
    const ended = await attempt("wait", { gone: true });
    assert.deepEqual([ended.status, ended.signal], [3, null]);
    removed(ended);
  });
  await check("a run that ends by itself, its browser closed: status 0, its directory removed", async () => {
    const ended = await attempt("pass");
    assert.deepEqual([ended.status, ended.signal, ended.seen], [0, null, "closed"]);
    removed(ended);
  });
  assert.deepEqual(fs.readdirSync(scratch).sort(), ["run.js"], "the runs left something in their TMPDIR");
  console.log("\n" + passed + " browser-support ending checks passed" + (skipped ? ", " + skipped + " skipped (not run here)" : "") +
              ". No browser; node runs that stand for one.");
})().catch(error => { console.error(error); process.exitCode = 1; });
