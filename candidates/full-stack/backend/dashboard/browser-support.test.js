"use strict";
// How a real-browser run ends, through browser-support.js, without a browser:
// each case is a node run that makes the run's directory as a launch does, then
// adds an "exit" listener where Playwright adds its own at a launch (it kills
// the browser, which writes in that directory until then). Checks that the
// directory is removed only after that listener, also where removing it fails
// at first (a browser just killed may still be writing) or it is made again;
// that a run ended by Ctrl-C, SIGTERM or SIGHUP ends by that signal once it has
// done so; and that a failed run writes its error in full, also to a reader
// that waits before it reads. No network, browser or real data.
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "mom-health-support-check-"));
process.on("exit", () => fs.rmSync(scratch, { recursive: true, force: true }));

// The run under check. Its arguments: how it ends (a signal's name, "fail",
// "pass"), then "flaky" to make the first removals fail or the directory come
// back once removed. It prints the run's directory, then "ready".
const run = path.join(scratch, "run.js");
fs.writeFileSync(run, `"use strict";
const fs = require("node:fs");
const path = require("node:path");
const [ending, flaky] = process.argv.slice(2);
if (flaky === "flaky") {
  // A browser just killed that still writes: the first two removals fail, and the third leaves the directory made again.
  const real = fs.rmSync;
  let calls = 0;
  fs.rmSync = (target, options) => {
    calls++;
    if (calls <= 2) throw Object.assign(new Error("ENOTEMPTY: directory not empty, rmdir '" + target + "' (synthetic)"), { code: "ENOTEMPTY" });
    real(target, options);
    if (calls === 3) fs.mkdirSync(target);
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
  support.failed(new Error("synthetic failure " + "x".repeat(200000)));
} else if (ending === "pass") {
  process.removeListener("exit", playwright); // the browser closed, as the suites close theirs
  fs.writeFileSync(${JSON.stringify(path.join(scratch, "seen"))}, "closed");
  setTimeout(() => {}, 100);
} else {
  setInterval(() => {}, 1000);
  console.log("ready");
}
`);

// One run. Its stderr is read only after a second, or (unread) never: then the
// run's write of a long error never ends.
function attempt(ending, flaky, unread) {
  return new Promise((resolve, reject) => {
    fs.rmSync(path.join(scratch, "seen"), { force: true });
    const child = spawn(process.execPath, [run, ending, flaky || ""], { env: { ...process.env, TMPDIR: scratch }, stdio: ["ignore", "pipe", "pipe"] });
    let out = "", errors = "";
    const started = Date.now();
    child.stdout.on("data", data => {
      out += data;
      if (/^SIG/.test(ending) && out.includes("ready\n")) child.kill(ending);
    });
    if (!unread) setTimeout(() => child.stderr.on("data", data => { errors += data; }), 1000);
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

let passed = 0;
async function check(name, action) { await action(); passed++; console.log("PASS " + name); }

(async () => {
  for (const name of ["SIGINT", "SIGTERM", "SIGHUP"]) {
    for (const flaky of ["", "flaky"]) {
      await check(name + (flaky ? ", the first removals failing" : "") + ": the run ends by that signal, its directory removed after Playwright's exit listener", async () => {
        const ended = await attempt(name, flaky);
        assert.deepEqual([ended.status, ended.signal], [null, name], "ended by the signal itself, not an exit with a status");
        assert.equal(ended.seen, "true", "Playwright's exit listener ran, the directory still there");
        assert.ok(ended.made.startsWith(path.join(scratch, "mom-health-chromium-")), ended.made);
        assert.equal(fs.existsSync(ended.made), false, "the run's directory was left");
        assert.equal(ended.errors, "");
      });
    }
  }
  await check("a failed run: status 1, its error in full to a reader that waits, its directory removed after Playwright's exit listener", async () => {
    const ended = await attempt("fail", "flaky");
    assert.deepEqual([ended.status, ended.signal], [1, null]);
    assert.equal(ended.seen, "true");
    assert.equal(fs.existsSync(ended.made), false, "the run's directory was left");
    assert.ok(ended.errors.includes("synthetic failure " + "x".repeat(200000)), "the error was cut at " + ended.errors.length + " bytes");
  });
  await check("a failed run whose error is never read: it ends after 10 s all the same, status 1, its directory removed", async () => {
    const ended = await attempt("fail", "", true);
    assert.deepEqual([ended.status, ended.signal, ended.seen], [1, null, "true"]);
    assert.ok(ended.seconds > 9 && ended.seconds < 20, ended.seconds + " s");
    assert.equal(fs.existsSync(ended.made), false, "the run's directory was left");
  });
  await check("a run that ends by itself, its browser closed: status 0, its directory removed", async () => {
    const ended = await attempt("pass");
    assert.deepEqual([ended.status, ended.signal, ended.seen], [0, null, "closed"]);
    assert.equal(fs.existsSync(ended.made), false, "the run's directory was left");
  });
  assert.deepEqual(fs.readdirSync(scratch).sort(), ["run.js"], "the runs left something in their TMPDIR");
  console.log("\n" + passed + " browser-support ending checks passed. No browser; node runs that stand for one.");
})().catch(error => { console.error(error); process.exitCode = 1; });
