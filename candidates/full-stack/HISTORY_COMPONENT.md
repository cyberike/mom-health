# History component — build-only candidate

## Expanded inactive transfer implementation

`HistoryTransferPlan` now implements review-only START/CONTINUE command proposals, unique command tokens, successful-write gating, 50-notification read windows, explicit terminal ambiguity, cancellation/disconnect/failure, monotonic deadline handling and bounded partial results. Limits are application safety choices: 4 windows, 500 records, 120 seconds overall and 10 seconds idle. No BLE object, thread, timer, service or transport invocation exists. The caller must feed clock/events explicitly. Command bytes are defensive copies; stale confirmations and late terminal-state notifications cannot advance the plan. Foreign session/device events reject without changing state. There is no automatic retry or reconnect. Cancellation issues no invented watch stop command.

`tests/history/HistoryTransferPlanTest.java` prepares cases for window transitions, immutable commands, stale confirmations, terminal ambiguity, late data, deadlines, failed writes, unconfirmed notification ordering, cancel, foreign devices, disconnect and bounded read windows. **Not executed.**

Additional read-only specification audit: V8 `V8 SDK/V8 SDK/V8 IOS/V8 IOS/IOS SDK说明文档.docx.txt:219–232` documents HR mode 0 latest/start and mode 2 next after up to 50 sets; date filtering only works for an exact existing stored date. V8 Android `HeartRateInfoActivity.java:73–89` counts 50 callbacks before continuation, as does the static example. The inactive planner follows the Android callback example, rather than silently equating callbacks with records. Parsed V1/V4 `.doc.strings.txt` history sections expose API examples but no exact terminal byte specification. No new authoritative terminal framing was located in this audit.

### Specification gaps versus validation still pending

**Missing exact specification:** short-packet versus end-marker discrimination; whether trailing FF is appended metadata or a sentinel in a record; fragmented notifications; opaque bytes 1–2/record identity; dynamic sample spacing; timezone and clock provenance. These stop a truthful *completed* retrieval claim, not code preparation. The inactive planner models SDK-end evidence as `TERMINAL_AMBIGUOUS`, retaining already decoded partial results and invalidating further commands. It does not silently import a potentially partial last packet.

**Known enough to build, hardware validation pending:** read opcodes, mode bytes, additive checksum, static/dynamic layouts, strict date/range validation and Android demo continuation ordering are implemented. Model-specific real firmware acceptance, connection ordering, packet captures, exact-date cursors, device time accuracy and transfer reliability remain untested. Hardware absence alone was not used to omit these code paths. Capability dispatch gates stay disabled until separately authorized acceptance; the planner itself is available for offline review in every model.

New original Java source is staged under `src/org/momhealth/gateway/history/`. No vendor source bodies or binaries are included. No tests, application runtime, BLE commands or vendor code were executed for this component. Source review is not hardware validation.

## Public API and availability

- `HistoryCapabilityRegistry.forModel("V1"|"V4"|"V8")`: immutable capability/evidence fields, including `liveAvailable`, `historyAvailable`, `liveEvidence`, `historyReason`. History is disabled for all models. V4 live is disabled. V1 live availability means the existing source-based adapter exists, not that V1 hardware was tested. V8 live transport has user evidence; clinical accuracy does not.
- `HrHistoryCodec.decodeRecords(model, Kind.STATIC|DYNAMIC, bytes)`: strict, bounded offline record decoding. Immutable records carry watch-local block time and nullable HR values with missing reasons. Zero is no result; values outside the candidate contract's20–250 range are unknown. No timezone or per-element dynamic time is invented. `Page.completion` always remains `UNKNOWN`.
- `commandForReview(model, kind, ReadMode.START|CONTINUE)`:16-byte read-only opcode fixture, no device dispatch, arbitrary modes, deletion or OTA; an overload encodes an exact previously observed watch-local date cursor. The explicit name limits intended use to offline review; do not wire these bytes into BLE without separate acceptance.
- `HistoryReviewSession`: in-memory offline inspection, max50 pages/500 records/480 bytes per input. Device/session mismatch is rejected. Atomic page acceptance, close invalidates and clears records, immutable snapshots. Repeated records remain separate because timestamp/HR equality does not establish sample identity. There is no persistence/upload/automatic pagination in this review container; the separate inactive transfer planner proposes continuation.
- `Capability.requireHardwareHistory()` always throws. There is deliberately no runtime bypass switch.

## Source evidence

Paths below are relative to workspace `sdk_inspection/`. All were read as data only.

V8 root: `V8 SDK/V8 SDK/V8 Android 2436_v8_version2.0(1)/v8test/`.

