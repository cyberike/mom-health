# Resilience and real-browser test review — November 3, 2026

Review branch `claude/mom-health-test-coverage-ggkdxz`, started from `567c0e1ce6bdbe92bfddd1c8631c4e78e32420d0` (the handoff commit on `main`). All work used the current candidate under `candidates/full-stack`; the root README describes an older version and was not used as a guide. Nothing was merged; the branch awaits owner review.

This is local synthetic testing in a cloud Linux container. Nothing was deployed or published, and no phone, watch, ADB, emulator, Docker, Railway, Caddy, Cosign/Rekor, credential store or real health data was touched. **Cloud testing does not validate the physical watch, the phone, Bluetooth, Android storage, real phone-to-server networking or unattended reliability.**

## Restrictions kept

- Handoff boundaries are unchanged: the Rekor/Caddy signature verification remains unresolved and was not retried; the rejected Railway public-domain action was not retried or approximated; no new persistent credentials, accounts, services or costs were created; WSL, Docker and emulator-ADB denials were respected.
- Only fictional records and obviously fictional in-test credentials were used. Every database was a fresh disposable SQLite file under the gitignored `candidates/full-stack/build/<suite>/<uuid>/`, retained for inspection like the existing suites. The browser launcher refuses any database outside its own fixture directory.
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

Separate adversarial verifiers were re-running each reported defect against the unchanged starting code when this report was first committed; their verdicts are recorded here when they finish. Every fix above already rests on a test that fails before it and passes after it.

## Findings not changed (decisions for the owner)

These were measured or demonstrated but are documented limitations, design choices or wording questions rather than clear defects. None was changed.

1. **gateway.py sign-in lockout.** Requests without an Authorization header count as failures, and a browser sends two during each ordinary Basic sign-in, so in headless Chromium the fourth fresh legitimate sign-in within a minute was refused with 429 (no wrong password); reloads in an already signed-in browser cost nothing. Its lockout 429 carries no challenge, so a browser that is not yet signed in sees a bare JSON page until the window expires. Role-wide lockout is documented; this browser cost is now recorded in `railway_candidate/README.md`. `direct_candidate.py` does not count header-less requests: ten fresh sign-ins were never throttled, and a repeated wrong password was throttled without locking out the caregiver.
2. **Status provenance in real-data mode.** `status()` returns whichever of a device's synthetic and real reports arrived last, so a fresh synthetic test report hides the real watch's report. Status is also last-received-wins regardless of `reported_at`, so a delayed older report replaces a newer one. A per-upsert sequence would also survive backward clock steps.
3. **Budgeted SQLite under its own concurrency.** With the 0.25 s busy timeout, 12-17 of 32 concurrently admitted writes return 503 after release. Integrity holds and retries converge. A `Retry-After` on storage 503s or server-side write serialization would reduce retries.
4. **Replay receipts** are JSON-equal but not byte-identical (key order differs between the first response and replays). This matters only if receipts are ever hashed or signed.
5. **Readiness and cursors.** `/v1/health` reports process readiness only; it stays "ready" while writes return 503. Restarting against an empty or different database also starts "ready", and cursors carry no store identity (an old descending cursor then returns new rows as "older"). Refresh never re-reads `/v1/health`, so a mode change after a backend restart is not re-validated until the next Connect (it fails closed only in the synthetic direction).
6. **Failed refresh display.** After a failed Refresh the previously loaded rows, count and badge stay visible (the status card and pager now say what was not loaded). The in-memory token is kept until Clear session or the next Connect, and Refresh does not use a newly typed token. Neither dashboard shows `Retry-After` for 429. Clear session does not reset the time-range filter.
7. **Boundary rejections** (400/401/403/413) are sent before the security-header wrapper, so they lack `no-store`, `nosniff`, CSP and frame options. Their bodies are fixed and non-sensitive.
8. **Bearer matching** is byte-exact: the scheme is case-sensitive and exactly one space is required (RFC 9110 treats schemes as case-insensitive). Over a real listener h11 trims surrounding whitespace. The Android uploader sends exactly `Bearer `. Both layers are now pinned.
9. **Small HTTP inconsistencies.** `/dashboard` without the trailing slash returns a JSON 401 when a token is configured (the shell itself is public). `HEAD` on the shell returns 405 because FastAPI GET routes do not register HEAD. Neither leaks data.
10. **Direct candidate clock handling.** Any wall-clock regression returns 503 for every role, even with no previous credential configured, and a forward jump followed by a correction keeps it unavailable until real time catches up. This is documented; it is now pinned.
11. **More than 50 devices.** Refresh still reloads only the first device page. It now announces when the chosen watch had to be replaced, but it does not page on to keep the selection.
12. **Timeouts and page lifecycle.** The 12-second timeout is per request, so a connect or refresh of up to four requests can stay busy for about 48 s against a wedged backend. `pagehide` clears the session without an announcement; Chromium does not restore the page from the back/forward cache (it is `no-store`), and other engines were not tested.
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
| `python -m unittest discover -s tests -v` | 172 tests OK (1 Windows-only skip) |
| `python -m unittest railway_candidate.test_gateway -v` | 12 OK |
| `cd tests && python -m unittest integration_contract_android -v` | 5 OK |
| `cd dashboard && node core.test.js` | 38 passed |
| `node app.test.js` | 10 passed |
| `node hosted.test.js` | 6 passed |
| `node lifecycle.test.js` | 40 passed |
| `node integration.test.js` | 6 passed |
| `node browser.test.js` (Playwright + Chromium) | 11 passed |
| `node hosted-browser.test.js` (Playwright + Chromium) | 6 passed |
| `node --check app.js && node --check core.js && node --check ../railway_candidate/dashboard-app.js && node --check browser-support.js` | OK |

