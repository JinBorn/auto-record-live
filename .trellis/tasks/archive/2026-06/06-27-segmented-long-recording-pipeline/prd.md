# Segmented Long Recording Pipeline

## Goal

Long livestream recordings should no longer require one very large source MP4 for downstream postprocess. The pipeline should support continuous FFmpeg recording into fixed-duration media chunks, preserve a stable global recording timeline, and let segmentation, subtitles, highlights, edit planning, exporting, copywriting, status, repair, and reset continue to work when a match crosses chunk boundaries.

## User Value

- Reduce risk from huge single raw files during long unattended recording.
- Avoid dropped frames caused by stop-and-restart recording loops.
- Keep downstream editing/export behavior coherent even when one match spans multiple chunks.
- Improve postprocess performance by letting later stages operate on the media windows they need.

## Confirmed Facts

- Current durable recording contract is `RecordingAsset(session_id, source_type, path, started_at, ended_at)`.
- Current recorder writes `data/raw/<session_id>/recording-source.mp4` or a placeholder `.txt`.
- Segmenter, subtitles, highlights, editing source-music detection, exporter, copywriter cover extraction, status, reset, and repair currently assume one latest `RecordingAsset.path` per session.
- Subtitle ASR already has boundary-limited `clip_timestamps`, but it still points at the single source file.
- Exporter edit plans and highlight plans use source-time windows relative to the recording/match boundary.
- Existing raw repair scans `data/raw/session-*/recording-source.mp4`.
- The optimized design must remain compatible with old single-file `RecordingAsset` rows.

## Requirements

- Recording must support an opt-in continuous segmented mode using one FFmpeg process and FFmpeg-side segmenting, not repeated process restarts.
- Segment files must be accompanied by a durable manifest that maps every chunk to the same session-global timeline used by match boundaries and edit plans.
- Chunk boundaries must not be treated as match boundaries.
- A match or highlight/edit window that crosses chunks must be resolvable into one or more concrete local media spans.
- Existing single-file recordings and manifests must continue to work without migration.
- Downstream stages must avoid full-recording scans where a bounded window can be resolved to smaller chunk spans.
- Missing, unreadable, or incomplete chunk manifests must degrade to the existing single-file behavior when a valid `RecordingAsset.path` exists.
- Status and repair tooling must surface segmented recordings clearly enough for operators to diagnose missing chunk manifests or unregistered chunks.

## Acceptance Criteria

- [x] Config supports segmented recording mode with a chunk duration setting and defaults that preserve existing single-file behavior.
- [x] Recorder can produce a chunk manifest for direct-stream recordings without stopping/restarting the live stream.
- [x] Durable models represent recording chunks and preserve `RecordingAsset.path` compatibility.
- [x] A shared resolver converts session-global time windows into chunk-local spans, including cross-chunk windows.
- [x] Unit tests cover single-file resolution, one-chunk resolution, cross-chunk resolution, edge clamping, and missing manifest fallback.
- [x] Exporter can render at least stream-copy/highlight/edit-plan windows that cross chunk boundaries by resolving them to concrete inputs.
- [x] Subtitle preprocessing/ASR can extract a boundary window from chunk spans instead of requiring a full-session input file.
- [x] Highlight KDA/frame sampling can resolve boundary windows against chunked media or explicitly fall back with a logged reason.
- [x] Repair/status recognize segmented raw outputs and do not report healthy segmented sessions as unregistered single-file recordings.
- [ ] Existing tests for single-file recording/export/subtitles continue to pass.

## Out Of Scope For First Pass

- Re-encoding all historical raw recordings into chunks.
- Perfect frame-accurate cutting at arbitrary non-keyframe chunk boundaries.
- Distributed storage or cloud object storage.
- UI for inspecting chunk manifests.
- Deleting/archiving old raw chunks after successful publishing.

## Open Questions

- None blocking. Default implementation should use opt-in segmented direct-stream recording with a conservative 15 minute chunk duration and maintain single-file mode as the default until the full downstream path is verified.
