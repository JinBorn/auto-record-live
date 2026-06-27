# Segmented Long Recording Pipeline Design

## Architecture

Add segmented recording as a compatibility layer around the existing single-file pipeline:

1. `RecordingAsset.path` remains the legacy primary media path.
2. A new durable `RecordingChunkManifest` records chunk files for a session.
3. A shared media resolver maps source-time windows to concrete media spans.
4. Downstream stages call the resolver instead of assuming one physical file.

This keeps existing assets readable while allowing new segmented sessions to avoid huge source files.

## Durable Contracts

### RecordingAsset

Keep the current fields unchanged. For segmented recordings, `path` should point to the manifest path or a stable session placeholder only after downstream consumers can resolve chunks. During rollout, prefer writing both:

- legacy-compatible `path` when a concatenated/full file exists
- chunk manifest for optimized consumers

### RecordingChunkManifest

New JSON/JSONL model under shared contracts:

```python
class RecordingChunk(BaseModel):
    path: str
    started_at_seconds: float
    ended_at_seconds: float
    duration_seconds: float
    index: int

class RecordingChunkManifest(BaseModel):
    session_id: str
    source_type: SourceType
    path: str
    started_at: datetime
    ended_at: datetime | None = None
    chunks: list[RecordingChunk]
    created_at: datetime
```

Manifest storage:

- file: `data/raw/<session_id>/recording-chunks.json`
- optional index manifest: `data/tmp/recording-chunk-assets.jsonl`

The JSONL index lets status/repair find manifests without scanning raw directories every time. The per-session JSON file is the durable source of truth.

## Media Resolver

Create `src/arl/media/recording_resolver.py` or an equivalently scoped module.

Core types:

```python
class MediaSpan(BaseModel):
    path: str
    source_start_seconds: float
    source_end_seconds: float
    local_start_seconds: float
    local_end_seconds: float
```

Core API:

```python
resolve_recording_window(asset, start_seconds, end_seconds, settings) -> list[MediaSpan]
recording_duration_seconds(asset, settings=None) -> float
recording_primary_video_path(asset, settings=None) -> Path | None
```

Rules:

- Single-file asset resolves to one span with local times equal source times.
- Manifest asset resolves to all chunks overlapping the requested source window.
- Gaps shorter than a small tolerance may be logged and skipped only when safe; larger gaps make the caller fall back/defer.
- All returned spans are clamped to valid local chunk ranges.

## Recorder

Segmented mode must use one FFmpeg process with muxer-level segmentation.

Direct-stream candidate command shape:

```bash
ffmpeg -nostdin -hide_banner -loglevel error \
  -headers ... -i <stream_url> \
  -map 0 -c copy \
  -f segment -segment_time <seconds> -reset_timestamps 1 \
  data/raw/<session_id>/chunks/recording-%05d.mp4
```

Important:

- Do not implement segmented mode by restarting the recorder per chunk.
- Segment durations are approximate because copy mode cuts near keyframes.
- After FFmpeg completes, probe chunks to write actual durations into the manifest.
- Keep existing non-segmented mode as default until downstream support is complete.

## Downstream Integration

### Segmenter

Initial phase:

- Use resolver duration instead of `RecordingAsset.path` duration.
- Vision detection can remain single-file only until a proxy/stitched input strategy exists.

Later phase:

- Run vision per chunk and translate detected local times to session-global times, or generate a low-resolution proxy for detection.

### Subtitles

Preferred approach:

- For a boundary, resolve chunk spans.
- If there is one span, pass that chunk and local clip timestamps.
- If multiple spans, preprocess those spans into one temporary WAV under `data/tmp/asr-audio/<session>/match-NN.wav`, then transcribe that WAV with local `[0, duration]` timing.

This avoids asking ASR to decode unrelated hours of video.

### Highlights

For KDA/frame sampling:

- Resolve the boundary to chunk spans.
- Sample each span locally and translate timestamps back to session-global time.
- If this is too broad for first pass, log `reason=chunked_media_not_supported` and skip KDA while keeping subtitle-based condensed planning.

### Editing

Source BGM detection should resolve sampled windows against chunk spans. Sampling a few windows can use the chunk that contains each sample.

### Exporter

Exporter is the main integration point.

- Stream-copy full/highlight exports: convert each requested source window into chunk-local inputs and concat.
- Edit plans: resolve each timeline segment before building the FFmpeg filter graph.
- Subtitle burn-in stays after concat for edit plans, preserving previous subtitle scaling fix.
- Audio BGM/SFX mixing remains timeline-output based and does not need chunk awareness.

### Copywriter Cover

Cover extraction should use the resolver to pick a concrete chunk for the requested source frame. If no chunk is available, keep existing export fallback.

### Status / Repair / Reset

- Repair should register `recording-chunks.json` when chunk files are complete and unregistered.
- Status should distinguish:
  - single-file recording registered
  - chunked recording registered
  - raw chunk directory exists but manifest/index missing
- Reset must not delete raw chunks, matching current raw-recording preservation behavior.

## Compatibility

- Default behavior remains unchanged.
- Existing `recording-source.mp4` rows continue to work.
- New resolver should first pass all existing single-file tests.
- New chunk-aware consumers should be introduced stage by stage behind helper APIs.

## Rollback

- Disable segmented recording config to return to the current single-file recorder.
- Manifest-aware stages must fall back to single-file `RecordingAsset.path` if no chunk manifest is present.
- Avoid irreversible migrations in the first pass.
