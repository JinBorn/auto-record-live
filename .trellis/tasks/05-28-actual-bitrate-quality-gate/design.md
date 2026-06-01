# Actual Resolution Quality Gate Design

## Architecture and Boundaries

The gate belongs in the recorder path, after the probe has selected the best
available direct-stream candidate for the current room/platform. Probe selection
continues to prefer the highest available quality and existing platform gates
remain in place:

- Douyin keeps tier-based selection/gating.
- Bilibili keeps qn and advertised-bitrate metadata gating.
- Recorder validates the actual stream delivered by the CDN.

The hard quality requirement is actual recorded resolution >=1080p. Bitrate is
diagnostic and may be logged with the quality event, but it is not a global cap
and must not lower quality for other rooms/platforms.

Browser-capture validation is out of the first implementation unless the same
probe contract can be reused without extra risk. The primary target is
direct-stream `recording-source.mp4`.

## Data Flow

1. Orchestrator creates a recording job from the highest-quality live snapshot
   supplied by the windows agent.
2. Recorder starts ffmpeg as it does today.
3. During the early validation window, recorder measures the partial output
   with ffprobe or equivalent stream stats.
4. If the actual video height is at least 1080, recording succeeds or continues
   under the existing ffmpeg success/failure flow.
5. If the actual video height is below 1080, recorder stops ffmpeg, deletes the
   partial output, and emits a recorder audit event.
6. Orchestrator consumes the recorder event as a known terminal quality
   rejection, marks the job failed/stopped according to existing failure
   semantics, and clears active job linkage when needed so a later
   `live_started` can create a fresh job.

## Contracts

Recorder quality event:

- `event_type`: `quality_below_actual_resolution`
- `reason` / `reason_detail`:
  `quality_below_actual_resolution:<width>x<height><1920x1080`
- Include existing recorder audit core decision fields.
- Include observed bitrate when available, either in the reason detail or a
  structured field if the model is extended.

Failure classification should treat this as a terminal quality rejection, not a
transient network failure. It should not burn retries on the same unusable
candidate unless a future probe supplies a different stream URL/candidate.

## Compatibility

Existing probe behavior should remain compatible:

- Bilibili `min_stream_qn` and advertised `min_stream_bitrate_kbps` tests should
  keep passing.
- Douyin `min_quality_tier` tests should keep passing.
- Existing ffmpeg failure contracts should remain valid; the new event is an
  additional known recorder event type.

Existing deployments without ffmpeg/ffprobe continue to follow the current
placeholder/fallback behavior. Quality validation only applies when direct
ffmpeg recording is active and enough media exists to probe.

## Tradeoffs

Recorder-time validation avoids recurring probe-time pre-records that would
multiply CDN and CPU load on every poll interval. The tradeoff is that a bad
candidate can consume the early validation window before being rejected.

Resolution is the hard acceptance floor because the operator needs 1080p+
fixtures and does not want a global bitrate cap. Bitrate remains useful to
diagnose poor 1080p encodes, but failing solely on bitrate should be a later
policy decision if real fixtures show it is needed.

## Rollback

The change should be guarded by narrow recorder/orchestrator code paths. A safe
rollback is to disable the recorder-time quality validation config or revert the
new recorder quality event handling while leaving probe selection unchanged.
