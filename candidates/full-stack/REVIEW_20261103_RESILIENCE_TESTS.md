# Resilience and real-browser test review — November 3, 2026

Review branch `claude/mom-health-test-coverage-ggkdxz`, started from `567c0e1ce6bdbe92bfddd1c8631c4e78e32420d0` (the handoff commit on `main`). All work used the current candidate under `candidates/full-stack`; the root README describes an older version and was not used as a guide. [cyberike/mom-health#1](https://github.com/cyberike/mom-health/pull/1) merged the branch through `b4e918d` into `main` as `7911545` (November 7, UTC). The corrections after that merge (from `5577105`) await owner review and are not merged.

This is local synthetic testing in a cloud Linux container. Nothing was deployed or published, and no phone, watch, ADB, emulator, Docker, Railway, Caddy, Cosign/Rekor, credential store or real health data was touched. **Cloud testing does not validate the physical watch, the phone, Bluetooth, Android storage, real phone-to-server networking or unattended reliability.**

## Restrictions kept

- Handoff boundaries are unchanged: the Rekor/Caddy signature verification remains unresolved and was not retried; the rejected Railway public-domain action was not retried or approximated; no new persistent credentials, accounts, services or costs were created; WSL, Docker and emulator-ADB denials were respected.
- Only fictional records and obviously fictional in-test credentials were used. Every database was a fresh disposable SQLite file under the gitignored `candidates/full-stack/build/<suite>/<uuid>/`, retained for inspection like the existing suites, or (in the November 7 follow-up) a throwaway local PostgreSQL cluster described there. The browser launcher refuses any database outside its own fixture directory.
- Servers bound only to `127.0.0.1`. The real-browser checks launch headless Chromium with no proxy and a host-resolver rule that makes every hostname fail except explicit loopback mappings, and every check asserts that all requests stayed on loopback. The fictional host `synthetic.example.com` resolved to loopback inside that browser only; no DNS, domain, tunnel or provider was involved.
- The original fail-closed `gateway.py` and `direct_candidate.py` code was not modified (RAILWAY_DIRECT_DECISION_20261029.md asks to keep the original gateway intact). Their behavior is measured and pinned below; one README sentence was added.

## Environment and prerequisites

The documented commands need FastAPI and httpx, which were not installed. They were installed into an isolated virtual environment outside the repository, matching the recorded Railway build where possible:

- Python 3.13.18 with FastAPI 0.162.0, Starlette 1.10.0, httpx 0.28.1, pydantic 2.14.2, anyio 4.16.0, h11 0.16.0 (Railway build: Python 3.13.16, FastAPI 0.162.0, Starlette 1.10.0, httpx 0.28.1) and Uvicorn 0.40.0 (the version the earlier local loopback findings were recorded with). The original Windows workstation used Python 3.14, FastAPI 0.128 and Starlette 0.50, so results there may differ.
- Node 22.23.4; Playwright 1.63.0 with headless Chromium 153.0.8010.12. Firefox and WebKit are not installed, so only Chromium was exercised.
- `integration.test.js`, `browser.test.js` and `hosted-browser.test.js` spawn `python` from `PATH`, so the backend's environment must come first on `PATH`. Without Playwright the browser suites print `BROWSER CHECKS NOT RUN` and exit 2; they never report a pass.
- Starlette 1.10 prints a `StarletteDeprecationWarning` (`httpx` vs `httpx2`) from `TestClient`; it does not affect results.

## Baseline at 567c0e1 (before any change)

| Command (from `candidates/full-stack/backend`) | Result |
| --- | --- |
| `python -m unittest discover -s tests -v` | 137 tests: 1 error, 1 skip |
| `python -m unittest railway_candidate.test_gateway -v` | 12 OK |
| `cd tests && python -m unittest integration_contract_android -v` | 5 OK |
| `cd dashboard && node core.test.js` / `app.test.js` / `hosted.test.js` / `integration.test.js` | 38 / 9 / 4 / 6 passed |
| `node --check` on `app.js`, `core.js`, `railway_candidate/dashboard-app.js` | OK |

The error was `test_direct_composition_sqlite`: its fixture got default-umask modes (0755/0644) and the POSIX validator correctly requires owner-only 0700/0600. That branch had never run on Linux; on Windows it is skipped. The skip is the Windows-only UNC-path test.

## Defects found and fixed

Each product fix has a regression test that fails on the code before the fix and passes after it. Commit `9937bbe` holds the failing tests for defects 1-5 so those failures can be reproduced by checking it out; the tests for defects 6-13 were committed together with their fixes.

| # | Defect (component) | Evidence before the fix | Fix | Regression test |
| --- | --- | --- | --- | --- |
| 1 | A gateway status report that stalls after arriving is stored over a report acknowledged later (`repository.save_status`) | Deterministic: the delayed report kept its earlier receipt time and was served instead of the later one; a mapper probe saw it in 12/12 rounds of 32 concurrent posts | Take the write lock first (SQLite `BEGIN IMMEDIATE`; PostgreSQL advisory lock mirroring `ingest`), then the receipt time | `test_concurrent_retries.StatusReportRaceTests` |
| 2 | In real-data mode a report saved at an exact second sorts as newer than a later one, because `isoformat()` drops a zero fraction (`...:00Z` > `...:00.500000Z` as TEXT) | Patched clock: the older synthetic report was returned for the device | Fixed-width microseconds (same commit as 1) | `test_device_isolation...newest_report_even_at_a_whole_second` |
| 3 | Separator-formatted BLE addresses (`AA-BB-CC-DD-EE-FF`, `aa_bb_...`, mixed) accepted as device IDs (`models.device_identifier`), contrary to CONTRACT.md and HANDS_OFF_REQUIREMENT.md | Stored and listed via `/v1/devices` | Reject any ID that is twelve hex digits once `-`/`_` separators are removed, however grouped | `test_device_isolation...direct_device_addresses_are_rejected_on_every_endpoint` |
| 4 | Synthetic records and gateway reports unlabeled in the hero and status cards (both dashboards); the table already labels rows | Real-data mode showed a synthetic record as "Last saved observation - not live" | "Synthetic test record" / "Synthetic test report, not from a real gateway." | `app.test.js`, `hosted.test.js` provenance checks |
| 5 | Keyboard focus dropped to `<body>` after Connect, a watch change or Refresh (both dashboards; Chromium moves focus off a disabled control) | Real Chromium: next ArrowDown scrolled the page instead of choosing the next watch | Restore focus to the initiating control when it is usable again | `browser.test.js` keyboard check |
| 6 | An earlier error banner stayed on screen after a later successful watch change, Load older or Load more devices (both dashboards) | Banner "Access was refused..." beside freshly loaded rows | A success replaces a lingering error with a neutral message that claims nothing about the earlier failure | `lifecycle.test.js` (both dashboards) |
| 7 | After a connect failed past `/v1/health`, the badge said "Backend · synthetic only" and Refresh was enabled while the status line said "Backend: not connected" (both dashboards) | Simulated 503 from `/v1/devices` or `/v1/gateway-status` | Status line reads "Backend reached: <time> · Records not fully loaded" until all loads succeed | `lifecycle.test.js` (both dashboards) |
| 8 | The hosted dashboard's 401 message said "Check the caregiver token" although it uses browser sign-in and has no token field | Simulated and real-browser refusal | Points to the caregiver sign-in and the documented cached-sign-in caveat | `hosted.test.js` |
| 9 | A Connect that timed out at `/v1/health` said "Refresh explicitly to try again" although Refresh is disabled until a backend is reached and the token field was already cleared (both dashboards) | Simulated 12-second timeout | Before a backend session exists the message says "Connect again to retry" | `lifecycle.test.js` (both dashboards) |
| 10 | A second Connect activation after a successful connect (the slow half of a double-click, or Enter again in the emptied token field) cleared the session, retried without the token and reported the correct token as refused (local dashboard) | Real Chromium: three of three repeated-submit paths | An empty resubmit while a token session is active only says it is already connected | `lifecycle.test.js`, `browser.test.js` |
| 11 | After a failed Refresh or watch change the status card said "No gateway status report received." and the pager "End of the currently available page sequence" beside a non-empty source count (both dashboards) | Real Chromium after a timeout and after a killed backend | Both say the status or observations for this watch are not loaded until a load succeeds | `lifecycle.test.js` |
| 12 | A connection failure showed the browser's raw "Failed to fetch" (both dashboards) | Real Chromium with a killed backend | The same caregiver wording as HTTP errors; timeouts and cancellation unchanged | `lifecycle.test.js` |
| 13 | With more than 50 watches, Refresh silently replaced a watch chosen from a later device page with the first watch (both dashboards) | Real Chromium with 55 fictional watches | The announcement says so and points to Load more devices (or says the watch is no longer listed) | `lifecycle.test.js` |

Copy that advised unavailable actions was corrected in both dashboards: before connecting the pager no longer says "Refresh", and a backend with no watches no longer suggests loading older observations.

Hardening (not user-reachable): a change event delivered while a load was pending returned early without restoring the watch selector, so the picker could show one watch over another's data. The selector is disabled while busy, so users cannot trigger it; the early return now restores the displayed watch (`lifecycle.test.js`).

Test defects fixed: `test_direct_composition_sqlite` (POSIX fixture modes; it now runs the real Linux owner/mode/no-follow/ACL checks, plus a new real negative test), and this branch's own first crash-recovery test, which killed the writer before SQLite synced the journal header and therefore could not detect a broken recovery (it now forces a genuinely hot journal; deleting that journal corrupts the index and exposes eight rows of the killed batch, which the assertions catch).

## Coverage of the requested areas

| Area | At 567c0e1 | Added on this branch |
| --- | --- | --- |
| Concurrent retries | Sequential replay, duplicate and collision checks; one external lock holder against the budgeted store; limiter thread safety | `test_concurrent_retries`: 24-thread identical batches, overlapping batches in rotated orders, one sample ID with conflicting contents, batch-ID reuse (repository and real HTTP), identical partial-collision receipts, a 503 after a durable commit followed by a retry, the status race, and the direct candidate's 32 admission slots with real budgeted storage (8 "Service busy" and 12-17 storage-busy 503s per run, none coinciding with a commit). A deferred-`BEGIN` mutant fails all five repository race tests. Browser: double and triple clicks start one operation. |
| Backend restarts | A second startup reading back one sample in the Railway harnesses | `test_restart_recovery`: receipts, samples and status survive a server restart; replay returns the original partial receipt and changed content still gets 409; cursors issued before a restart continue after new inserts and stay device- and direction-bound; concurrent first start; startup waits for a brief writer and fails closed on a persistent one; a writer killed mid-ingest with a genuinely hot journal leaves nothing and the retry commits once. `test_auth_failures`: a restart with a rotated token. Browser: a crashed backend restarted on the same port and database serves an identical view. |
| Device isolation | One ascending cross-device cursor; descending-to-ascending; device listing with real-data filtering | `test_device_isolation`: prefix-, separator- and case-similar IDs never share samples, summaries or status; a cursor-binding matrix including forged cursors (kills a descending-scope mutant every earlier suite missed); listing pages without skips; direct-address IDs refused on every endpoint (kills a 12-hex mutant). `lifecycle.test.js` and the browser suite: watch changes reset rows, cursors and status, even when the new load fails. |
| Authentication failures | Missing token, hosted host/proto checks, duplicate headers in hosted mode, gateway roles and throttle, extensive direct-candidate checks | `test_auth_failures`: wrong and malformed credentials on every route with a fixed non-echoing body and no side effects, authorization before body reading, exact shell exemption, real-HTTP whitespace handling, token rotation. `test_ingress_auth_failures`: gateway challenge and per-role accounting, lockout countdown, direct-candidate credential mixing and clock rollback. `lifecycle.test.js`: 401/403/429/503 after a successful connect and full recovery. Browser: wrong token then recovery. Hosted browser: real Basic sign-in through both ingress candidates. |
| Cancellation | Clear session while `/v1/health` was pending; direct-candidate slots retained across cancellation with fake repositories | `test_cancellation_loopback` on real TCP: a disconnect mid-upload stores nothing; a client that gives up mid-ingest still gets one commit, and a concurrent identical retry receives that receipt; any server-side warning fails these tests. The direct candidate restores exactly 32 admission slots after storage failures, a raising downstream send and 32 cancelled callers (two leak mutants that pass all 22 earlier direct-candidate tests fail it). `test_budgeted_sqlite`: a budget that expires at any of several hundred interrupt points inside a real ingest leaves all or nothing. `lifecycle.test.js`: a stale operation cannot end a newer one, a body that finishes streaming after Clear session is discarded, the 12-second timeout recovers, `pagehide` during a connect aborts it and forgets the token. Browser: real `AbortController` aborts (`net::ERR_ABORTED`). |
| Late responses after a watch change or Clear session | A late `/v1/health` after Clear session | `lifecycle.test.js` (both dashboards): late sample pages and gateway reports after Clear session, the synthetic preview or a reconnect never render; a cursor from a cleared session is never reused or appended; a request abandoned by Clear session never sends under, or ends, a session with another token (local copy). Browser: Clear session during a connect and the preview during a watch change discard the late responses; the watch selector is disabled while a load is pending. |

Real-browser layout checks (`browser.test.js`): empty states before and after connecting, connect/clear/reconnect giving an identical view, phone and tablet widths 320-768 px with no horizontal page overflow and controls at least 44 px tall, and 200%/400% zoom reflow (emulated as a 640x400 or 320x200 CSS-pixel viewport at 2x/4x, which is what browser zoom gives a 1280x800 window) with every control reachable by keyboard. No console errors, CSP violations or page errors occurred. The independent browser mapping probed a wider matrix without committing it (320x568, 375x667, 414x896 and 768x1024 phones and tablet, 1280x800, 200% and 400%, each in five states including 55 watches and an error state, and the hosted copy at 320 px, 200% and 400%) and found no horizontal scrolling, clipped text, overlapping controls or targets under 24x24 px.

## Independent mapping and verification

Five review agents mapped coverage and probed for defects in parallel, each with read-only access to the repository, experiments confined to a scratch directory, loopback-only servers and fictional data. Every defect they reported was reproduced again here with a deterministic test before it was fixed.

- **Concurrency and restarts.** Every data-integrity invariant held across 16-32 threads, several processes, real loopback Uvicorn, SIGTERM and SIGKILL restarts and genuinely hot journals: no duplicate, partial write, wrong receipt or acknowledgment without a durable commit. Reported defect 1 and the weakness in this branch's first crash test.
- **Device isolation.** 418 sample and cursor checks, 141 summary checks and 117 checks through both ingress candidates found no cross-device leak. Reported defects 2-5 and two mutants that survived every earlier suite.
- **Authentication failures.** Reported defects 6-8 and measured the `gateway.py` sign-in lockout (finding 1 below).
- **Cancellation and late responses.** 48 simulated scenarios on both dashboard copies and 12 real-Chromium probes held for every user-reachable late response. Reported defect 9, the selector hardening and two admission-slot leak mutants.
- **Real-browser UI.** Reported defects 10-13, the layout matrix above, and independently confirmed defect 5.

A separate adversarial verifier per area then tried to refute every reported item against the unchanged starting code, with its own independent reproduction. Of 19 reported items, 18 were confirmed as real and undocumented (14 product defects, the two coverage gaps behind the surviving mutants, this branch's first crash test, and one duplicate report of the focus defect from a second area). The remaining item, the `gateway.py` sign-in lockout, was confirmed as a real mechanism but classified as the documented, test-pinned design of the original gateway; it is handled as finding 1 and in `railway_candidate/README.md`, not by a code change. Verifier suggestions adopted on this branch: the stricter address rule (strip separators, then reject twelve hex digits), the startup-recovery assertion in the crash test, and the same-browser reload note. Two alternatives were not adopted: re-using the in-memory token for a repeated Connect (this branch instead leaves the session untouched and says it is already connected) and paging Refresh until a watch from a later device page is found (this branch announces the change instead).

## Findings not changed (decisions for the owner)

These were measured or demonstrated but are documented limitations, design choices or wording questions rather than clear defects. None was changed on November 3. The November 7 follow-up (end of this report) settled the provenance part of item 2, item 7, the Refresh part of item 5, and the range and `Retry-After` parts of item 6 with conservative defaults. The rest remain open.

1. **gateway.py sign-in lockout.** Requests without an Authorization header count as failures, and a browser sends two during each ordinary Basic sign-in, so in headless Chromium the fourth fresh legitimate sign-in within a minute was refused with 429 (no wrong password); reloads in an already signed-in browser cost nothing. Its lockout 429 carries no challenge, so a browser that is not yet signed in sees a bare JSON page until the window expires. Role-wide lockout is documented; this browser cost is now recorded in `railway_candidate/README.md`. `direct_candidate.py` does not count header-less requests: ten fresh sign-ins were never throttled, and a repeated wrong password was throttled without locking out the caregiver.
2. **Status provenance in real-data mode.** `status()` returns whichever of a device's synthetic and real reports arrived last, so a fresh synthetic test report hides the real watch's report. Status is also last-received-wins regardless of `reported_at`, so a delayed older report replaces a newer one. A per-upsert sequence would also survive backward clock steps.
3. **Budgeted SQLite under its own concurrency.** With the 0.25 s busy timeout, 12-17 of 32 concurrently admitted writes return 503 after release. Integrity holds and retries converge. A `Retry-After` on storage 503s or server-side write serialization would reduce retries.
4. **Replay receipts** are JSON-equal but not byte-identical (key order differs between the first response and replays). This matters only if receipts are ever hashed or signed.
5. **Readiness and cursors.** `/v1/health` reports process readiness only; it stays "ready" while writes return 503. Restarting against an empty or different database also starts "ready", and cursors carry no store identity (an old descending cursor then returns new rows as "older"). Refresh never re-reads `/v1/health`, so a mode change after a backend restart is not re-validated until the next Connect (it fails closed only in the synthetic direction).
6. **Failed refresh display.** After a failed Refresh the count and badge stay visible, and so do the previously loaded rows unless it failed at or after its sample request, which empties the table first (the status card and pager now say what was not loaded; this qualification was added in the third verification). The in-memory token is kept until Clear session or the next Connect, and Refresh does not use a newly typed token. Neither dashboard shows `Retry-After` for 429. Clear session does not reset the time-range filter.
7. **Boundary rejections** (400/401/403/413) are sent before the security-header wrapper, so they lack `no-store`, `nosniff`, CSP and frame options. Their bodies are fixed and non-sensitive.
8. **Bearer matching** is byte-exact: the scheme is case-sensitive and exactly one space is required (RFC 9110 treats schemes as case-insensitive). Over a real listener h11 trims surrounding whitespace. The Android uploader sends exactly `Bearer `. Both layers are now pinned.
9. **Small HTTP inconsistencies.** `/dashboard` without the trailing slash returns a JSON 401 when a token is configured (the shell itself is public). `HEAD` on the shell returns 405 because FastAPI GET routes do not register HEAD. Neither leaks data.
10. **Direct candidate clock handling.** Any wall-clock regression returns 503 for every role, even with no previous credential configured, and a forward jump followed by a correction keeps it unavailable until real time catches up. This is documented; it is now pinned.
11. **More than 50 devices.** Refresh still reloads only the first device page. It now announces when the chosen watch had to be replaced, but it does not page on to keep the selection.
12. **Timeouts and page lifecycle.** The 12-second timeout is per request, so a connect or refresh of up to four requests can stay busy for about 48 s against a backend that answers each request just inside the timeout. One that never answers ends the operation after 12 s, because the first timeout cancels all of it. `pagehide` clears the session without an announcement; Chromium does not restore the page from the back/forward cache (it is `no-store`), and other engines were not tested.
13. **Operational notes.** A writer killed before its first page spill leaves a non-hot journal stub that `durability_probe` refuses (a fail-closed false alarm on a healthy database). Uvicorn re-raises SIGTERM, so a graceful stop exits with status -15.
14. **Accessibility observations.** At 320 px a partly visible focused link in the horizontally scrolling mobile nav is not scrolled fully into view (WCAG 2.4.11 AA is met, 2.4.12 AAA is not). Chromium makes the overflowing table container a keyboard stop without an accessible name. After the last "Load older" page the disabled pager cannot keep focus; Shift+Tab returns to the readings card, but forward Tab reaches the end of the page because nothing focusable follows. The chart's SVG axis labels scale with the chart width (about 3.7 CSS px at 320 px or 400% zoom, so browser zoom does not enlarge them); the table is the text equivalent.
15. **Hero recency.** The hero card's latest value is the last record the server stored, as documented in `dashboard/README.md`; after a delayed upload of older history it can be older than the newest table row.

## Exact commands and results (branch tip)

Prerequisites, outside the repository (versions above):

```
python3 -m venv <outside-repo>/venv
<outside-repo>/venv/bin/python -m pip install fastapi==0.162.0 starlette==1.10.0 httpx==0.28.1 uvicorn==0.40.0
export PATH=<outside-repo>/venv/bin:$PATH
```

From `candidates/full-stack/backend`:

| Command | Result |
| --- | --- |
| `python -m unittest discover -s tests -v` | 174 tests OK (1 Windows-only skip) |
| `python -m unittest railway_candidate.test_gateway -v` | 12 OK |
| `cd tests && python -m unittest integration_contract_android -v` | 5 OK |
| `cd dashboard && node core.test.js` | 39 passed |
| `node app.test.js` | 10 passed |
| `node hosted.test.js` | 6 passed |
| `node lifecycle.test.js` | 56 passed |
| `node integration.test.js` | 6 passed |
| `node browser.test.js` (Playwright + Chromium) | 15 passed |
| `node hosted-browser.test.js` (Playwright + Chromium) | 10 passed |
| `node --check app.js && node --check core.js && node --check ../railway_candidate/dashboard-app.js && node --check browser-support.js` | OK |

Counts include the November 7 follow-up and all three rounds of corrections. The full backend run takes about 27 s and both browser suites about 56 s on four shared CPUs. New fixture directories accumulate under `candidates/full-stack/build/` (gitignored), as with the existing suites.

## Blocked or not run

- PostgreSQL on a hosted or managed instance (Railway or any other): not attempted. PostgreSQL ran only on throwaway local clusters in this container (follow-up below); Docker remains denied.
- Caddy and the Rekor transparency check: unchanged and not retried. Railway domain, deployment, public HTTPS, a real proxy or edge: not attempted.
- Phone, watch, BLE, Android storage, ADB, emulator, S25 Ultra: not allowed. The Android portable Java suites were not run; they are outside this request and their runner ships in the build kit, not this repository.
- Firefox, WebKit/Safari, headed browsers with a native Basic sign-in prompt, real mobile browsers and screen readers: not available here.
- Multi-worker or multi-replica limiter state, real clock steps and power-loss durability: not exercised.

## Remaining acceptance checklist

- [x] Owner review and merge of the branch through `b4e918d` ([cyberike/mom-health#1](https://github.com/cyberike/mom-health/pull/1), `7911545`).
- [ ] Owner review of the corrections after the merge (`5577105` onward); merge decision (not merged).
- [ ] Choose the ingress candidate in light of the measured `gateway.py` sign-in lockout. Review the follow-up's defaults for items 2, 5, 6 and 7, and decide what remains of items 2, 5 and 6. For item 2, that means whether each kind of status report should follow `reported_at` rather than arrival order.
- [ ] Resolve the Rekor/Caddy verification through legitimately applied, confirmed scoped access, then run the prepared Caddy loopback harness.
- [ ] Resolve the rejected Railway public-domain action through the applicable review; only then a protected synthetic hosted test.
- [x] PostgreSQL parity, concurrency, rollback and crash recovery, including the status lock, on throwaway local PostgreSQL 16 clusters (follow-up below).
- [ ] If PostgreSQL is chosen over the Railway SQLite volume: the same suite against the intended instance's settings (TLS, connection limits, its locale, default isolation and any `statement_timeout`), and a server-side `idle_in_transaction_session_timeout` decision (follow-up below).
- [ ] Browser QA beyond headless Chromium: Firefox and Safari, real phones, VoiceOver/TalkBack/NVDA (including what is spoken when a Refresh mode change moves focus to the Connect form, and whether the message is then heard twice, as a live update and as that control's description), real 200%/400% zoom, the native sign-in prompt and its cache.
- [ ] Supervised phone acceptance per `PHONE_ACCEPTANCE_0.8.3.md`, with the user's participation and approvals.
- [ ] Real phone-to-server HTTPS upload to an approved destination; caregiver login and role isolation over real HTTPS; synthetic end-to-end result.
- [ ] Hands-off acceptance on Pixel/Fold and the S25 Ultra: screen-off, reconnect, process and reboot recovery, offline buffering, charging and range loss, without Mom tapping controls.
- [ ] V1/V4 hardware parity; heart-rate accuracy remains independently unverified.
- [ ] A separate explicit decision before any real health data is activated.

## Method

Test strength was checked with mutants that the new tests must kill: deferred transactions, an unbound descending cursor scope, a removed 12-hex rule, a lost hot journal, two admission-slot leaks and commit instead of rollback on error. Product fixes were validated in a separate copy and committed one defect at a time, each with the test that fails without it, while the review agents kept reading the unchanged starting code.

## Follow-up, November 7, 2026

After reviewing the branch, the owner approved three follow-ups: a pull request ([cyberike/mom-health#1](https://github.com/cyberike/mom-health/pull/1), merged on November 7 as `7911545`), PostgreSQL testing on a throwaway database in this container, and conservative defaults for some of the findings above. Every restriction listed at the top still applies.

### PostgreSQL parity on throwaway local clusters

**Environment and isolation.**

- The container already had the Ubuntu PostgreSQL 16.15 server packages. Its stock `main` cluster was stopped and was not touched.
- psycopg[binary] 3.3.8 (bundled libpq 18.6) was installed into the same virtual environment outside the repository.
- `backend/tests/postgresql_throwaway.py` creates a new cluster for each run under the gitignored `build/postgresql-fixtures/<id>/`:
  - The server listens only on a Unix socket in an owner-only directory. `listen_addresses` is empty, so there is no TCP listener.
  - Authentication is `trust` on that socket; TCP is set to `reject`.
  - `fsync`, `synchronous_commit` and `full_page_writes` are on.
  - One fictional non-superuser role owns one `mom_health_synthetic_*` database. The suite refuses any database with another name.
  - Every libpq `PG*` variable is removed from the environment of the launcher's own clients and of the command it runs, so no client can be redirected off the private socket.
- PostgreSQL refuses to run as root, so the server ran as the container's existing `postgres` account through `runuser`. The cluster directory and its `cluster.json` stay root-owned; only `data/`, `log/` and the socket directory belong to that account, so it cannot change what root later runs.
- After each run the launcher stops the server and confirms that it exited: `pg_ctl status` reports it stopped, `postmaster.pid` is gone, and no process names the data directory. With `--discard`, it then deletes the cluster. The same happens when setup fails part-way, and when the launcher is interrupted or terminated (SIGINT, SIGTERM or SIGHUP):
  - A setup step still running (initdb, `pg_ctl`, `psql`) may finish first, so none is left running on its own. One still running after 2 minutes gets SIGTERM, then SIGKILL 10 s later; as root, `runuser` itself ends the step 2 s after the SIGTERM. If something still holds the step's output after the SIGKILL (a stopped step stops `runuser` too), the launcher stops reading it, so the wait stays bounded, and the cleanup reports any process that still names the cluster.
  - A command still running is sent SIGTERM. A signal that arrives while the command is being started is held until the command's process is known, so the command is still stopped.
  - Further signals are ignored until the cleanup is done, and the cleanup's server commands run in their own session, so a repeated group signal cannot cut them short. A signal already ignored at start (under `nohup`) stays ignored.
  - A closed terminal or broken pipe neither stops the cleanup nor changes the exit status. After a failed status line, stderr is pointed at `/dev/null`, so Python's flush at exit cannot fail and turn the status into 120.
  - Only the command's own process is signalled. Programs it started stop too only when the signal reaches the whole process group (Ctrl-C, `timeout`, a closed terminal), so interactive commands keep the terminal.
- The launcher needs Linux. When the checkout path is too long for a Unix socket, the socket goes in a private directory under `/tmp`, named on stderr and removed after the stop. It is not put under `TMPDIR`, which can be long or closed to the server account. If the server does not start, the error includes the end of its log, which `--discard` would delete. The launcher's own behavior is checked by the opt-in `tests/postgresql_launcher_checks.py`.
- Each test works in its own schema, which is dropped afterwards.
- No password, network listener or persistent credential was created, and all data was fictional.

**Coverage.** `backend/tests/postgresql_parity.py` has 41 tests: 37 at first, and four more added after the independent verification (below). It is opt-in, like `integration_contract_android`. Without psycopg or a database it fails with `POSTGRESQL PARITY CHECKS NOT RUN` and never reports a pass.

These suites run unchanged, on PostgreSQL:

- `test_backend.RepositoryTests` (13). The three tests that open the SQLite file are replaced by PostgreSQL equivalents, one of which injects a fault with a real PL/pgSQL trigger.
- `test_concurrent_retries.RepositoryRaceTests` (5).
- `test_device_isolation.DeviceIsolationTests` (6). Its status-order check runs in a PostgreSQL version that forces a sequential scan.
- The two app-restart tests in `test_restart_recovery`.

New checks:

- **Startup.** Eight concurrent first starts create one schema. Startup waits for a brief peer and fails closed on a persistent one; the peer is either another instance's migration lock or an uncommitted write.
- **Killed session.** A session terminated with `pg_terminate_backend` in the middle of a 100-sample ingest leaves nothing behind, and the retry commits once.
- **Server crash.** The server is crash-stopped with `pg_ctl stop -m immediate` while an ingest is open:
  - Batches acknowledged before the crash survive recovery, and replaying them returns identical receipts.
  - The open transaction leaves nothing behind.
  - Writes and reads during the outage get 503; `/v1/health` still says "ready" (finding 5).
  - Retries commit exactly once.
- **Locks.** A brief and a persistent lock holder, for both uploads and status reports. Reads behind a persistent table lock. Every connection's own settings, whatever the server defaults: exactly a 5 s lock timeout and READ COMMITTED. The backend sets no statement timeout of its own, so one set for the server, database or role still applies. The check gives one connection a statement timeout and expects it unchanged; on a second connection without one, it expects what a plain connection with the same settings sees.
- **Status reports.** A report held between `connect()` and the status lock must still carry the later receipt time, which pins lock 7240090003 deterministically. Delayed and 32-way concurrent reports are checked too. The delayed one stalls before both the clock read and the lock, so it does not test the lock on its own.
- **Status order.** With index scans disabled, the real report still wins although the synthetic one is stored first.
- **Isolation settings.** The same races run under stricter default isolation levels.
- **Lost acknowledgment.** A durable commit whose acknowledgment is lost; the retry gets the receipt.
- **Disconnect.** A client gives up mid-ingest while its identical retry waits on the ingest lock.
- **Collation.** The device listing keeps byte order under a linguistic collation.

**Defects found and fixed** (`repository.py`, PostgreSQL path only). Each has a regression test that fails without the fix:

| # | Defect | Evidence before the fix | Fix | Regression test |
| --- | --- | --- | --- | --- |
| 14 | Lock waits had no limit (PostgreSQL's `lock_timeout` defaults to 0). A peer holding the upload, status or migration lock, or an uncommitted write, made uploads and status reports wait indefinitely and made startup hang. SQLite answers 503 after its 5 s busy timeout and fails startup closed. | Requests still waiting after 20 s; startup never finished, so the test server could not be stopped | `SET LOCAL lock_timeout = '5s'` in every PostgreSQL transaction (each operation is one transaction on its own connection). | `PostgreSQLWriteLockTests...persistent_lock_holder_yields_503...`, `PostgreSQLRestartTests...startup_waits_for_a_brief_peer...` |
| 15 | The advisory-lock protocol depended on the server's default isolation level. If a server, database or role sets `default_transaction_isolation` to repeatable read or serializable, the snapshot is taken before the lock wait. Identical concurrent retries then hit unique-key violations or serialization failures and got 503 instead of the stored receipt. No duplicate or wrong acknowledgment occurred. | 15 of 16 identical retries failed under repeatable read; 12 of 16 under serializable | Every connection runs in READ COMMITTED | `PostgreSQLWriteLockTests...stricter_default_isolation_level` |
| 16 | The device listing and its cursor followed the database locale. Under a linguistic collation (here ICU `en-US`; hosted servers are commonly initialized with `en_US.utf8`), the same watches were listed in a different order from SQLite: lowercase before uppercase, `_` before `-`. Nothing was skipped or repeated. | The inherited listing test, run against an ICU database | `COLLATE "C"` (byte order, like SQLite) for the listing and its cursor | `PostgreSQLDeviceIsolationTests...linguistic_collation`, which forces a linguistic column collation so it fails without the fix on any server with ICU |

**Mutants.** Each of these changes makes at least one test fail (6 of 6): removing any one of the three advisory locks (7240090001 migration, 7240090002 upload, 7240090003 status), the READ COMMITTED setting, the lock timeout, or the byte-order collation. The tests added after the verification also catch these seven (7 of 7): reading the receipt time before the status lock; running reads in a second, unbounded transaction; a 700 ms or 19 s lock timeout; a statement timeout instead of the lock timeout; no READ COMMITTED setting; and no ORDER BY on status.

**Commands and results** (from `candidates/full-stack/backend`, outside environment first on `PATH`, as root):

| Command | Result |
| --- | --- |
| `python tests/postgresql_throwaway.py --run-as postgres -- python -m unittest tests.postgresql_parity -v` | 41 OK in about 45 s (C locale), server stopped and exited |
| `python tests/postgresql_throwaway.py --run-as postgres --locale icu-en-US -- python -m unittest tests.postgresql_parity -v` | 41 OK in about 45 s (ICU `en-US`), server stopped and exited |
| The suite as committed in `9039d45`, against the code before the fixes | C: 7 failures (4 lock-wait subtests, 2 isolation subtests, the forced-collation check). ICU `en-US`: 8 (also the inherited listing-order check). Re-measured after the verification. |
| `python -m unittest tests.postgresql_launcher_checks -v` | 26 OK, as root, with and without `PYTHONUNBUFFERED` (the failed-setup check also runs once as the `postgres` account). Each of 27 launcher changes, reverted on its own, makes a check fail (27 of 27; see the third verification). The first 5 checks, against the launcher as merged in `7911545`: 4 failures and 2 errors |
| `python -m unittest tests.postgresql_parity` without a database | exits 1: `POSTGRESQL PARITY CHECKS NOT RUN` |
| `python -m unittest discover -s tests -v` | 174 OK (1 Windows-only skip) after all follow-up changes; the parity and launcher modules are not discovered |

Run as a non-root account, the launcher needs no `--run-as`. The two cluster directories from the first runs on unfixed code (stopped, fictional data, about 47 MB each) were retained under `build/postgresql-fixtures/`; the other runs used `--discard`. A verifier's cluster left by the container restart, and the reviewers' scratch copies, were removed.

**Not shown.**

- Hosted or managed PostgreSQL, TLS, real network latency, connection limits under production load (each request opens its own connection), replication and failover.
- glibc `en_US` collation: the locale isn't installed here, so ICU stood in for it.
- PostgreSQL versions other than 16.15, and psycopg versions other than 3.3.8.

**Operational note for the owner.** If the app host disappears mid-transaction (power loss or a network partition), PostgreSQL keeps that session's locks until it notices the dead connection, which with default TCP keepalives can take hours. Until then, every write that needs those locks is answered 503 after 5 s. A backend restarted meanwhile can also fail to start, because startup waits for the table locks such a transaction holds and fails closed after 5 s. A server-side `idle_in_transaction_session_timeout` of about a minute would bound this. That is a server-configuration choice and was not made here.

### Design defaults chosen

The owner asked for the open choices in "Findings not changed" to be settled conservatively. Each default below is its own commit, with tests that fail without it. The corrections after the independent verifications (below) add tests that need the provenance, Refresh and `Retry-After` defaults: `fa6ccb8`'s status-order checks, the mode-change checks of `42d2ecd` and of the second and third rounds, the date checks of `fdf7381`, and the rounding and date checks of `f4c922a`. `2f06582`'s status-card label also assumes the provenance default. Dropping one of those defaults therefore also means reverting its part of those commits. `54cb4af`'s range test holds without the range default. `gateway.py` and `direct_candidate.py` are unchanged.

| Finding | Default chosen | Why it is the conservative choice | Tests |
| --- | --- | --- | --- |
| 2, status provenance | In real-data mode, `GET /v1/gateway-status` serves the real gateway's report whenever one exists. The synthetic test report is served only when there is no real one, and both dashboards label it synthetic. A real report is shown as the "Last real gateway report received by backend". Synthetic-only mode is unchanged. | A fresh synthetic test report can no longer hide the real watch's state. The real report may be older; its receipt time and age are shown. | `test_device_isolation...serves_the_real_gateway_report_over_a_synthetic_one` and `...whatever_order_the_store_returns_rows`, on SQLite and on PostgreSQL |
| 7, boundary rejections | 400, 401, 403 and 413 rejections carry the same `no-store`, `nosniff`, `no-referrer`, `DENY` and Content-Security-Policy headers as every other response the application sends. Status codes and bodies are unchanged. An unhandled-exception 500 and Uvicorn's own 400 for malformed HTTP are produced outside the application and carry none. | It only adds restrictions: an error response can no longer be cached or framed. | `test_auth_failures...every_rejection_carries_the_security_headers` (all five rejection kinds) |
| 5, Refresh never re-read `/v1/health` | Refresh re-reads `/v1/health` first. If that read shows the backend's data mode changed since Connect, the dashboard clears the session and requests nothing more. Keyboard focus that was lost (on the page body, or still on the disabled Refresh button, which Chromium blurs only at its next frame) or that was on the readings table, which the change has just emptied, moves to the Connect form, and the message then describes that control for screen readers until the next message; focus the user moved to another control stays there. The message names what was removed (in the local copy, the token too); the hosted copy says it requires a synthetic-only backend and to connect again once the backend is synthetic-only. The caregiver connects again deliberately. | Once the health read shows the change, Refresh requests and shows no records, and switching into real-data mode always takes a deliberate Connect, because a synthetic-only session rejects real records everywhere. A change after that read, or during a watch change, Load older or Load more devices, is seen only at the next Refresh. A local session without a token cannot see a switch to real-data mode, because `app.py` then requires a token: its Refresh is refused (401) at its first request, so it keeps the loaded synthetic rows. The cost is one more request per Refresh. Against a backend that answers each request just inside the 12-second timeout, a Refresh can stay busy for up to about 48 s instead of about 36 s, as long as a Connect; one that never answers ends it after 12 s (finding 12). | `lifecycle.test.js`: both dashboards, both directions in the local copy, and where focus goes; `browser.test.js` and `hosted-browser.test.js` in real Chromium |
| 6, time-range filter | Every new session (Connect, preview or Clear session) starts with "All loaded data". | That is the widest range, so a filter from an earlier session can no longer hide loaded records. A range chosen before Connect is reset. | `lifecycle.test.js`, both dashboards |
| 6, `Retry-After` | Both dashboards state the wait a response asks for with `Retry-After`. Only delta-seconds or a valid IMF-fixdate HTTP date are read; anything else, including an impossible date, is ignored. A 429 reads "Too many requests or sign-in attempts (HTTP 429). No current data was confirmed. The backend asked to wait about N seconds before trying again; no automatic retry occurs." Other errors that carry the header get the same final sentence. Waits round up, in seconds, minutes and hours. | It tells the caregiver when a retry can work, without retrying automatically. | `core.test.js`, `lifecycle.test.js`, and `hosted-browser.test.js` in real Chromium through the unmodified `gateway.py` lockout |

Still open:

- Item 1: `gateway.py` must stay intact.
- Item 3: the budgeted-SQLite 503s are in the path of `direct_candidate.py`, the inactive candidate. This review deliberately left it unchanged, although only the original `gateway.py` is required to stay intact.
- Item 4.
- The rest of item 5: health reports process readiness only, and cursors carry no store identity. A mode change after Refresh's health read, or during a watch change, Load older or Load more devices, is seen only at the next Refresh, and a local session without a token sees a switch to real-data mode only as a refused (401) Refresh.
- The rest of item 6: a failed Refresh keeps the count, badge and token, and keeps the loaded rows unless it fails at or after its sample request, which empties the table first. Refresh does not use a newly typed token.
- Items 8 to 15.
- Within item 2, each of a device's two reports is still last-received-wins, regardless of `reported_at`.

Status selection no longer compares receipt times, so defect 2's fix now only keeps the receipt-time format sortable. The test still checks that format.

### Independent verification of the follow-up, and corrections

After the merge, the follow-up (commits `9039d45`..`b4e918d`) was checked the way the November 3 work was. A read-only workflow ran four reviewers (PostgreSQL path and launcher, backend defaults, dashboard defaults, documentation), one adversarial verifier per reviewer that tried to refute every finding with its own reproduction, and a completeness critic. It used fictional data and throwaway clusters only. A container restart interrupted it; it resumed from its saved results.

Outcome: 38 items. 33 were confirmed (some overlap across reviewers) and 5 were judged deliberate design. The critic found 2 more gaps. Every confirmed item was corrected, as follows.

| Area | Confirmed problem | Correction |
| --- | --- | --- |
| Launcher | SIGTERM or SIGHUP (for example from `timeout` or a closed session) left the server running and the cluster behind, even with `--discard` | Signals now run the cleanup; a command still running is stopped first (completed in the second and third verifications) |
| Launcher | As root, it ran `pg_ctl` from a `cluster.json` that the server account could replace, so that account could run code as root. Root also appended to a file inside that account's directory | The cluster directory and `cluster.json` are root-owned; the server account writes its own config |
| Launcher | A failed setup left the cluster directory behind, even with `--discard` | Cleanup no longer depends on setup finishing |
| Launcher | Version directories sorted as strings, so 9.6 would be chosen over 16 | Numeric sort |
| Launcher | `PG*` variables (for example `PGHOSTADDR`) could send its clients off the private socket. Judged design, adopted | Removed from every client's environment |
| Launcher | Deep checkouts exceeded the Unix socket path limit. Judged design, adopted | Short private socket directory, removed after the stop (moved under `/tmp` in the second verification) |
| Parity suite | Imported the Linux-only launcher at load time, so it could not run elsewhere even with only a DSN (critic) | Imported only inside the crash check |
| Tests | The status lock's receipt-time rule, the lock bound on reads, the 5 s value and mechanism, and the status `ORDER BY` were not pinned | New tests on both backends (the SQLite one reverses unordered results); every corresponding mutant now fails |
| Dashboards | After a mode change, "Connect again" did not say the token was removed too (local), and led to a Connect that always fails (hosted). Focus dropped to the page body | Per-copy wording; focus moves to the Connect form (refined in the second verification); real-browser checks for both copies |
| Dashboards | Impossible `Retry-After` dates were read through `Date.parse` | A date counts only if it round-trips to the same IMF-fixdate text |
| Dashboards | The status card called an older real report the "Last report received by backend", although a newer synthetic one had arrived. Judged design; adopted, since the provenance default caused it | "Last real gateway report received by backend" |
| Tests | Keeping a chosen range within a session, no retry scheduled after a 429, the `Retry-After` wording boundaries and rounding, the null-health guard and the mode-change error styling were not pinned; the `Retry-After` test also depended on the Refresh default | New and corrected lifecycle and core checks; every corresponding mutant fails (10 of 10, including both real-browser checks). Minute- and hour-level rounding was still unpinned; see the second verification |
| Documentation | 14 inaccurate statements: wrong before-fix counts, the Refresh timing, overstated claims about the mode re-check, revertibility, the operational note, item 2 and item 3, the libpq version, the merge state, the security-header scope, the Railway README's `Retry-After` note, "anything else is ignored", and the stale "24 backend tests" line. The critic added the README's "latest report" wording | All corrected here and in the READMEs |

Left as documented design: the PostgreSQL comment no longer claims pooler safety, and psycopg's statement preparation is unchanged, since no pooler is used. A preview Refresh still starts a new preview, and so resets the range.

Results after the corrections:

| Suite | Result |
| --- | --- |
| Backend `unittest discover` | 174 OK (1 Windows-only skip) |
| `test_gateway` | 12 OK |
| `integration_contract_android` | 5 OK |
| PostgreSQL parity | 41 OK under both locales |
| PostgreSQL launcher checks | 5 OK |
| Dashboard `core` / `app` / `hosted` / `lifecycle` / `integration` | 39 / 10 / 6 / 51 / 6 passed |
| `browser` / `hosted-browser` (real Chromium) | 12 / 8 passed |

### Second independent verification, and corrections

The seven correction commits (`5577105`..`5d9fd1b`) were checked the same way: a read-only workflow ran three reviewers (launcher and PostgreSQL, dashboards, documentation), one adversarial verifier per reviewer and a completeness critic. It used fictional data, throwaway clusters and copies under the gitignored `build/` only. One verifier also ran a read-only `git ls-remote` against the GitHub remote, and said so; it changed nothing.

Outcome: 28 items. 25 were confirmed (some overlap across reviewers), 2 were judged deliberate design and 1 was refuted. The critic found 2 more gaps. Every confirmed item and both gaps were corrected, as follows.

| Area | Confirmed problem | Correction |
| --- | --- | --- |
| Launcher | When the terminal closed or the output pipe died (`... \| tee log`, then Ctrl-C), the status lines raised. The cleanup then stopped before removing the temporary socket directory and, with `--discard`, the cluster | Status lines can no longer fail. Checks with a closed pipe and a closed terminal |
| Launcher | Run as an ordinary account with a wrong `--bindir`, the cleanup's status check raised, so a failed setup left its cluster. The check passed only as root | A missing `pg_ctl` means no server is running. The failed-setup check also runs as the `postgres` account |
| Launcher | A signal to the launcher alone during initdb killed only `runuser` (or initdb itself). The orphaned initdb kept writing, `pg_ctl stop` refused its single-user backend and the cleanup was skipped. Very early group signals could hit the same window | An interrupted setup step may finish (it is killed after 2 minutes; as root that bounded only `runuser` until the third verification). A failed stop is reported and retried, and the cleanup waits up to 60 s until no process names the cluster |
| Launcher | As root, a repeated group SIGTERM made `runuser` kill `pg_ctl stop`, so the cleanup aborted | The cleanup's server commands run in their own session |
| Launcher | Under `nohup`, the launcher replaced the ignored SIGHUP with its own handler, so a closed terminal ended the run. A regression | Signals ignored at start stay ignored |
| Launcher | The short socket directory followed `TMPDIR`, which can be long or closed to the server account. The server then could not start, and `--discard` deleted the only log that said why (critic) | Always under `/tmp`, and named on stderr. A failed start reports the end of the server log |
| Launcher | On a signal to the launcher alone, only the command's own process is stopped; programs it started keep running. Judged a trade-off | Documented. The command stays in the launcher's process group, so interactive commands keep the terminal |
| Launcher checks | Four fixes were not pinned: who appends the configuration, a second signal, the temporary socket directory after a failed setup, and its mode. Two checks shared fixed paths and failed when runs overlapped. One needed `build/` to exist | 16 checks. Six of the eight launch checks used their own copy in a unique directory (all of them since the third verification), and the unit checks need no `build/`. Each of the 15 listed launcher changes, reverted on its own, makes a check fail; the signal block around the socket directory's creation stayed unpinned until the third verification |
| Parity suite | The settings check's "no statement timeout" read the server's default, not the backend's | The check gives the connection a statement timeout and expects it unchanged (a backend that set one only when none was in force passed it until the third verification) |
| Dashboards | If the user had moved focus to the readings table while a Refresh waited, focus still ended on the page body after a mode change | Focus on the emptied table also moves to the Connect form |
| Dashboards | Nothing pinned that focus the user moved to another control stays there; the simulated DOM had no focused element | The simulated DOM tracks focus. Lifecycle and real-Chromium checks cover both cases in both copies |
| Dashboards | A screen reader might speak the newly focused control and drop the polite status update (critic; not demonstrated, since no screen reader is available here) | While the message is current, it is the accessible description of the control that received focus, as Chromium's accessibility tree shows. The next message removes it. A screen reader may then speak it twice, as a live update and as the description; not checked with one |
| Dashboards | The hosted message gave no next step. Judged design; adopted | "Connect again once the backend is synthetic-only." |
| Tests | Minute- and hour-level `Retry-After` rounding was not pinned: every case read the same when rounded to the nearest unit | Cases where the two differ. `24:00:00`, which `Date.parse` reads as the next midnight, is the rollover example; `Date.parse` already rejects 24:30 and 12:61 |
| Documentation | The merge state in the opening line and the checklist; revertibility, which omitted the provenance default; the rounding claims; the statement timeout; the "wedged backend" timing in findings 5 and 12; the dashboard README's counts; the "Still open" list. The dashboard reviewer added the unqualified focus statements, the tokenless case and the check-then-request window | Corrected here and in the dashboard README |

Commit messages are not rewritten: `5577105` says its checks "pin all of this", and `fdf7381` names 24:30 among dates that `Date.parse` misread. The table above corrects both. Left unchanged by design: after a switch to real-data mode the hosted copy still puts focus on Connect, the control that works once the backend is synthetic-only again. The refuted item said `5d9fd1b` claimed a code fix it does not contain; it carries the documentation for both critic gaps, and `fa6ccb8`'s message credits the code fix.

**Mutants.** Each listed change, reverted on its own, makes at least one test fail: 15 of 15 for the launcher (each closed-stream, missing-`pg_ctl`, setup-step, session, ignored-signal, socket-directory, failed-stop, log-tail, configuration-append, socket-mode and stderr-naming change; the configuration-append, failed-setup and socket-mode ones pin first-round behavior), and 12 of 12 for the dashboards (no hand-over from the emptied table, focus moved unconditionally, no description link, a stale description, no hosted next step, minutes or hours rounded to nearest, dates not round-tripped; each copy where the change applies to both). The focus and description-link mutants fail in the simulated DOM and in real Chromium; the stale-description ones failed only in the simulated DOM until the third verification. In that run the socket-directory mutant was recorded as killed by a `FileNotFoundError` (the check needed `build/` to exist), not by a detection; the third verification's rerun catches it for real.

Results after these corrections:

| Suite | Result |
| --- | --- |
| Backend `unittest discover` | 174 OK (1 Windows-only skip) |
| `test_gateway` | 12 OK |
| `integration_contract_android` | 5 OK |
| PostgreSQL parity | 41 OK under both locales (about 44 s each), server stopped and exited |
| PostgreSQL launcher checks | 16 OK, as root |
| Dashboard `core` / `app` / `hosted` / `lifecycle` / `integration` | 39 / 10 / 6 / 54 / 6 passed |
| `browser` / `hosted-browser` (real Chromium) | 13 / 9 passed |

No PostgreSQL process, temporary socket directory or launcher copy was left after these runs; the two retained clusters from the first runs are unchanged.

### Third independent verification, and corrections

The five commits of the second round of corrections (`041fd0b`..`809ac41`) were checked the same way: a read-only workflow ran four reviewers (launcher behavior, launcher checks and parity, dashboards, documentation), one adversarial verifier per reviewer and a completeness critic. They used fictional data, throwaway clusters and copies under the gitignored `build/` only. A scan of their recorded tool calls found no network command.

Outcome: 24 items. 23 were confirmed (some overlap across reviewers: four documentation items repeat a dashboard or launcher-check item) and 1 was judged a design choice. The critic found 3 more gaps; it had already probed the third and found it held. Every confirmed item and the first two gaps were corrected, as follows.

| Area | Confirmed problem | Correction |
| --- | --- | --- |
| Launcher | A signal sent to the launcher alone while Popen started the command was raised before the launcher knew the command's process. The command was never sent SIGTERM and kept running, although the launcher reported a clean stop (about a third of timed attempts). Pre-existing | Such a signal is held until the command's process is known, then stops the run as usual. Signals are not ignored or blocked meanwhile: the command would inherit that and could then not be stopped |
| Launcher | As root, a setup step still running 2 minutes after a signal was never killed: only `runuser` was, and the launcher then read the step's output until the step itself exited. Docstring, commit message and report said it was killed | SIGTERM first (`runuser` ends the step 2 s later), SIGKILL 10 s after that, and the launcher stops reading if something still holds the output, as a stopped step does: it stops `runuser` too. The cleanup then reports what still names the cluster |
| Launcher (critic) | Without `PYTHONUNBUFFERED`, which this container sets for every process, a status line that met a closed pipe or terminal stayed buffered and Python's flush at exit turned the exit status into 120, also after a successful command. The closed-stream check failed outside this container | After a failed status line, stderr points at `/dev/null`. The closed-stream checks run with stderr buffered and unbuffered, and also for a command that succeeds; a unit check runs a child that writes after its reader closed |
| Launcher checks | Unpinned: the cleanup's own session inside `run()` (16 of 16 passed without it), the shield against a signal during the cleanup after a normal exit (without it the launcher ended with a traceback and left its cluster), the handler's own `ignore_signals()`, and `create()`'s two guards for the `/tmp` socket directory | The repeated-signal check also requires that no stop failed. New checks for a signal during the cleanup after the command succeeded (to the launcher and to its group), for the handler, and for a signal right after the socket directory is made and a failed `cluster.json` write |
| Launcher checks | Two launch checks ran the checkout's own launcher, so a failure there left a running server, its cluster and socket directory next to the retained clusters. The second round's report and commit said every launch check used a private copy | Both use private copies, and the waiting command names its copy, so a failed check stops it too (shown with a launcher that SIGHUP kills outright: the check fails in about 5 s and leaves nothing). The ownership check also runs a short copy under `/tmp`, whose socket stays in the cluster, the branch the real checkout uses |
| Launcher checks | The `TMPDIR` check failed with `FileNotFoundError` unless `build/` existed, and that error was what "killed" the second round's socket-directory mutant. A setup check left a `/tmp/mhpg-*` directory whenever `TMPDIR` was longer than 36 bytes (macOS's per-user `TMPDIR` is about 48). The module did not import without `termios`, contrary to the commit message | The check creates `build/` first, and the mutant is now caught for real in a copy without `build/`. The setup check keeps any socket directory in its own temporary root. `pty` is imported only where it is used; the module imports without `termios` and `pwd` |
| Parity suite | A backend that set a statement timeout only when none was in force passed the settings check (design question; adopted) | A second connection without a statement timeout must see what a plain connection with the same settings sees |
| Dashboards | Chromium keeps a focused button that was just disabled as the active element until its next frame. A mode-change answer handled before then (Refresh activated without an input event, as an assistive technology's default action can be, and answered at once) took the disabled Refresh button for focus the user had moved: focus ended on the page body with no description. Both copies, also before the second round | A disabled control counts as lost focus. A lifecycle case for both copies, and a real-Chromium check that answers `/v1/health` at once after a script click and requires focus to go straight to the token field |
| Dashboards | The stale-description mutants passed both real-Chromium suites, although the report and `e72b1e6` said every description mutant failed there | Both suites check, after the next message, that the control has no description left |
| Dashboards | At phone widths the emptied table still scrolls and keeps focus, so the stated reason for handing focus on from it (Chromium drops it later) held only above about 420 px | Wording only: after a mode change, focus on the emptied table is handed on at any width, because the session has ended |
| Dashboards | An ordinary Refresh that emptied the table (no watch listed any more) dropped focus that was on the table to the page body. Pre-existing | Focus returns to Refresh when the table no longer has anything to scroll; at phone widths, where the empty table still scrolls, it stays. Real-Chromium checks in both copies, at 1280 and 320 px in the local one |
| Dashboards | With no watch listed any more, Refresh said "another watch is shown" while the selector said "No watch data loaded". Pre-existing | "…so no watch is shown." A lifecycle check for both copies, also asserted in both real-Chromium checks above |
| Documentation | "Like any failed Refresh, keeps the loaded synthetic rows" (a Refresh that fails at or after its sample request has already emptied the table); the revertibility sentence omitted `f4c922a`; the second round's "15 of 15" label (three of them pin first-round behavior, and the new signal block was unpinned); the "Still open" list lacked two item-5 residuals; the second-round account omitted a verifier's disclosed `git ls-remote` (design question; adopted) | Corrected above and in the dashboard README, including item 6's own wording |

Left as a design choice: while the mode-change message is current it is both the polite status update and the focused control's description, so a screen reader may speak it twice. That is now noted in the second-round table and the acceptance checklist; no screen reader was available to check it. The critic's third gap, the in-cluster socket branch and a real failed start, held when it probed them; the ownership check now covers the first, and the parity runs below use it from the real checkout.

Commit messages are not rewritten. `041fd0b` says the checks module "imports anywhere", that each launch check uses a private copy, that an interrupted step is "killed after two minutes", and that each fix reverted on its own makes a check fail. `d82228c` says a backend that sets its own statement timeout fails the settings check, which was not true of one that sets it only when none is in force. `e72b1e6` says each change fails the real-browser checks too. The table above corrects each.

**Mutants.** Each change below, reverted (or applied) on its own, makes at least one check fail.

- Launcher: 27 of 27, run in a copy with no `build/` at first and with `PYTHONUNBUFFERED` unset. These are the 15 of the second round (rerun against the new code) and 12 new ones: no shield during the cleanup after a normal exit; a handler that leaves signals handled; setup steps never in their own session; no signal block around the socket directory; the directory kept after a failed write; no signal held while the command starts; signals ignored while one is held; a held signal never acted on; a late step killed without SIGTERM; its output read to the end after the kill; a late step never killed; stderr left broken for the exit flush.
- Dashboards: 18 of 18. These are the 12 of the second round, whose stale-description mutants now also fail in real Chromium, and 6 new ones, one per copy each: a disabled control keeps focus; an emptied table keeps focus after Refresh; "another watch is shown" when none is.
- Parity, in a throwaway cluster: the real `connect()` passes with no default and with a role, database or server default. Variants that set 30 s, disable the timeout, or set 30 s for the session fail under all four. One that sets 5 s only when none is in force fails without a default; with one it sets nothing, like the real code.

**Not run.** The critic suggested probing a real initdb stopped mid-setup (SIGSTOP) under the launcher. This session's permission classifier refused that probe, and it was not retried. The bounded wait is pinned instead by the process-level checks above: a step that ignores SIGTERM, a step whose child holds its output, and, as root, a step under real `runuser`. A stopped child was seen to stop `runuser` too; that case then takes the path of the check whose child holds the output: the launcher stops reading after the SIGKILL.

Results after these corrections:

| Suite | Result |
| --- | --- |
| Backend `unittest discover` | 174 OK (1 Windows-only skip) |
| `test_gateway` | 12 OK |
| `integration_contract_android` | 5 OK |
| PostgreSQL parity | 41 OK under both locales (about 44 s each), run from the real checkout, whose socket stays in the cluster; server stopped and exited |
| PostgreSQL launcher checks | 26 OK as root, with `PYTHONUNBUFFERED` unset and set (about 42 s each) |
| Dashboard `core` / `app` / `hosted` / `lifecycle` / `integration` | 39 / 10 / 6 / 56 / 6 passed |
| `browser` / `hosted-browser` (real Chromium) | 15 / 10 passed (about 56 s together) |

No PostgreSQL process, temporary socket directory or launcher copy was left after these runs, and the two retained clusters are unchanged.
