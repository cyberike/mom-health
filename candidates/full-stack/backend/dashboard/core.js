(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.MomHealthDashboard = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const MAX_SAMPLES = 1000;
  const MAX_DEVICES = 200;
  const missingReasons = new Set(["no_result", "unsupported", "not_requested", "unknown"]);
  function boundedString(value, name, maximum) {
    if (typeof value !== "string" || !value.length || value.length > maximum) throw new Error("Invalid " + name);
    return value;
  }
  function timestamp(value, nullable) {
    if (nullable && value === null) return null;
    boundedString(value, "timestamp", 64);
    if (!/(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) throw new Error("Invalid timestamp");
    return value;
  }
  function sample(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid observation");
    const bpm = value.heart_rate_bpm;
    if (bpm !== null && (!Number.isInteger(bpm) || bpm < 20 || bpm > 250)) throw new Error("Unsupported heart-rate value");
    if (bpm === null && !missingReasons.has(value.missing_reason)) throw new Error("Missing observation reason");
    if (bpm !== null && value.missing_reason !== null) throw new Error("Contradictory observation");
    if (!["V1", "V4", "V8"].includes(value.model) || !["live", "history"].includes(value.source) || typeof value.synthetic !== "boolean") throw new Error("Invalid provenance");
    // Match the backend evidence gate: unsupported live models and historical
    // values must not become real readings merely because a server sent them.
    if (!value.synthetic && bpm !== null && (value.source !== "live" || !["V1", "V8"].includes(value.model))) throw new Error("Unverified real heart-rate provenance");
    return Object.freeze({
      sample_id: boundedString(value.sample_id, "observation identifier", 64),
      device_id: boundedString(value.device_id, "device identifier", 64),
      session_id: boundedString(value.session_id, "session identifier", 64),
      model: value.model, source: value.source, synthetic: value.synthetic,
      received_at: timestamp(value.received_at, false), measured_at: timestamp(value.measured_at, true),
      heart_rate_bpm: bpm, missing_reason: value.missing_reason
    });
  }
  function cursor(value) {
    if (value === null) return null;
    return boundedString(value, "cursor", 256);
  }
  function samplePage(value, deviceId) {
    if (!value || !Array.isArray(value.samples) || value.samples.length > 100) throw new Error("Invalid observation page");
    const samples = value.samples.map(sample);
    if (samples.some(s => s.device_id !== deviceId)) throw new Error("Observation belongs to another watch");
    return { samples, next_cursor: cursor(value.next_cursor) };
  }
  function devicePage(value) {
    if (!value || !Array.isArray(value.devices) || value.devices.length > 100) throw new Error("Invalid device page");
    const devices = value.devices.map(d => {
      const id = boundedString(d.device_id, "device identifier", 64);
      if (!Number.isSafeInteger(d.sample_count) || d.sample_count < 0) throw new Error("Invalid observation count");
      const latest = d.latest === null ? null : sample(d.latest);
      if (latest && latest.device_id !== id) throw new Error("Mismatched device summary");
      return Object.freeze({ device_id: id, sample_count: d.sample_count, latest });
    });
    return { devices, next_cursor: cursor(value.next_cursor) };
  }
  function appendUnique(existing, incoming, limit = MAX_SAMPLES) {
    const result = existing.slice();
    const known = new Map(existing.map(s => [s.sample_id, JSON.stringify(s)]));
    for (const row of incoming) {
      if (known.has(row.sample_id)) {
        if (known.get(row.sample_id) !== JSON.stringify(row)) throw new Error("Observation ID content conflict");
        continue;
      }
      if (result.length >= limit) throw new Error("Dashboard observation limit reached");
      known.set(row.sample_id, JSON.stringify(row)); result.push(row);
    }
    return result;
  }
  function gatewayStatus(value, deviceId) {
    if (!value || typeof value !== "object") throw new Error("Invalid gateway response");
    if (value.status === null) {
      if (value.server_received_at !== null) throw new Error("Contradictory gateway response");
      return null;
    }
    const s = value.status;
    if (!s || s.schema_version !== 1 || s.device_id !== deviceId || !["V1", "V4", "V8"].includes(s.model) || !["connecting", "connected", "disconnected", "error"].includes(s.connection)) throw new Error("Invalid gateway report");
    if (typeof s.synthetic !== "boolean" || typeof s.recording_enabled !== "boolean") throw new Error("Invalid gateway flags");
    if (s.battery_percent !== null && (!Number.isInteger(s.battery_percent) || s.battery_percent < 0 || s.battery_percent > 100)) throw new Error("Invalid battery report");
    for (const n of [s.pending_samples, s.uploaded_samples]) if (!Number.isSafeInteger(n) || n < 0) throw new Error("Invalid queue report");
    return Object.freeze({
      device_id: deviceId, model: s.model, connection: s.connection, battery_percent: s.battery_percent,
      pending_samples: s.pending_samples, uploaded_samples: s.uploaded_samples,
      recording_enabled: s.recording_enabled, synthetic: s.synthetic,
      reported_at: timestamp(s.reported_at, false), last_observation_at: timestamp(s.last_observation_at, true),
      server_received_at: timestamp(value.server_received_at, false)
    });
  }
  function filterRows(rows, range, now = Date.now()) {
    const span = range === "24h" ? 86400000 : range === "7d" ? 604800000 : null;
    if (!["all", "24h", "7d"].includes(range)) throw new Error("Unknown time range");
    return rows.filter(r => span === null || (Date.parse(r.received_at) >= now - span && Date.parse(r.received_at) <= now))
      .sort((a, b) => Date.parse(b.received_at) - Date.parse(a.received_at) || a.sample_id.localeCompare(b.sample_id));
  }
  function ageLabel(iso, now = Date.now()) {
    if (iso === null) return "Unknown";
    const age = now - Date.parse(iso);
    if (!Number.isFinite(age)) return "Unknown";
    if (age < -60000) return "Clock ahead";
    if (age < 60000) return "Under a minute";
    if (age < 3600000) return Math.floor(age / 60000) + " min ago";
    if (age < 86400000) return Math.floor(age / 3600000) + " hr ago";
    return Math.floor(age / 86400000) + " days ago";
  }
  // Retry-After is delta-seconds or an HTTP date (RFC 9110). Returns a wait such
  // as "about 45 seconds", or "" when the header is absent or unusable.
  function retryAfterLabel(value, now = Date.now()) {
    const text = typeof value === "string" ? value.trim() : "";
    if (!text) return "";
    // Only the IMF-fixdate form servers must send is read as a date, and only when it
    // names a real moment: V8's Date.parse silently rolls impossible fields over.
    const at = /^[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT$/.test(text) ? Date.parse(text) : NaN;
    const date = Number.isFinite(at) && new Date(at).toUTCString() === text;
    const seconds = Math.ceil(/^\d+$/.test(text) ? Number(text) : date ? (at - now) / 1000 : NaN);
    if (!Number.isFinite(seconds)) return "";
    if (seconds <= 1) return "a moment";
    if (seconds < 90) return "about " + seconds + " seconds";
    if (seconds < 5400) return "about " + Math.ceil(seconds / 60) + " minutes";
    return seconds <= 86400 ? "about " + Math.ceil(seconds / 3600) + " hours" : "more than a day";
  }
  function demoData(now = Date.now()) {
    const deviceId = "synthetic-v8-preview";
    const rates = [74, 75, 76, 74, 73, 73, 72, null, 74, 75, 74, 73, 74, 76, 75, 74, 73, 72, 74, 74, 75, 74, 73, 74];
    const samples = rates.map((bpm, index) => sample({
      sample_id: "preview-observation-" + String(index + 1).padStart(3, "0"),
      device_id: deviceId, session_id: "synthetic-preview-session", model: "V8", source: "live", synthetic: true,
      received_at: new Date(now - (rates.length - index) * 60000).toISOString(), measured_at: null,
      heart_rate_bpm: bpm, missing_reason: bpm === null ? "no_result" : null
    }));
    return { devices: [{ device_id: deviceId, sample_count: samples.length, latest: samples[samples.length - 1] }], samples };
  }
  return { MAX_SAMPLES, MAX_DEVICES, sample, samplePage, devicePage, gatewayStatus, appendUnique, filterRows, ageLabel, retryAfterLabel, demoData };
}));
