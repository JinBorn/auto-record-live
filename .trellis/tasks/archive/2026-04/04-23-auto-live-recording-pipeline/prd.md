# brainstorm: auto live recording pipeline

## Goal

Build a local-first pipeline that monitors followed streamers across Chinese live platforms, detects when they go live, records the stream at 1080p or higher, segments gameplay into match-level clips, trims low-value intervals, generates subtitles, and exports a polished 10 to 20 minute video with minimal manual work.

## What I already know

* Target platforms include Douyin, Bilibili, Douyu, and Huya.
* The user wants automatic live detection and recording.
* The user wants resolution at 1080p or higher when source quality allows it.
* The user wants semantic segmentation, for example one League of Legends match per saved clip.
* The user wants automatic trimming of low-value gameplay sections such as uneventful travel time.
* The user wants subtitles in the output video.
* The final deliverable should be a 10 to 20 minute edited video.
* MVP content scope is League of Legends only.
* When unattended stability and final video quality conflict, MVP should prioritize final video quality.
* MVP first platform is Douyin live streaming.
* MVP may depend on a logged-in Douyin browser/session on Windows for stream acquisition and refresh.
* MVP v1 stops at per-match clips with subtitles and single-match exports, not full multi-match highlight assembly.
* Subtitle and ASR processing should be fully local and offline.
* MVP may fall back to browser-side screen capture when direct live stream recording is unavailable.
* MVP should validate end to end with one fixed streamer before expanding to multiple streamers.
* Available hardware is Lenovo R7000p 2020 with GTX 1650, Ryzen 7 4800H, 32 GB RAM, 1 TB SSD, Windows 11 with WSL2 Ubuntu 24.04.
* Given the hardware, multi-stage local processing is feasible, but fully local high-accuracy vision + speech + highlight editing across long recordings will require careful model/runtime choices and likely asynchronous batch processing instead of near-real-time editing.

## Assumptions (temporary)

* MVP should run primarily on the user's own machine rather than depend on paid cloud GPU services.
* MVP should support only League of Legends first before generalizing to other content types.
* Recording quality should prefer source-native stream quality rather than AI upscaling.
* Subtitle generation can be offline/batch after recording rather than live subtitles.
* Automatic editing quality can initially be heuristic-assisted rather than fully cinematic.
* It is acceptable for post-processing time to be longer if final output quality is better.

## Open Questions

* None at current MVP scope.

## Requirements (evolving)

* Monitor configured streamers on supported platforms.
* For MVP, support Douyin as the first recording source.
* Detect stream start reliably and trigger recording automatically.
* Capture the highest allowed stream quality, targeting 1080p+ when available.
* Prefer direct live stream recording when possible, with browser-side capture as a fallback path.
* Persist raw recordings safely and support long-duration sessions.
* Detect match/session boundaries from raw recordings.
* For MVP, detect League of Legends lifecycle stages such as champion select, loading, in-game, and post-game.
* Produce structured intermediate clips per match.
* Identify and remove low-value intervals according to configurable rules.
* Generate subtitles aligned to the retained video.
* For MVP v1, export a subtitle-burned single-match video automatically.
* Operate within consumer-grade local hardware constraints.
* Prefer higher-quality post-processing and selection over fastest turnaround when resources are limited.
* Allow Windows-side browser/session automation as part of the acquisition layer for Douyin.
* Keep subtitle generation and speech recognition fully local and offline.
* Validate the complete workflow with one fixed streamer before expanding scheduler scope.

## Acceptance Criteria (evolving)

* [ ] User can configure a streamer list and platform source.
* [ ] MVP can complete one end-to-end run for one fixed Douyin streamer.
* [ ] System detects a supported streamer going live and starts recording automatically.
* [ ] For a test stream, raw recording is saved successfully at source quality up to 1080p+ when available.
* [ ] For League of Legends MVP content, at least one full match is segmented into an individual clip automatically.
* [ ] The pipeline generates subtitles for the retained output clip.
* [ ] The pipeline exports a watchable single-match video with subtitles without manual timeline editing.

## Definition of Done (team quality bar)

* Tests added or updated where practical for parsers, schedulers, and segmentation logic.
* Lint, typecheck, and core validation pass.
* Docs and setup notes are updated.
* Runtime, storage, and failure-recovery constraints are documented.

## Out of Scope (explicit)

* General-purpose support for every live content genre in v1.
* Perfect semantic understanding of all games and all streamer behaviors.
* Fully cloud-distributed media processing in MVP.
* Automatic multi-match 10 to 20 minute highlight compilation in MVP v1.
* Automated publishing to video platforms in MVP unless later requested.

## Technical Notes

* WSL2 is suitable for orchestration, scraping, downloading, and batch media processing, but hardware decode/encode and some Windows-only capture/browser automation paths may require a split architecture between Windows host and WSL services.
* Chinese live platforms often change APIs, anti-bot behavior, and stream URL signing; platform adapters should be isolated per site and treated as fragile integration points.
* Match segmentation and highlight trimming are different problems and should likely be separate stages.
* Douyin MVP should assume browser automation or session extraction on Windows host, then hand off recording/processing jobs to WSL services.
* Local offline ASR is feasible on this hardware if treated as post-processing rather than real-time inference.
* Browser capture fallback improves acquisition robustness but may reduce source fidelity versus direct stream recording.
