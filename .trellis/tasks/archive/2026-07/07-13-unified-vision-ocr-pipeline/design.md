# Design

## Architecture

Add a recording-scoped `vision-analysis` stage before segmentation and downstream postprocessing:

```text
RecordingAsset / segmented source manifest
  -> shared coarse frame sampler
  -> detector dispatcher (timer, KDA, result, death/respawn)
  -> typed readings + derived event candidates
  -> bounded local refinement jobs
  -> versioned VisionAnalysisAsset
  -> segmenter / highlights / editing / copywriter / quality-report
```

The stage owns video decoding, detector scheduling, fingerprints, cache reuse, and telemetry. Detector modules own only crop extraction and interpretation of a provided frame.

## Durable Contracts

Introduce typed additive contracts rather than embedding machine events in SRT:

- `VisionAnalysisAsset`: session/recording identity, source duration, input fingerprint, detector-config fingerprint, schema version, timestamps, detector health, coarse/refined cost metrics.
- `VisionReading`: source timestamp, detector kind, typed payload, confidence, crop/layout profile, coarse/refined provenance.
- `VisionEvent`: stable ID, kind, source start/end/observed timestamp, confidence, evidence reading IDs, typed attributes.

Initial reading/event kinds:

- timer reading
- KDA reading and KDA change
- death-state / respawn countdown / respawn completion
- match-result candidate and confirmed victory/defeat

Store the latest compatible asset per recording fingerprint in append-only JSONL, following existing latest-row-wins consumers. State/reset/status must treat the asset as a first-class pipeline output.

## Shared Sampling

- Resolve segmented recordings through the existing recording-window resolver.
- Choose the smallest enabled coarse interval as the decode schedule; slower detectors run only on their due samples.
- Decode each due source frame once, then dispatch the same frame object to all due detectors.
- Detectors must not open `VideoCapture` themselves during coarse analysis.
- Skip unchanged fixed HUD crops where safe, but still emit enough stable readings for validation.
- Keep match-boundary-independent source timestamps so timer readings can create boundaries and later consumers can slice the same asset by match.

## Refinement

Coarse changes create explicit refinement candidates. The stage merges overlapping candidate ranges and performs one local decode per merged range, dispatching every refined frame only to requesting detectors.

- KDA refinement preserves the existing stable-baseline -> three stable target frames rule.
- Match-result refinement searches only around a coarse result candidate or timer/scene transition.
- Death/respawn refinement searches only around KDA death changes, death-like scene evidence, or detected countdown candidates.
- Every refinement has a maximum source duration and frame budget. Exceeding it yields degraded detector health, not a pipeline failure.

## Cache and Idempotency

Cache identity includes:

- recording/segment manifest fingerprint and file metadata
- schema version
- layout profile and crop coordinates
- enabled detectors and detector-specific versions/settings
- coarse interval and refinement policy

Downstream reruns reuse the asset. `--force-reprocess` on a downstream stage does not invalidate visual analysis. A dedicated vision-analysis force option appends a replacement asset.

## Migration

- `VisionMatchDetector` first prefers timer readings from `VisionAnalysisAsset`; direct scanning remains a temporary fallback when the asset is absent or incompatible.
- `HighlightPlannerService` first prefers persisted KDA readings/events; its current private coarse/refinement scan remains a temporary fallback.
- `HighlightPlanAsset.kda_events` remains populated for compatibility with editing/copy/report consumers until those consumers adopt the generic visual-event view.
- Remove duplicate scanning only after parity tests and representative-session validation pass.

## Detector Strategy

- The initial layout profile is fixed to 1920x1080 LoL Chinese-client captures. Crop coordinates may be represented through a versioned layout profile, but no 720p scaling/fallback behavior is required.
- Fixed numeric HUD uses lightweight template/threshold recognition before general OCR.
- Victory/defeat uses a small multilingual template/text vocabulary and temporal confirmation.
- Death/respawn combines countdown OCR with visual state; absence of a readable countdown must not imply the player is alive.
- All accepted values require plausibility and temporal monotonicity checks.

## Performance Contract

Persist and report:

- coarse decoded frames
- detector invocations and accepted readings
- unchanged-crop skips
- refinement candidates, merged ranges, decoded refined frames
- wall time by coarse/refinement/detector
- cache hit/miss reason

The integration target is one shared coarse decode pass. Local refinement cost may exceed the old implementation only when new signals are enabled, and must remain proportional to candidate ranges rather than recording duration.

Hard rollout budgets:

- representative initial publish analysis wall time <= 1.25x the legacy timer + KDA scan baseline
- cache-hit downstream reruns = zero coarse OCR invocations
- default union of refined source ranges <= 15% of match source duration
- reaching a frame/range budget marks requesting detectors degraded and prevents further refinement growth

## Rollout and Rollback

- Feature-gated stage and per-detector switches. The `publish` preset enables the stage and the initial detector set; default/non-publish mode remains opt-in during rollout.
- Compatibility fallback remains enabled for the migration release.
- Timer/KDA asset-backed behavior may be active immediately after parity validation. Death/respawn and match-result detectors default to shadow mode and emit proposed boundary/edit adjustments without applying them.
- Active rollout of new detectors requires review of at least three representative sessions plus an explicit configuration change.
- A detector failure marks only that detector degraded.
- Disable the new stage or individual detectors to restore legacy behavior without data migration.

## Supported Layout Boundary

The first release supports only the current 1920x1080 LoL Chinese-client capture. Other resolutions, client languages, HUD scales, and streamer overlays require future layout profiles and are not silently inferred.
