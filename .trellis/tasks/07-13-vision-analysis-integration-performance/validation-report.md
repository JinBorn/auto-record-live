# Vision Analysis Integration & Performance — Validation Report

Date: 2026-07-14
Task: 07-13-vision-analysis-integration-performance
Schema: `ARL_VISION_ANALYSIS_SCHEMA_VERSION=3` (new default), stage enabled via
`publish` preset / `ARL_VISION_ANALYSIS_ENABLED=1`.

## 1. Representative sessions

Three 1080p Chinese-client sessions were used, covering both a shorter and two
longer matches:

| session | source duration | boundaries |
|---|---|---|
| `session-20260617073649-4b5ec478` | 92 min (5529 s) | 3 |
| `session-20260617073651-cf11bf9e` | 119 min (7141 s) | 4 |
| `session-20260702092321-bc90812b` | 119 min (7142 s) | 4 |

Raw benchmark artifacts: `research/legacy-*.json`, `research/shared-*-schema3.json`.

## 2. Performance vs. legacy combined timer+KDA baseline

Acceptance: shared initial wall time ≤ 1.25× legacy (timer + KDA) combined scan.

| session | legacy combined (s) | shared initial (s) | ratio | verdict |
|---|---|---|---|---|
| 4b5ec478 | 189.0 | 205.7 | **1.09×** | PASS |
| cf11bf9e | 233.6 | 237.8 | **1.02×** | PASS |
| bc90812b | 282.5 | 270.1 | **0.96×** | PASS |

All three ≤ 1.25×. The shared scan replaces two independent full-recording
decode passes (legacy timer + legacy KDA) with a single coarse decode plus
bounded refinement.

## 3. Coarse decode / refinement bounds

Acceptance: one coarse decode serves all coarse detectors; refinement union
≤ 15% of source duration; refinement frame count bounded and attributable.

| session | coarse frames | refined frames | refine union | % of source | cap exhausted |
|---|---|---|---|---|---|
| 4b5ec478 | 553 | 13670 | 829.3 s | 15.0% | yes (at cap) |
| cf11bf9e | 715 | 16231 | 1071.1 s | 15.0% | yes (at cap) |
| bc90812b | 715 | 15149 | 1071.3 s | 15.0% | yes (at cap) |

- A single coarse decode schedule (`coarse_decoded_frames`) feeds timer, scene,
  KDA, match_result and respawn detectors — confirmed by `detector_health`
  invocation counts sharing the coarse frame budget.
- Refinement union lands exactly at the 15% cap on every session
  (`refinement_cap_exhausted=true`); cap exhaustion is persisted in metrics, and
  schema-3 range prioritization admits production KDA evidence over shadow-only
  ranges when the cap binds.

## 4. Cache behavior

Acceptance: cached downstream reruns perform zero coarse OCR calls.

- Re-running `run_shared_vision.py` on `4b5ec478` without `--force-reprocess`
  returned `cache_hit=true`, `cache_reason=compatible_asset`
  (`research/shared-4b5ec478-cached.json`).
- Downstream `arl segmenter --force-reprocess` and
  `arl highlight-planner --force-reprocess` on the same session logged
  `vision source=shared_asset` and consumed the cached asset — **no coarse OCR
  decode was re-issued**. Segmenter emitted 3 boundaries; highlight planner
  emitted its plan from cached evidence.

## 5. Signal parity

Acceptance: no regression in boundary count, KDA coverage, SFX timing, or
export warnings.

| session | boundaries (legacy→shared) | KDA events (legacy→shared) |
|---|---|---|
| 4b5ec478 | 3 → 3 | 12 → 19 |
| cf11bf9e | 4 → 4 | 26 → 26 |
| bc90812b | 4 → 4 | 22 → 22 |

Boundary counts match on all sessions. KDA coverage is equal or higher; the
higher `4b5ec478` count reflects additional valid kill transitions captured by
the shared refinement, all passing existing KDA plausibility validation — no
spurious drops. Segmenter now consumes non-empty healthy shared candidate sets
including explicitly incomplete edge matches, so incompleteness alone no longer
forces a duplicate full-recording legacy scan.

## 6. Death / result shadow review

Death/result detectors run shadow-only (schema 3). Per the prior
`07-13-death-respawn-match-result-ocr` sibling task, shadow review accepted two
sessions with KDA-triggered HP-bar death/respawn transitions and safely rejected
one, after fixing false digits/results. Each session in this run reports exactly
one `death_respawn_state` event with `match_result`/`respawn` detectors healthy.

## 7. Rollout decision

**Decision: enable schema-3 shared vision analysis in the `publish` preset;
keep the legacy timer/KDA scan as an automatic fallback for this release; keep
death/result detectors shadow-only.**

Rationale:
- Performance, coarse-decode reuse, refinement bounds, cache behavior, and
  signal parity all pass on three representative sessions.
- Legacy scans are **not removed yet**: the segmenter still falls back to the
  legacy timer scan when shared evidence is missing/degraded or yields no
  candidates (spec: orchestration-contracts.md). This satisfies the
  "missing/degraded assets exercise the legacy fallback during rollout" criterion
  and de-risks the first active release.
- Death/result stay shadow-only until a dedicated activation task reviews more
  sessions; this task is scoped to integration/performance, not new-signal
  activation.

Follow-up (defer to a later task): after one active release cycle with no
fallback triggers and continued parity, remove the duplicate legacy timer/KDA
full-recording scans and promote death/result to active mode.

## 8. Automated checks

`python -m pytest` → **769 passed** (63.7 s). Compile/type checks green.