- `blesdk_2301/src/main/java/com/jstyle/blesdkv8/constant/DeviceConst.java:24–25`: dynamic wire opcode0x54 and static0x55. `BleConst` callback string identifiers are not wire opcodes.
- `blesdk_2301/src/main/java/com/jstyle/blesdkv8/other/ResolveUtil.java:841–917`:24-byte dynamic block, BCD-rendered local date bytes3–8 and15 HR bytes9–23;10-byte static record with HR byte9. No sample interval/timezone is established here.
- `blesdk_2301/src/main/java/com/jstyle/blesdkv8/Util/BleSDK.java:827–849`: mode byte1, empty date leaves zero filter,16-byte commands. Lines903–908 additive checksum. Lines912–933 optional date filtering is implemented as a review-only exact stored date overload.
- `app/src/main/java/com/jstyle/test2025/activity/HeartRateStaticInfoActivity.java:53–96`: start read and continuation after50 callbacks, subject to SDK end flag. Start/continue modes0/2 appear in this demo's declarations. Delete mode is intentionally omitted from our API.

V1 root: `2208A(V1) SDK/2208A（V1） SDK/SDK - Android_2208a_v6/2208aSdk/2208atest/`.

- `blesdk_2208/src/main/java/com/jstyle/blesdk2208a/constant/DeviceConst.java:35,37`: same HR history opcodes.
- `blesdk_2208/src/main/java/com/jstyle/blesdk2208a/Util/ResolveUtil.java:762–835`: same24/10-byte field layouts and ambiguous end heuristic.
- `blesdk_2208/src/main/java/com/jstyle/blesdk2208a/Util/BleSDK.java:1655–1676`: history command construction.
- `app/src/main/java/com/jstyle/test2208/activity/HeartRateStaticInfoActivity.java:53–96`: start/continuation example.

V4 root: `2501 (v4) SDK/2501 (v4)/V4 SDK(Android)/2501test/`.

- `blesdk_2208/src/main/java/com/jstyle/blesdk2501/constant/DeviceConst.java:35,37`: same HR history opcodes.
- `blesdk_2208/src/main/java/com/jstyle/blesdk2501/Util/ResolveUtil.java:759–832`: same24/10-byte field layouts and ambiguous end heuristic.
- `blesdk_2208/src/main/java/com/jstyle/blesdk2501/Util/BleSDK.java:1677–1698`: command construction.
- `app/src/main/java/com/jstyle/test2501newz/activity/HeartRateStaticInfoActivity.java:57–100`: start/continuation example. This evidence does not enable V4 hardware.

## Precise blockers and conservative parsing

All three source parsers consider any packet shorter than a record an end condition; they also inspect the final byte forFF while dividing total length by record width and ignoring a remainder. This does not distinguish truncation, an isolated terminator, an appended terminator, or an HR/sentinel field. No captured history fixture establishes exact terminal framing, sequence bytes1–2, integrity checking, continuation semantics, packet fragmentation or device timezone. We therefore reject short/remainder/trailingFF frames, never mark a completed sync, and never issue history requests. Nonzero unknown header fields are not interpreted. Each accepted record must repeat the expected opcode; variants need evidence before support is widened.

Dynamic15-value arrays remain blocks: no synthetic15 individual measured timestamps. Device-local dates use strict BCD/calendar validation; normalizing to `measured_at` requires independently confirmed timezone/clock accuracy. Receipt time must never substitute for measurement time. The data contract's timezone-aware `measured_at` remains null unless that evidence is later supplied. Unsupported BP/glucose/sleep/other measurements are not emitted.

This is a complete conservative offline codec/review component, **not a completed watch-history acquisition feature**. Activation is externally blocked by per-model captured format/end/pagination evidence and user authorization for device operations. No denial occurred during this component's file work.

## Prepared acceptance (not executed)

`tests/history/HistoryComponentTest.java` is a dependency-free Java main test source, prepared for later approval. It covers each model/kind, command modes/checksum, immutable result structure, zero/out-of-range values, invalid BCD/date, short/oversized/wrong-opcode/trailingFF packets, disabled gates, foreign device/session, bounded pages and closed sessions. Compile with Java8 alongside history source; execute only after runtime/testing authorization. Synthetic fixtures do not establish firmware compatibility.

Later hardware acceptance must be separate for each V1/V4/V8: explicit selected device/session, known stored HR history, read-only start and bounded continuation, capture packet lengths/termination without deleting records, validate watch-local time against a known clock, repeated transfer identity and disconnect/late packet exclusion. Do not broaden history dispatch merely because live HR works. Estimate20–30 minutes per available model after a safe exact terminal specification is established; unavailable hardware/time semantics remain blockers.


