# Private caregiver dashboard — local candidate

Current integration update: six checks now exercise app.js against actual FastAPI/SQLite responses using a local subprocess bridge and simulated DOM. Empty connected dashboards can refresh to discover their first arriving records. A later synthetic pass at the handoff confirmed 38 core/static and 9 UI-flow checks (current counts: `../../REVIEW_20261103_RESILIENCE_TESTS.md`), including rejection of unsupported real V4/history HR and clearing the prior device's readings when the selected source disappears. The receipt-age label refers to the last backend-saved record, which is not necessarily the newest measurement when uploads are delayed. See ../INTEGRATION_RESULT.md for the earlier integration setup. This does not claim real browser or phone HTTPS validation, and nothing has been deployed.

Buildless original HTML/CSS/JavaScript, served by the existing FastAPI backend at `/dashboard/`. No vendor artwork, libraries, CDN, analytics or external assets. The visual direction matches the phone prototype: charcoal cards, blue-purple welcome area and coral heart-rate accents. It is responsive and uses native form controls, keyboard focus, semantic tables, SVG descriptions, live status and text labels rather than color alone.

## Implemented experience

- Explicit connection to the same-origin backend; optional token held only in JavaScript memory, never browser storage. No arbitrary destination field. All data requests are GET, omit ambient credentials, reject redirects, disable caching and have a 12-second timeout with a 256 KiB body bound.
- Device selection, paginated source discovery (maximum 200), explicit refresh and bounded history pagination (maximum 1,000 observations). Cursors are checked for repetition; foreign-device responses and conflicting observation IDs are rejected.
- Backend observation count, latest source summary, phone receipt timestamp and age, date-range filtering, individual-observation plot and accessible history table with observation IDs. Equal timestamp/value records with different IDs remain distinct; repeated identical IDs are omitted and conflicting IDs stop loading.
- Explicit last-reported gateway status from `/v1/gateway-status`: connection state, nullable battery, pending/uploaded phone counts, recording flag, gateway report time and backend receipt time. Absent reports remain unavailable. In real-data mode the backend serves the real gateway's report whenever one exists; a synthetic test report appears only without one and is labeled as synthetic. A real report is shown as the "Last real gateway report received by backend"; a newer synthetic report is then not shown. A report never becomes a current-online claim; receipt age is not a clinical freshness threshold or proof of wear.
- Explicit synthetic preview generated locally, marked in the banner and metrics. It makes no API call and is never sent to the server. Initial state is empty, not a fabricated patient overview.
- Clear session removes loaded records/token and cancels outstanding requests. Leaving the page clears the in-memory session. Every new session (Connect, preview or Clear session) starts from all loaded data, so an earlier time-range choice never hides records. No automatic polling, upload, background activity or browser persistence.
- Refresh first re-checks `/v1/health`. If that read shows the backend's data mode changed since Connect (synthetic-only or real data), the session is cleared and nothing more is requested; connect again deliberately. Keyboard focus that was lost (with the disabled Refresh button, or on the emptied table) moves to the Connect form, and the message then describes that control for screen readers; focus moved to another control stays there. A change after that read, or during a watch change, Load older or Load more devices, is seen only at the next Refresh; a synthetic-only session still rejects real records. A local session without a token cannot see a switch to real-data mode, because the backend then requires a token: that Refresh is refused (401) and, like any failed Refresh, keeps the loaded synthetic rows. In the hosted copy, a change to real-data mode lasts until the backend is synthetic-only again, because that copy requires a synthetic-only backend; its message says so.
- Error messages state any wait the backend asks for with `Retry-After` (for example a 429 sign-in lockout), in seconds, minutes or hours, rounded up. Only delta-seconds or a valid IMF-fixdate date is read; anything else, including an impossible date, is ignored. Nothing retries automatically.

The backend enforces loopback and API authorization. Static HTML/JS/CSS shell can be served without the token so a caregiver can enter it; the shell contains no health data. This is not public hosting or a production multi-user identity system. No ongoing-access credential has been generated or configured.

## Files and testing

`index.html`, `styles.css`, `core.js`, `app.js`; pure logic/static guard checks in `core.test.js` and no-network simulated DOM/HTTP flow checks in `app.test.js`.

Run the prepared synthetic checks with installed Node:

```powershell
node core.test.js
node app.test.js
node --check app.js
node --check core.js
```

These checks do not prove actual rendering, browser fetch cancellation, focus behavior or screen-reader accessibility. A real browser pass is still required. No local server or browser was launched by the dashboard worker; no device operation or real-health-data load occurred.

November 3, 2026 additions (see `../../REVIEW_20261103_RESILIENCE_TESTS.md`): `node lifecycle.test.js` runs late-response, timeout and failure-recovery checks against both dashboard copies with the same simulated harness. `node browser.test.js` and `node hosted-browser.test.js` drive real headless Chromium (Playwright) against the real backend on 127.0.0.1 with fictional data and disposable databases; put the backend's Python environment first on `PATH`. Without Playwright they print `BROWSER CHECKS NOT RUN` and exit 2. Only Chromium was exercised; other browsers, real phones and screen readers remain unverified.

The separate Railway candidate uses `../railway_candidate/dashboard-index.html` and `../railway_candidate/dashboard-app.js`. Its client requires the backend to report `synthetic_only: true` before requesting device records; a real-data-mode response is rejected. `node hosted.test.js` runs six simulated DOM/transport checks against those hosted files, including same-origin browser-sign-in request settings and refusal of real-data mode and real observations. This does not verify browser Basic-auth caching, real HTTPS/proxy behavior or a hosted deployment. The candidate remains synthetic-only and is not approved for real health data.

After separate permission for local service/browser operation, start the existing backend using its documented command and open the loopback `/dashboard/` URL. First inspect the empty state and synthetic preview at desktop/mobile widths, 200% zoom and keyboard navigation. Then use an isolated synthetic backend to test connect, device changes, refresh, older pages, time filters, malformed/error responses, cancellation/clear-session and auth failure. Do not use real records for initial UI acceptance.

## Remaining backend/data dependencies

The frontend uses `/v1/health`, `/v1/devices?limit=50&cursor=...`, `/v1/samples?device_id=...&limit=50&order=desc&cursor=...`, and `/v1/gateway-status?device_id=...`. A device summary's `latest` means last server insertion, not necessarily most recent physical measurement; no device timestamp is invented. The status route accepts explicit gateway reports, but reports will be absent until the gateway is separately configured and exercised. Unattended acquisition/sync remains separate gateway/backend work. Hands-off caregiving is the intended product goal, not a completed capability asserted by this dashboard.

## Completed checks after reconnection

37 core/static checks and 7 simulated UI-flow checks passed then (current counts: `../../REVIEW_20261103_RESILIENCE_TESTS.md`); app/core JavaScript syntax checks passed. The simulation covers empty start, explicit preview, token rejection, same-origin authenticated reads, status-only devices, foreign-page rejection and cancellation/late-response isolation. No browser, HTTP listener, external request, phone/watch or real health data was used. Actual visual rendering, browser behavior, zoom and assistive-technology QA remain pending.

The interrupted status patch had not landed; it was reconciled before editing. Source bytes were valid UTF-8 with no control characters; heart glyphs were changed to HTML entities because the Windows shell display had misrendered them. The original test failure was a test false positive that mistook the SVG XML namespace for an external network URL; its corrected guard still rejects actual external requests.
