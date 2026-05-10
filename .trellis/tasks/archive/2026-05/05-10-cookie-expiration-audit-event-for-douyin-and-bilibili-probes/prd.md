# Cookie expiration audit event for Douyin and Bilibili probes

## Goal

Surface an explicit, queryable signal when the configured `ARL_DOUYIN_COOKIE` or `ARL_BILIBILI_SESSDATA` has expired, so the user can distinguish "streamer not live" from "your cookie expired and recording silently stopped." This is the logical follow-up to the strict 1080p quality gate (commit `952c22d`): the gate now rejects sub-1080p candidates, which means an expired cookie no longer silently degrades to 720p — it produces total recording silence with no actionable hint.

## What I already know

### Detection signals per platform

- **Bilibili — clean signal exists**: When `SESSDATA` expires, `api.live.bilibili.com` returns `code=-101` (账号未登录). Currently surfaced via `BilibiliRoomProbe._fetch_json` raising `ValueError(f"api_error:code=-101:...")`, caught by `detect()` and turned into `AgentSnapshot(state=OFFLINE, reason="api_error:code=-101:...")`. No special classification today.
- **Douyin — inferential signal only**: When the cookie expires, the page DOM stops embedding signed `_uhd`/`_origin` URLs and falls back to anonymous `_hd` (720p60 signed) — exactly the behavior documented in `.env.example`. Today this hits the new strict gate and surfaces as `reason="quality_below_min_tier:hd<uhd"`. Detection requires combining: (a) cookie was configured, (b) gate rejected the candidate, (c) detected tier matches the anonymous baseline.

### Existing event/log surfaces

- `windows-agent-events.jsonl` — `AgentEvent { event_type: "live_started"|"live_stopped", snapshot }`. State transitions only. Emitted from `WindowsAgentService.run_once`.
- `orchestrator-events.jsonl` — orchestrator audit log. Already used for `ignored_unknown_event_type`, `recording_retry_scheduled`, `ffmpeg_record_succeeded`, etc.
- `AgentSnapshot.reason` — free-form string already flowing through to orchestrator's snapshot payload and state file.

### Constraints from contracts

- Per `.trellis/spec/backend/orchestration-contracts.md`, any change to `event_type`, snapshot fields, or state shape requires updating the contract spec.
- Orchestrator's `process_agent_event` (orchestrator/service.py:123-133) treats any `event_type` other than `live_started`/`live_stopped` as `ignored_unknown_event_type`. Adding a new event type requires extending the orchestrator dispatch.

## Assumptions (temporary)

- The user wants this signal **per-cycle** (every probe run that detects cookie expiration), not just on the first transition. (To validate.)
- B 站 cookie-expired detection should ALSO trigger when 1080p gate rejects an authenticated stream's `current_qn=250` (i.e., the quality gate fired at the anonymous-cap qn) — same inferential pattern as 抖音, complementing the explicit `code=-101` signal.
- Detection must not produce false positives when **no cookie is configured** — cookie-expired is meaningless if the user never authenticated. Skipped silently in that case.

## Open Questions

(All resolved — see Decision section below.)

## Requirements

- Detect cookie-expired condition for both Douyin and Bilibili probes.
- Emit a new agent `event_type="cookie_expired_for_<platform>"` to `windows-agent-events.jsonl`.
- Orchestrator dispatches the new event type into `orchestrator-events.jsonl` (audit log) — must NOT be classified as `ignored_unknown_event_type`.
- **Detection rules (high-confidence only)**:
  - Bilibili: `SESSDATA` configured AND API returns `code=-101` (caught at the playinfo endpoint inside `_fetch_json`) → emit cookie-expired.
  - Douyin: cookie configured AND quality gate rejects candidate AND detected tier is exactly `hd` (anonymous baseline) → emit cookie-expired.
  - Other quality-gate failures (`sd`/`md`/`ld` tier, B 站 `qn` rejected without `-101`) are NOT cookie-expired — they pass through with their existing `quality_below_*` reason.