The full backend run takes about 27 s and both browser suites about 50 s on four shared CPUs. New fixture directories accumulate under `candidates/full-stack/build/` (gitignored), as with the existing suites.

## Blocked or not run

- PostgreSQL runtime, including the new advisory-lock path in `save_status`: no psycopg or server, and Docker remains denied.
- Caddy and the Rekor transparency check: unchanged and not retried. Railway domain, deployment, public HTTPS, a real proxy or edge: not attempted.
- Phone, watch, BLE, Android storage, ADB, emulator, S25 Ultra: not allowed. The Android portable Java suites were not run; they are outside this request and their runner ships in the build kit, not this repository.
- Firefox, WebKit/Safari, headed browsers with a native Basic sign-in prompt, real mobile browsers and screen readers: not available here.
- Multi-worker or multi-replica limiter state, real clock steps and power-loss durability: not exercised.

## Remaining acceptance checklist

- [ ] Owner review of this branch; merge decision (not merged).
- [ ] Choose the ingress candidate in light of the measured `gateway.py` sign-in lockout, and decide items 2, 5, 6 and 7 above.
- [ ] Resolve the Rekor/Caddy verification through legitimately applied, confirmed scoped access, then run the prepared Caddy loopback harness.
- [ ] Resolve the rejected Railway public-domain action through the applicable review; only then a protected synthetic hosted test.
- [ ] PostgreSQL parity, concurrency and rollback on an approved isolated instance, including the new status lock.
- [ ] Browser QA beyond headless Chromium: Firefox and Safari, real phones, VoiceOver/TalkBack/NVDA, real 200%/400% zoom, the native sign-in prompt and its cache.
- [ ] Supervised phone acceptance per `PHONE_ACCEPTANCE_0.8.3.md`, with the user's participation and approvals.
- [ ] Real phone-to-server HTTPS upload to an approved destination; caregiver login and role isolation over real HTTPS; synthetic end-to-end result.
- [ ] Hands-off acceptance on Pixel/Fold and the S25 Ultra: screen-off, reconnect, process and reboot recovery, offline buffering, charging and range loss, without Mom tapping controls.
- [ ] V1/V4 hardware parity; heart-rate accuracy remains independently unverified.
- [ ] A separate explicit decision before any real health data is activated.

## Method

Test strength was checked with mutants that the new tests must kill: deferred transactions, an unbound descending cursor scope, a removed 12-hex rule, a lost hot journal, two admission-slot leaks and commit instead of rollback on error. Product fixes were validated in a separate copy and committed one defect at a time, each with the test that fails without it, while the review agents kept reading the unchanged starting code.
