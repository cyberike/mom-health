(function () {
  "use strict";
  const core = window.MomHealthDashboard;
  const el = id => document.getElementById(id);
  const ui = Object.fromEntries(["modeBadge", "clearSession", "announcement", "accessToken", "connectForm", "connectButton", "demoButton", "deviceSelect", "moreDevices", "refreshButton", "sourceStatus", "latestBpm", "latestLabel", "latestTime", "sampleCount", "countNote", "receiptAge", "receiptNote", "rangeSelect", "chartEmpty", "chartWrap", "historyChart", "loadedStatus", "historyRows", "loadMore", "pageNote", "statusReportTime", "watchLink", "watchLinkNote", "watchBattery", "watchBatteryNote", "phoneQueue", "phoneQueueNote"].map(id => [id, el(id)]));
  let mode = "none", token = "", devices = [], rows = [], deviceCursor = null, sampleCursor = null;
  let gateway = null;
  let selectedId = "", busy = false, generation = 0, controller = null, syntheticOnly = true, focusReturn = null, errorShown = false, statusLoaded = false, samplesLoaded = false;
  const seenDeviceCursors = new Set(), seenSampleCursors = new Set();
  const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" });
  const timeFormat = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
  function say(message, error = false) { ui.announcement.textContent = message; ui.announcement.classList.toggle("error", error); errorShown = error; }
  function localTime(value) { return dateFormat.format(new Date(value)); }
  function currentDevice() { return devices.find(d => d.device_id === selectedId) || null; }
  function setBusy(value) {
    // Disabling the focused control drops keyboard focus to <body>. Remember the
    // control that started an operation and return focus to it afterwards when
    // focus was lost and the control is usable again.
    const starting = value && !busy, ending = busy && !value;
    if (starting) { const current = document.activeElement; focusReturn = current && current !== document.body ? current : null; }
    busy = value; ui.connectButton.disabled = value; ui.refreshButton.disabled = value || mode === "none";
    ui.deviceSelect.disabled = value || !devices.length; ui.loadMore.disabled = value || !sampleCursor || rows.length >= core.MAX_SAMPLES;
    ui.moreDevices.disabled = value || !deviceCursor || devices.length >= core.MAX_DEVICES;
    ui.moreDevices.hidden = !deviceCursor;
    ui.connectForm.setAttribute("aria-busy", String(value));
    if (ending) {
      const target = focusReturn, current = document.activeElement; focusReturn = null;
      if (target && target.isConnected && !target.disabled && !target.hidden && (!current || current === document.body)) target.focus();
    }
  }
  function clearSession(notify = true) {
    generation++; if (controller) controller.abort(); controller = null;
    mode = "none"; token = ""; gateway = null; devices = []; rows = []; selectedId = ""; deviceCursor = null; sampleCursor = null; statusLoaded = false; samplesLoaded = false;
    seenDeviceCursors.clear(); seenSampleCursors.clear(); ui.accessToken.value = "";
    // Every session starts from the widest range, so an earlier filter never hides loaded records.
    ui.rangeSelect.value = "all";
    ui.modeBadge.textContent = "Not connected"; ui.modeBadge.className = "badge neutral";
    ui.sourceStatus.textContent = "Backend: not connected · Refresh is manual";
    renderDevices(); render(); setBusy(false);
    if (notify) say("Session cleared. Loaded readings and token removed from this page. Saved backend records are unchanged.");
  }
  async function request(path, epoch) {
    if (location.protocol !== "http:" && location.protocol !== "https:") throw new Error("Open this dashboard through its configured backend to connect. Synthetic preview works without a backend.");
    const abort = controller;
    const timer = setTimeout(() => { if (abort) abort.abort(); }, 12000);
    try {
      let response;
      try { response = await fetch(path, { method: "GET", headers: token ? { "Authorization": "Bearer " + token, "Accept": "application/json" } : { "Accept": "application/json" }, cache: "no-store", credentials: "omit", redirect: "error", signal: abort.signal }); }
      // A connection failure gets the same caregiver wording as HTTP errors; aborts propagate.
      catch (error) { if (error && error.name === "AbortError") throw error; throw new Error("The backend could not be reached. No current data was confirmed."); }
      if (generation !== epoch) throw new Error("Cancelled");
      if (!response.ok) {
        // Say how long the backend asked to wait (Retry-After); nothing retries automatically.
        const wait = core.retryAfterLabel(response.headers.get("retry-after")), advice = wait ? " The backend asked to wait " + wait + " before trying again; no automatic retry occurs." : "";
        if (response.status === 429) throw new Error("Too many requests or sign-in attempts (HTTP 429). No current data was confirmed." + (advice || " Wait before trying again; no automatic retry occurs."));
        throw new Error((response.status === 401 ? "Access was refused. Check the caregiver token, then connect again." : response.status === 403 ? "Backend denied this request. Check the caregiver access configuration." : "Backend request failed (HTTP " + response.status + "). No current data was confirmed.") + advice);
      }
      if (!(response.headers.get("content-type") || "").includes("application/json")) throw new Error("Backend returned an unsupported response.");
      const reader = response.body.getReader(); const chunks = []; let bytes = 0;
      while (true) { const part = await reader.read(); if (part.done) break; bytes += part.value.byteLength; if (bytes > 262144) { await reader.cancel(); throw new Error("Backend response exceeded the dashboard limit."); } chunks.push(part.value); }
      const body = new Uint8Array(bytes); let offset = 0; for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
      if (generation !== epoch) throw new Error("Cancelled");
      return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
    } finally { clearTimeout(timer); }
  }
  async function operation(work) {
    if (busy) return;
    const epoch = ++generation; if (controller) controller.abort(); controller = new AbortController(); setBusy(true);
    try {
      await work(epoch);
      // Watch changes and pagers announce nothing on success; never leave an
      // earlier failure on screen as if it described the records now shown.
      if (epoch === generation && errorShown) say("Request completed. The view shows the records loaded so far; this does not confirm a current watch connection.");
    }
    catch (error) { if (epoch === generation) say(error.name === "AbortError" ? (mode === "backend" ? "Request timed out or was cancelled. Refresh explicitly to try again; no automatic retry occurs." : "Request timed out or was cancelled. Connect again to retry; no automatic retry occurs.") : error.message || "Data could not be loaded.", true); }
    finally { if (epoch === generation) setBusy(false); }
  }
  async function readHealth(epoch) {
    const health = await request("/v1/health", epoch);
    if (!health || health.schema_version !== 1 || typeof health.synthetic_only !== "boolean" || health.status !== "ready") throw new Error("Backend contract is not supported.");
    return health;
  }
  function renderDevices() {
    ui.deviceSelect.replaceChildren();
    if (!devices.length) { const option = document.createElement("option"); option.value = ""; option.textContent = "No watch data loaded"; ui.deviceSelect.append(option); }
    for (const device of devices) { const option = document.createElement("option"); option.value = device.device_id; option.textContent = (device.latest ? device.latest.model + " · " : "Watch · ") + device.device_id; ui.deviceSelect.append(option); }
    ui.deviceSelect.value = selectedId;
  }
  function node(tag, text, className) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n; }
  function svgNode(tag, attrs, text) { const n = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const [key, value] of Object.entries(attrs)) n.setAttribute(key, String(value)); if (text !== undefined) n.textContent = text; return n; }
  function drawChart(filtered) {
    const usable = filtered.filter(s => s.heart_rate_bpm !== null).slice().reverse();
    ui.chartEmpty.hidden = usable.length > 0; ui.chartWrap.hidden = usable.length === 0;
    ui.historyChart.replaceChildren(svgNode("title", { id: "chartTitle" }, "Saved heart-rate observations"), svgNode("desc", { id: "chartDescription" }, "Individual watch-reported observations by phone receipt time. Null values are not plotted. Equivalent values are listed in the table."));
    if (!usable.length) {
      ui.chartEmpty.querySelector("h3").textContent = filtered.length ? "No usable heart-rate value in this view" : "No readings in this view";
      ui.chartEmpty.querySelector("p").textContent = mode === "none" ? "Connect a data source or use the clearly labeled synthetic preview." : (!currentDevice() ? "No watch records are available from this backend yet." : sampleCursor ? "Try another time range or load older observations." : "Try another time range.") + " Missing readings are not a health assessment."; return;
    }
    const dates = usable.map(s => Date.parse(s.received_at)); const rates = usable.map(s => s.heart_rate_bpm);
    const lo = Math.max(0, Math.floor((Math.min(...rates) - 5) / 5) * 5), hi = Math.ceil((Math.max(...rates) + 5) / 5) * 5;
    const first = Math.min(...dates), last = Math.max(...dates), span = Math.max(1, last - first);
    for (let i = 0; i < 4; i++) { const y = 20 + i * 52; const value = hi - (hi - lo) * i / 3;
      ui.historyChart.append(svgNode("line", { x1: 55, x2: 775, y1: y, y2: y, stroke: "#394257", "stroke-width": 1 }), svgNode("text", { x: 43, y: y + 5, fill: "#b7bfd1", "font-size": 12, "text-anchor": "end" }, Math.round(value)));
    }
    for (let i = 0; i < usable.length; i++) { const s = usable[i], x = first === last ? 415 : 55 + (dates[i] - first) / span * 720, y = 20 + (hi - rates[i]) / (hi - lo) * 156;
      const dot = svgNode("circle", { cx: x, cy: y, r: 3.5, fill: "#ffa1ac", opacity: .85 }); dot.append(svgNode("title", {}, s.heart_rate_bpm + " bpm · " + localTime(s.received_at) + " · " + s.sample_id)); ui.historyChart.append(dot);
    }
    ui.historyChart.append(svgNode("text", { x: 55, y: 209, fill: "#b7bfd1", "font-size": 12 }, timeFormat.format(first)), svgNode("text", { x: 775, y: 209, fill: "#b7bfd1", "font-size": 12, "text-anchor": "end" }, timeFormat.format(last)));
  }
  function render() {
    const device = currentDevice(); const latest = device && device.latest; const filtered = core.filterRows(rows, ui.rangeSelect.value);
    ui.latestBpm.textContent = latest && latest.heart_rate_bpm !== null ? latest.heart_rate_bpm : "—";
    // Backend records are labeled synthetic wherever shown, as the table rows are.
    ui.latestLabel.textContent = latest ? (mode === "demo" ? "Synthetic preview observation" : latest.synthetic ? (latest.heart_rate_bpm === null ? "Synthetic test record has no usable HR" : "Synthetic test record - not watch-reported") : latest.heart_rate_bpm === null ? "Last saved observation has no usable HR" : "Last saved observation - not live") : "No reading loaded";
    ui.latestTime.textContent = latest ? "Phone receipt: " + localTime(latest.received_at) : "Receipt time: unavailable";
    ui.sampleCount.textContent = device ? device.sample_count.toLocaleString() : "—";
    ui.countNote.textContent = device ? (mode === "demo" ? "Fictional preview records only" : "Backend total for this selected source") : "No source selected";
    ui.receiptAge.textContent = core.ageLabel(latest ? latest.received_at : null);
    ui.receiptNote.textContent = latest ? (mode === "demo" ? "Synthetic timing, not your watch" : "Current connection is unknown") : "No source selected";
    ui.statusReportTime.textContent = gateway ? (gateway.synthetic ? "Synthetic test report, not from a real gateway. " : "") + "Last report received by backend: " + localTime(gateway.server_received_at) + " (" + core.ageLabel(gateway.server_received_at) + "). Gateway clock: " + localTime(gateway.reported_at) : mode === "backend" && selectedId && !statusLoaded ? "Gateway status for this watch is not loaded." : "No gateway status report received.";
    const connectionNames = { connected: "Connected when reported", disconnected: "Disconnected when reported", connecting: "Connecting when reported", error: "Error when reported" };
    ui.watchLink.textContent = gateway ? connectionNames[gateway.connection] : "Not reported";
    ui.watchLinkNote.textContent = gateway ? "Saved gateway report only. The watch may have changed state since then." : "Receiving saved data does not establish a current Bluetooth connection.";
    ui.watchBattery.textContent = gateway && gateway.battery_percent !== null ? gateway.battery_percent + "% - last reported" : "Not reported";
    ui.watchBatteryNote.textContent = "Battery age and current charge are not inferred from heart-rate records.";
    ui.phoneQueue.textContent = gateway ? gateway.pending_samples + " pending / " + gateway.uploaded_samples + " uploaded" : "Not reported";
    ui.phoneQueueNote.textContent = gateway ? "At report time, local recording was " + (gateway.recording_enabled ? "enabled." : "off.") : "Pending records on the phone cannot be inferred from backend totals.";
    ui.loadedStatus.textContent = filtered.length + " shown · " + rows.length + " loaded" + (device ? " · " + device.sample_count + " in source" : "") + " · Times displayed in your browser’s local timezone";
    ui.pageNote.textContent = rows.length >= core.MAX_SAMPLES ? "1,000-observation limit reached. Start a new refresh to reset this view." : sampleCursor ? "More observations are available. Load them explicitly." : mode === "none" ? "No observations loaded. Up to 1,000 observations per dashboard session." : mode === "backend" && selectedId && !samplesLoaded ? "Observations for this watch are not loaded." : "End of the currently available page sequence. Refresh to check for new data.";
    drawChart(filtered); ui.historyRows.replaceChildren();
    if (!filtered.length) { const tr = node("tr"), td = node("td", "No observations in this view.", "table-empty"); td.colSpan = 4; tr.append(td); ui.historyRows.append(tr); }
    for (const row of filtered) { const tr = node("tr"); const time = node("td", localTime(row.received_at)); time.title = row.received_at;
      const value = row.heart_rate_bpm === null ? "Unavailable · " + row.missing_reason.replaceAll("_", " ") : row.heart_rate_bpm + " bpm";
      const id = node("td", row.sample_id.length > 18 ? row.sample_id.slice(0, 14) + "…" : row.sample_id); id.title = row.sample_id;
      tr.append(time, node("td", value), node("td", row.model + " · " + (row.synthetic ? "synthetic" : "watch-reported") + " · " + row.source), id); ui.historyRows.append(tr);
    }
    setBusy(busy);
  }
  async function loadSamplePage(epoch, reset) {
    if (reset) { rows = []; sampleCursor = null; samplesLoaded = false; seenSampleCursors.clear(); render(); }
    const parameters = new URLSearchParams({ device_id: selectedId, limit: String(Math.min(50, core.MAX_SAMPLES - rows.length)), order: "desc" });
    if (sampleCursor) parameters.set("cursor", sampleCursor);
    const page = core.samplePage(await request("/v1/samples?" + parameters, epoch), selectedId);
    if (syntheticOnly && page.samples.some(s => !s.synthetic)) throw new Error("Backend returned real data in synthetic-only mode; page rejected.");
    if (page.next_cursor && seenSampleCursors.has(page.next_cursor)) throw new Error("Backend repeated a page cursor; loading stopped.");
    rows = core.appendUnique(rows, page.samples); sampleCursor = page.next_cursor; samplesLoaded = true;
    if (sampleCursor) seenSampleCursors.add(sampleCursor); render();
  }
  async function loadDevices(epoch, reset) {
    const parameters = new URLSearchParams({ limit: "50" }); if (!reset && deviceCursor) parameters.set("cursor", deviceCursor);
    const page = core.devicePage(await request("/v1/devices?" + parameters, epoch));
    if (syntheticOnly && page.devices.some(d => d.latest && !d.latest.synthetic)) throw new Error("Backend returned real data in synthetic-only mode; page rejected.");
    if (page.next_cursor && seenDeviceCursors.has(page.next_cursor)) throw new Error("Backend repeated a device cursor; loading stopped.");
    const next = reset ? [] : devices.slice(); const known = new Set(next.map(d => d.device_id));
    for (const device of page.devices) { if (known.has(device.device_id)) throw new Error("Backend repeated a device; refresh the source."); next.push(device); known.add(device.device_id); }
    if (next.length > core.MAX_DEVICES) throw new Error("Dashboard device limit reached.");
    devices = next; deviceCursor = page.next_cursor; if (deviceCursor) seenDeviceCursors.add(deviceCursor);
    if (!devices.some(d => d.device_id === selectedId)) {
      selectedId = devices.length ? devices[0].device_id : "";
      // Never display the previous watch's readings or status under another watch.
      rows = []; gateway = null; sampleCursor = null; statusLoaded = false; samplesLoaded = false; seenSampleCursors.clear();
    }
    renderDevices(); render();
  }
  async function loadGatewayStatus(epoch) {
    gateway = null; statusLoaded = false; render();
    const response = await request("/v1/gateway-status?" + new URLSearchParams({ device_id: selectedId }), epoch);
    gateway = core.gatewayStatus(response, selectedId);
    if (syntheticOnly && gateway && !gateway.synthetic) { gateway = null; throw new Error("Backend returned real status in synthetic-only mode; report rejected."); }
    statusLoaded = true; render();
  }
  ui.connectForm.addEventListener("submit", event => {
    event.preventDefault(); if (busy) return; const entered = ui.accessToken.value;
    // Connect empties the token field, so a second activation (a slow double-click or a second Enter) must not
    // wipe the session and retry without the token.
    if (!entered && token && mode === "backend") { say("Already connected with the token entered earlier. Use Refresh to check for new records, or Clear session before connecting with another token."); return; }
    clearSession(false); if (entered && !/^[A-Za-z0-9._~+/=-]{32,4096}$/.test(entered)) { say("Token format is invalid. No request was made.", true); return; } token = entered;
    operation(async epoch => { const health = await readHealth(epoch);
      syntheticOnly = health.synthetic_only; mode = "backend";
      ui.modeBadge.textContent = syntheticOnly ? "Backend · synthetic only" : "Private backend data"; ui.modeBadge.className = "badge backend";
      // Until every record request succeeds, say what is actually known.
      ui.sourceStatus.textContent = "Backend reached: " + localTime(new Date().toISOString()) + " · Records not fully loaded";
      await loadDevices(epoch, true); if (selectedId) { await loadSamplePage(epoch, true); await loadGatewayStatus(epoch); }
      ui.sourceStatus.textContent = "Backend checked: " + localTime(new Date().toISOString()) + " · No automatic polling";
      say(devices.length ? "Backend records loaded. This does not confirm a current watch connection. Refresh manually to check for new records." : "Backend is available, but no permitted watch records have arrived. Your phone's locally saved readings are not automatically on this backend.");
    });
  });
  ui.demoButton.addEventListener("click", () => {
    clearSession(false); const demo = core.demoData(); mode = "demo"; devices = demo.devices; rows = demo.samples; selectedId = devices[0].device_id;
    ui.modeBadge.textContent = "Synthetic preview"; ui.modeBadge.className = "badge demo";
    ui.sourceStatus.textContent = "Preview generated locally · No backend request or real watch data";
    renderDevices(); render(); say("SYNTHETIC PREVIEW — every reading here is fictional. No request was sent and nothing was saved.");
  });
  ui.deviceSelect.addEventListener("change", () => { if (busy) { ui.deviceSelect.value = selectedId; return; } selectedId = ui.deviceSelect.value; rows = []; gateway = null; sampleCursor = null; statusLoaded = false; samplesLoaded = false; render(); if (mode === "backend") operation(async epoch => { await loadSamplePage(epoch, true); await loadGatewayStatus(epoch); }); });
  ui.refreshButton.addEventListener("click", () => {
    if (mode === "demo") { ui.demoButton.click(); return; }
    if (mode !== "backend") return;
    operation(async epoch => { const previous = selectedId; gateway = null; statusLoaded = false; render();
      // Re-check the data mode first: records are never shown under a mode other than the one this session connected with.
      const health = await readHealth(epoch);
      if (health.synthetic_only !== syntheticOnly) { clearSession(false); say("The backend's data mode changed since you connected: it now " + (health.synthetic_only ? "accepts synthetic test data only" : "accepts real watch data") + ". Loaded records were removed from this page. Connect again to continue.", true); return; }
      seenDeviceCursors.clear(); await loadDevices(epoch, true); const moved = previous && selectedId !== previous; if (selectedId) { await loadSamplePage(epoch, true); await loadGatewayStatus(epoch); } ui.sourceStatus.textContent = "Backend checked: " + localTime(new Date().toISOString()) + " · No automatic polling"; say("Refreshed saved backend records and any gateway status report. Reported status is not proof of a current connection." + (moved ? (deviceCursor ? " The previously selected watch is not on the first page of sources, so another watch is shown; use Load more devices to find it." : " The previously selected watch is no longer listed, so another watch is shown.") : "")); });
  });
  ui.moreDevices.addEventListener("click", () => { if (mode === "backend" && deviceCursor) operation(epoch => loadDevices(epoch, false)); });
  ui.loadMore.addEventListener("click", () => { if (mode === "backend" && sampleCursor && rows.length < core.MAX_SAMPLES) operation(epoch => loadSamplePage(epoch, false)); });
  ui.rangeSelect.addEventListener("change", render); ui.clearSession.addEventListener("click", () => clearSession());
  window.addEventListener("pagehide", () => clearSession(false));
  clearSession(false);
}());
