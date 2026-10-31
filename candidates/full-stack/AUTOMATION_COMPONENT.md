# Caregiver-enabled automation policy engine

Original pure Java source: `src/org/momhealth/gateway/automation/AutomationConfig.java` and `AutomationEngine.java`. This is an event-driven scheduling/state component, not an Android service, timer, transport or network client. Constructing/testing it performs no device or network work. The Android service supplies events and executes actions.

## Explicit configuration and persistence

`AutomationConfig(deviceId, model, intervalMs, acquisitionConsent, recordingConsent, uploadConsent, maxSessionsPerHour, maxConnectAttempts, uploadIntervalMs)` requires every value. There is no default cadence or implied consent. Only V1/V8 are accepted; V4 remains unavailable. Device identity is an opaque UUID, not a BLE address.

- Acquisition interval: 46 seconds to 24 hours. The minimum accommodates a 30-second request and 16-second cleanup budget; it is an engineering limit, not a recommended health schedule.
- Session budget: 1–60 starts per rolling hour. Missing intervals are not fabricated.
- Connection budget: 1–6 attempts per failed sequence; 2/4/8/16/32/60-second bounded backoff and a 20-second individual timeout. Retry exhaustion requires caregiver resume or idle availability recovery.
- Upload interval: explicitly 1 minute to 24 hours, independent upload consent. One bounded attempt at a time; no immediate retry following failure; network loss cancels in-flight work.
- `toMap()` and `fromMap()` provide strict versioned serialization. Exact keys, all fields and explicit booleans are required. The caller persists only after caregiver consent. No credentials or health records are serialized here.

## Integration API

Create `AutomationEngine`; call `environment(radioAvailable, networkAvailable, powerAllowed, elapsedRealtime)` and `enable(config, elapsedRealtime)` under the corresponding caregiver action. Initial availability is false; initial state is DISABLED. `tick(elapsedRealtime)` advances deadlines; clocks must be monotonic/nonnegative.

Restore persisted cadence/hourly-budget waiting with `deferAcquisitionUntil(deadlineMs, now)`, after enable and before environment availability. This only raises the next acquisition time, never moves it earlier, emits no action and does not change active measurement/cleanup deadlines or independent uploads. Deferral must be between now and 24 hours ahead. Disabled/revoked engines ignore it. Async restore paths should use `deferAcquisitionUntil(expectedGeneration, deadlineMs, now)` so stale restoration cannot alter a newly enabled generation. Use this API rather than falsely marking power unavailable while waiting for a durable acquisition budget.

After **every event**, drain `drainActions()`. Immutable actions expose `type`, `generation`, `operationId`, `deadlineMs`. Types: CONNECT, START_MEASUREMENT, STOP_MEASUREMENT, DISCONNECT, UPLOAD_BATCH, CANCEL_UPLOAD. The queue is bounded; failure to drain is caller misuse.

Return results with the identifiers captured from each action:

- `connectionResult(generation, operationId, success, now)`
- `measurementStarted(generation, operationId, now)` after the controller confirms the start write, not upon requesting it.
- `measurementFinished(generation, operationId, cleanupConfirmed, now)` after controller cleanup. A returned HR alone does not finish a session. Controller-confirmed command delivery is not physical sensor-shutdown proof.
- `connectionLost(generation, now)`
- `uploadFinished(generation, operationId, success, now)`
- `acceptsObservation(generation, operationId, arrivalMs)` gates recording using confirmed current session, original receipt time, recording/acquisition consent and the deadline. Do not relabel a delayed observation as current.

START has a 30-second maximum from dispatch and a 5-second confirmation deadline. At the relevant deadline the engine proposes STOP, rejects further observations and allows at most 16 seconds for cleanup. Repeated ticks cannot extend deadlines or repeat STOP. Unconfirmed cleanup enters ATTENTION and disconnects; no silent next acquisition.

`suspend(now)`, `resume(now)` and `revoke(now)` are explicit caregiver controls. Revocation immediately invalidates old observation callbacks and clears the engine configuration; caller must also clear persisted consent. Cancellation drops queued start/connect work and emits bounded teardown for possibly dispatched work. Cleanup actions intentionally may carry the **previous generation**: execute STOP/DISCONNECT/CANCEL_UPLOAD teardown even though callbacks/observations from that generation are invalid. New work is quarantined for 16 seconds after possibly active connection work is cancelled.

Radio, power allowance or link loss during acquisition latches ATTENTION because shutdown is unconfirmed. Radio restoration alone does not clear this latch; explicit caregiver resume/enable is required. This is technical recovery, not a clinical alert or a promise of uninterrupted monitoring. Idle link loss can recover with bounded backoff when measurement is next due. Mom should not be asked to operate caregiver controls.

## Verification and limits

`tests/automation/AutomationEngineTests.java` covers configuration, start confirmation, cutoff, stop ordering, cleanup timeout, stale operation/generation exclusion, independent consent combinations, hourly budgets, retry limits, interruptions, cancellation, upload scheduling and explicit resume using synthetic events/clocks. No Android runtime, phone, watch, real health data, service or network is involved. See the parent build report for the final assertion count and log path.

Android lifecycle/foreground-service behavior, durable consent/reboot handling, notification permission, battery behavior, transport and physical shutdown, HTTP cancellation and caregiver status require separate runtime acceptance. No clinical cadence is chosen. No dependency, vendor binary, V4 activation or device permission is added by this component.