- **Frequency**: only emit on snapshot transitions (aligned with existing `_has_changed` dedup logic). No re-emit while snapshot reason stays the same.
- Do not produce false positives when no cookie is configured.
- Preserve existing snapshot/event semantics — additive only (the underlying snapshot still emits its existing offline reason; the cookie-expired event is supplementary).
- New CLI subcommand `arl.cli cookie-health` runs one detection cycle per configured platform and reports cookie status (configured / fresh / expired / not-configured) with a hint pointing to README's cookie-grab instructions.

## Acceptance Criteria

- [ ] B 站: when `SESSDATA` is configured and playinfo returns `code=-101`, agent emits `cookie_expired_for_bilibili` event in addition to `live_stopped`.
- [ ] B 站: when no `SESSDATA` is configured and `code=-101` is returned, NO cookie_expired event is emitted (the user never authenticated).
- [ ] 抖音: when `ARL_DOUYIN_COOKIE` is configured and quality gate rejects with `detected_tier == hd`, agent emits `cookie_expired_for_douyin` event in addition to `live_stopped`.
- [ ] 抖音: when no cookie is configured and gate rejects at `_hd` tier, NO cookie_expired event is emitted.
- [ ] 抖音: when cookie is configured and gate rejects at `_sd`/`_md`/`_ld` tier (not the anonymous baseline), NO cookie_expired event is emitted.
- [ ] Orchestrator routes `cookie_expired_for_<platform>` events into `orchestrator-events.jsonl` audit log; no `ignored_unknown_event_type` warning.
- [ ] `arl.cli cookie-health` exits 0 on fresh cookies, prints a readable status report, exits non-zero (or visible warning) when any configured cookie is detected expired.
- [ ] Unit tests cover all positive/negative paths above.
- [ ] `.trellis/spec/backend/orchestration-contracts.md` lists the new `cookie_expired_for_<platform>` event in the `event_type` registry.
- [ ] `pytest` baseline (currently 214) stays green and grows by the new tests.

## Definition of Done

- Tests added (unit; positive + negative for both platforms; CLI smoke test).
- Lint / typecheck / pytest green.
- Orchestration contract spec updated (new event type entry).
- README "B 站接入" / "抖音 cookie" notes link to the new CLI and explain the audit-log signal.
- Journal entry recording the implementation.

## Technical Approach

### Detection placement

Detection lives in a new method on each probe (or on the `PlatformProbe` base) that classifies cookie state given the just-produced snapshot:

```python
class PlatformProbe(ABC):
    def classify_cookie_state(self, snapshot: AgentSnapshot) -> CookieState:
        """Return 'fresh' | 'expired' | 'not_configured'.
        Default base implementation returns 'not_configured';
        subclasses implementing cookie-aware probes override.
        """
```

- `BilibiliRoomProbe.classify_cookie_state`: returns `"expired"` if `settings.sessdata` truthy AND snapshot.reason matches `^api_error:code=-101`. `"fresh"` if sessdata truthy and snapshot is LIVE. `"not_configured"` otherwise.
- `DouyinRoomProbe.classify_cookie_state`: returns `"expired"` if `settings.cookie` truthy AND snapshot.reason matches `^quality_below_min_tier:hd<`. `"fresh"` if cookie truthy and snapshot is LIVE. `"not_configured"` otherwise.

`WindowsAgentService.run_once` calls `classify_cookie_state(snapshot)` after each probe; if result is `"expired"` AND `_has_changed` returned True, it appends a second `AgentEvent(event_type=f"cookie_expired_for_{snapshot.platform}", snapshot=snapshot)` to the JSONL.

### Orchestrator dispatch

`process_agent_event` (orchestrator/service.py:123) gets an `elif event.event_type.startswith("cookie_expired_for_"):` branch that appends a structured record to the audit log via the existing `_log_audit_event` helper. No state-file mutation needed — this is informational.

### `cookie-health` CLI

Adds `cookie-health` subcommand to `arl.cli`. Implementation: instantiate `WindowsAgentService` (or just `build_probes`) without the polling loop; for each probe, call `detect()` once + `classify_cookie_state(snapshot)`; print a tabular report. Exit code: 0 if all cookies fresh or not_configured; 1 if any expired.

