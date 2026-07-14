# Unified Vision OCR Pipeline

## Goal

Create a reusable visual-analysis stage that decodes each recording once for coarse sampling, extracts multiple fixed HUD regions from the same frames, performs event-local refinement only when needed, and persists versioned visual evidence for segmentation, highlight planning, editing, copywriting, and quality reporting.

## Confirmed Facts

- Timer OCR currently runs inside `VisionMatchDetector` during coarse match detection and start/end refinement.
- KDA OCR currently runs independently inside `HighlightPlannerService`, including a publish-mode frame-by-frame refinement pass between coarse readings.
- Highlight visual activity and combat continuity also open their own `cv2.VideoCapture` paths, so adding more detector-specific scans would multiply video decoding work.
- Existing durable `HighlightPlanAsset.kda_events` proves that persisted visual events can be reused by edit planning and quality reporting without putting machine cues into subtitles.
- The agreed architecture is one low-frequency multi-region coarse scan, change-triggered candidate detection, small event-local refinement windows, and cached downstream reuse.

## Requirements

- Introduce a dedicated `vision-analysis` stage/CLI command before downstream planning stages.
- Decode a recording span once per coarse pass and dispatch each sampled frame to enabled detectors.
- Persist typed, versioned visual-analysis assets keyed by recording/session, source timeline, detector configuration, and input fingerprint.
- Make reruns idempotent and reuse cached assets when the recording fingerprint and detector configuration are unchanged.
- Support segmented recordings and translate local chunk timestamps into the recording-relative source timeline.
- Preserve best-effort behavior: unavailable OCR dependencies or unreadable HUD regions must degrade individual detectors, not fail the whole postprocess pipeline.
- Keep event refinement bounded to candidate windows; no detector may silently start a full-video frame-by-frame scan.
- Migrate timer and KDA consumers to prefer the durable visual-analysis asset while retaining a compatibility fallback during rollout.
- Design the event schema so later detectors can add death/respawn state, match result, team score, level, objectives, and other HUD readings without schema churn.
- Expose detector timing, read counts, cache hits, failures, and refinement frame counts for performance validation.
- The first release supports the project's standard 1920x1080 LoL Chinese-client capture only. 720p input is explicitly unsupported and does not require fallback crops or acceptance coverage.
- The `publish` preset enables visual analysis automatically. Default/non-publish operation remains opt-in during rollout.
- Timer/KDA consumers retain legacy direct-scan fallback until representative performance, parity, and human-review gates pass.

## Acceptance Criteria

- [x] One coarse video sampling pass can produce timer and KDA readings from the same decoded frames. (foundation + migration; single coarse schedule feeds all detectors)
- [x] Timer-based segmentation and KDA-based highlight/SFX behavior remain functionally equivalent on existing fixtures. (migration task; boundary/KDA parity in integration validation)
- [x] A forced downstream highlight/edit/export rerun reuses cached visual assets without repeating coarse OCR. (integration task: segmenter/highlight force-reprocess consumed shared_asset, zero coarse decode)
- [x] Candidate refinement scans only bounded source ranges and records its frame-processing cost. (refinement union + refined_decoded_frames metrics)
- [x] Segmented recording timestamps remain correct across chunk boundaries. (chunk→source timeline translation; covered by pipeline tests)
- [x] Invalid or partial detector output is ignored safely and does not block unrelated detectors. (per-detector degradation; death/respawn safe-validation task)
- [x] Typed visual assets are reset/status-aware and covered by unit plus pipeline tests. (769 tests incl. vision_analysis unit + pipeline suites)
- [x] Performance validation reports wall time, decoded coarse frames, OCR calls by detector, refined frames, and cache behavior on representative recordings. (validation-report.md, three sessions)
- [x] Initial publish visual-analysis wall time is no more than 1.25x the current combined timer plus KDA scan baseline on representative recordings. (1.09x/1.02x/0.96x)
- [x] Enabling additional coarse detectors does not multiply decoded coarse-frame count by detector count. (coarse_decoded_frames constant per session regardless of detector count)
- [x] Cache-hit downstream reruns perform zero coarse OCR calls. (cache_hit=compatible_asset; downstream force-reprocess reuse)
- [x] Refined source ranges total no more than 15% of match source duration by default; hitting the cap records degradation and stops range expansion. (15.0% at cap, refinement_cap_exhausted persisted)

## Likely Delivery Shape

- Foundation: asset contracts, cache/fingerprint, shared frame dispatcher, CLI/state/reset/status integration.
- Migration: timer OCR and KDA OCR consume the shared asset; preserve compatibility fallback.
- First new signals: death/respawn state and victory/defeat result, or a narrower subset chosen during planning.
- Follow-up signals: team score and cut-boundary HUD continuity; objectives/level/item state remain later candidates.

## Out of Scope

- Full-video generic text recognition.
- Making OCR mandatory for export.
- Replacing subtitles or semantic analysis with OCR.
- Implementing every possible HUD detector in the first delivery.

## Product Decisions

- The first implementation includes infrastructure, timer/KDA migration, death/respawn state, and victory/defeat result.
- The supported layout is 1920x1080 LoL Chinese client. 720p video is considered unusable for this product and is out of scope.
- Publish mode enables the new stage automatically; other modes remain disabled unless explicitly configured.
- Legacy timer/KDA scanning is removed only after rollout acceptance, not in the initial migration commit.
- Performance gates are 1.25x maximum initial-run overhead versus the existing combined timer/KDA baseline, zero coarse OCR on cache hits, and a default 15% source-duration refinement cap.
- Death/respawn and match-result detectors launch in shadow mode. They persist evidence and proposed downstream adjustments but do not alter cuts/boundaries until at least three representative sessions pass accuracy and human-review gates.
