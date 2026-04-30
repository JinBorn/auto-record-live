# MVP System Design

## Scope Snapshot

MVP target:

* One fixed Douyin streamer
* League of Legends live content only
* Windows logged-in browser/session allowed
* Prefer direct stream recording, fallback to browser capture
* Fully local/offline subtitle generation
* Output one subtitle-burned video per detected match

## Proposed Architecture

Split the system into two runtimes:

* Windows host: browser/session automation and optional screen/audio capture fallback
* WSL2 Ubuntu: orchestration, recording control, metadata, segmentation, ASR, export

This avoids forcing fragile Douyin session handling into WSL while keeping the heavier batch pipeline in Linux.

## Runtime Components

### 1. Windows Agent

Responsibilities:

* Launch or attach to a persistent logged-in Chrome/Edge profile
* Monitor one Douyin live room URL
* Detect live/offline state
* Extract stream metadata if possible
* Attempt direct playable URL extraction
* If extraction fails, expose a browser capture fallback endpoint or command trigger
* Notify WSL orchestrator of state changes

Recommended stack:

* `Playwright` with persistent browser profile
* Small local HTTP service or file-based IPC to communicate with WSL

Why:

* Playwright is better than ad hoc scraping for session-bound pages
* Persistent profile avoids repeated login churn and anti-bot friction

### 2. Orchestrator

Responsibilities:

* Receive `live_started`, `live_stopped`, and source capability events
* Start one recording job only once per session
* Track job state and retries
* Persist session metadata
* Trigger downstream analysis when recording closes

Recommended stack:

* `Python 3.11+`
* `FastAPI` or lightweight CLI daemon
* `SQLite` for MVP metadata

Core tables:

* `streamer`
* `live_session`
* `recording_job`
* `media_asset`
* `match_segment`
* `subtitle_asset`
* `export_job`

### 3. Recorder

Responsibilities:

* Record direct stream when available
* Fall back to Windows-side browser capture when direct stream is unavailable
* Segment long recordings into manageable files
* Store timestamps, duration, source type, and file paths
* Validate output integrity before marking recording complete

Recommended path:

* Primary: `ffmpeg` recording from extracted HLS/FLV stream URL
* Fallback: browser capture via Windows `ffmpeg`/OBS-style capture path

Recording rules:

* Preserve source quality when possible
* Avoid re-encoding during direct recording if container copy is possible
* Rotate files by time, for example every 30 to 60 minutes, to reduce corruption risk

### 4. LoL Segmenter

Responsibilities:

* Turn raw recording into match-level clips
* Detect stage transitions:
  * champion select
  * loading screen
  * in-game
  * post-game/result
* Emit precise start/end timestamps for each match

Recommended strategy:

* Rule-based state machine first
* Inputs from sparse frame sampling, OCR, and template matching

Signals to consider:

* Draft UI layout
* Loading screen champion grid
* In-game HUD presence
* Scoreboard/minimap area
* End-game result banner
* OCR hits such as queue text, loading text, victory/defeat text

Why not pure AI first:

* Easier to debug
* Lower hardware pressure
* More deterministic on a single game

### 5. Subtitle Worker

Responsibilities:

* Extract audio from match clip
* Run offline ASR
* Produce subtitle files such as `srt` and `ass`
* Optionally post-process filler words or repeated fragments

Recommended stack:

* `faster-whisper`
* Start with `small` or `medium` model depending acceptable latency
* CPU or GPU mode chosen by benchmarking on GTX 1650

Expected tradeoff:

* `small` is safer for speed
* `medium` may improve Mandarin accuracy but could slow processing noticeably

### 6. Exporter

Responsibilities:

* Cut final per-match clip by detected timestamps
* Burn subtitles
* Add simple opening/closing card if needed
* Normalize output naming and output directory layout

Recommended stack:

* `ffmpeg`
* Optional `ass` subtitles for better styling

Output naming example:

* `YYYYMMDD_streamer_match01.mp4`

## Data Flow

1. Windows Agent watches Douyin room.
2. Douyin goes live.
3. Agent sends session event to Orchestrator with either:
   * direct stream URL metadata, or
   * browser capture fallback capability
4. Recorder starts and writes raw media files.
5. Live ends or recorder is stopped.
6. Orchestrator finalizes recording metadata.
7. LoL Segmenter scans recording and emits match boundaries.
8. Each match clip is generated.
9. Subtitle Worker produces subtitles for each match clip.
10. Exporter burns subtitles and writes final video.

## Recommended Repository Layout

```text
apps/
  windows-agent/
  orchestrator/
  workers/
    recorder/
    lol-segmenter/
    subtitle-worker/
    exporter/
packages/
  shared-types/
  shared-utils/
data/
  raw/
  processed/
  exports/
```

For a simpler MVP, this can start as one Python repo with folders:

```text
src/
  windows_agent/
  orchestrator/
  recorder/
  segmenter/
  subtitles/
  exporter/
```

## Technology Recommendations

### Language

Prefer Python for MVP.

Reasons:

* Good media tooling integration
* Good OCR/ASR ecosystem
* Fast enough for orchestration and batch processing
* Easier to prototype state-machine-based segmentation

### OCR

Start with one of:

* `PaddleOCR`
* `EasyOCR`

Use OCR only on targeted UI crops, not full frames.

### Frame Analysis

Use sparse sampling first:

* 1 fps for coarse classification
* temporary denser scan around candidate transition zones

This keeps the GTX 1650 usable.

### Capture

Preferred order:

1. Direct stream URL + `ffmpeg`
2. Browser capture fallback

Do not build around browser capture first unless forced, because it reduces fidelity and increases failure surface.

## Key Risks

### Douyin acquisition fragility

Risk:

* Session expiry
* Page structure changes
* Stream URL extraction breaks

Mitigation:

* Isolate adapter
* Keep browser fallback
* Add health checks and manual re-login prompt

### Match segmentation errors

Risk:

* False match starts
* Missing post-game boundary

Mitigation:

* Multi-signal state machine
* Save debug snapshots on transitions
* Build a small labeled test corpus from one streamer

### ASR speed on local hardware

Risk:

* Processing backlog after long streams

Mitigation:

* Single-match output only in v1
* Benchmark `small` and `medium` first
* Queue jobs rather than parallelize aggressively

### Storage growth

Risk:

* Long recordings consume SSD quickly

Mitigation:

* Separate `raw` and `exports`
* Add retention policy for raw files after successful export

## MVP Milestones

### Milestone 1: Acquisition Proof

Success means:

* Detect one fixed Douyin streamer live
* Start recording automatically
* Save a valid raw recording

### Milestone 2: Match Segmentation

Success means:

* Produce at least one correct LoL match clip automatically

### Milestone 3: Subtitles

Success means:

* Generate usable offline subtitles for one match clip

### Milestone 4: Final Export

Success means:

* Produce one subtitle-burned per-match video end to end

## Suggested First Build Order

1. Windows Agent with persistent Douyin session
2. Direct recording path with metadata persistence
3. Browser capture fallback path
4. Match boundary detector with debug artifacts
5. Offline subtitle worker
6. Final exporter

## What Not To Build Yet

* Multi-streamer scheduling
* Automatic highlight compilation across matches
* AI-generated title/cover/publishing
* General support for non-LoL live content
* Fully autonomous smart highlight editing