### Contract spec update

Add to `.trellis/spec/backend/orchestration-contracts.md`:

- New value enumerated for agent `event_type`: `cookie_expired_for_<platform>` (informational, no state transition).
- Note: orchestrator MUST handle this event without raising `ignored_unknown_event_type`.

## Decision (ADR-lite)

**Context**: After the strict 1080p quality gate landed (`952c22d`), an expired Douyin cookie or Bilibili `SESSDATA` causes total recording silence with no actionable hint — the gate rejects every candidate. Users cannot distinguish "streamer offline" from "your cookie expired."

**Decision**:

- Output surface: **A** (new `event_type="cookie_expired_for_<platform>"` flowing through `windows-agent-events.jsonl` → orchestrator dispatch → `orchestrator-events.jsonl` audit log).
- Detection policy: **high-confidence only** — Bilibili requires explicit `code=-101`, Douyin requires exact `_hd` anonymous-baseline match. Other gate failures are not classified as cookie-expired.
- Frequency: **state-transition only** — reuse existing `_has_changed` dedup so a persistently-expired cookie produces one event, not one per cycle.
- Plus a `cookie-health` CLI for on-demand checks.

**Consequences**:

- Zero false positives in steady-state operation, at the cost of missing detection on rare Douyin transient sub-`_hd` downgrades.
- Audit log stays clean (one event per cookie-state transition).
- Probe interface gains one method (`classify_cookie_state`); orchestrator gains one event-type branch; agent service emits one extra event per cycle when warranted.
- Future `cookie_restored` symmetric event remains a clean follow-up — same plumbing, different rule.

## Out of Scope (explicit)

- `cookie_restored` symmetric recovery event.
- B 站 status-endpoint (`get_info`) -101 handling (in practice playinfo is where -101 surfaces; status returns `live_status` regardless of auth). Will revisit if observed in production.
- Automatic cookie refresh / re-login.
- Webhook / desktop-notification / email channels — `audit log + grep` is the MVP UX.
- LoL semantic stage detection, post-processing automation, recovery service hardening — separate tasks.

## Implementation Plan (small PRs)

- **PR1 — Detection plumbing**: add `CookieState` enum + `classify_cookie_state` on `PlatformProbe` base + Bilibili/Douyin overrides. Pure additions, no behavior change. Unit tests for the classifier (8-10 cases covering fresh/expired/not_configured for both platforms). +~120 LOC, +~10 tests.
- **PR2 — Event emission + orchestrator dispatch**: extend `WindowsAgentService.run_once` to emit `cookie_expired_for_<platform>` events; extend `process_agent_event` to dispatch them into the audit log. Update `.trellis/spec/backend/orchestration-contracts.md`. Tests: agent emits expected event sequence; orchestrator does NOT log `ignored_unknown_event_type`. +~80 LOC, +~6 tests.
- **PR3 — CLI + docs**: `cookie-health` subcommand + README notes + journal. +~60 LOC, +~3 tests.

## Technical Notes

### Files inspected

- `src/arl/windows_agent/bilibili_probe.py` — `_fetch_json` raises `ValueError("api_error:code=-101:...")`, `detect()` swallows into snapshot reason.
- `src/arl/windows_agent/probe.py` (Douyin) — `_quality_gate_reason()` returns `quality_below_min_tier:<detected><configured>`; `_extract_quality_tier()` already extracts the URL tier.
- `src/arl/windows_agent/service.py` — `_event_name()` is the single place that maps snapshot → event_type today; `_has_changed()` is the dedup gate.
- `src/arl/windows_agent/models.py:52` — `AgentEvent.event_type: str` is open-ended; no enum constraint.
- `src/arl/orchestrator/service.py:123-133` — `process_agent_event` only handles `live_started`/`live_stopped`, anything else is logged as `ignored_unknown_event_type` to audit log.

### Output surface candidates (decision: A)

- **A. New `event_type`** ✅ chosen.
- **B. Specialized snapshot reason** only.
- **C. Both**.

### Research References

(none — questions resolved by code inspection)


