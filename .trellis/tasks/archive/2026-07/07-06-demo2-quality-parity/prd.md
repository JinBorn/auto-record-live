# Demo2 quality parity for publish editing

## Goal

Close the observable quality gap between current `publish` preset exports and the
`data/demo2` reference edit (【觅渡】AP上单电刀机器人...) across seven capability
areas: publishing copy, ASR subtitles, teaser, kill SFX, close-up zooms, BGM
arrangement, and cover rendering. This is the parent/coordination task; each
area is delivered by a child task.

## Background Evidence

The 2026-06 `06-25-demo-editing-upgrades` task family already shipped first
versions of every demo2 capability. The 07-02 validation report
(`.trellis/tasks/archive/2026-07/07-02-condensed-highlight-duration-tightening/validation-report.md`)
shows the remaining gap on 4 real samples:

- Titles/cover lines degrade to raw ASR excerpts with traditional-Chinese
  mojibake (e.g. "堆場式是咋的 就對面的人他也會...") because
  `CopywriterService._summary_headline` is a hardcoded keyword-phrase table
  overfit to demo1/demo2 content.
- Teaser was emitted on 0 of 4 samples (`_PRIMARY_TEASER_REASONS` only accepts
  `highlight_keyword` windows).
- Subtitle active ratio 39-47% with no-subtitle gaps up to 42.5s
  (faster-whisper `small`, no zh normalization, no domain prompt).
- BGM beds: 0-2 per export; source-music detection globally disables BGM even
  when music covers only part of the match.
- SFX: synthetic coin at segment starts (not at kill timestamps), no
  single/multi-kill distinction.
- Zoom: exactly 1 static full-segment punch-in (up to 30s), no ease animation.

Root causes: (1) no LLM semantic layer, (2) ASR is the weakest upstream stage,
(3) plan/render contracts cannot express short animated close-ups, timed SFX,
multi-phase BGM, or teaser transitions.

## Decisions (Jinson, 2026-07-06)

- LLM: cloud API via an OpenAI-compatible chat-completions abstraction
  (covers DeepSeek/Qwen/Kimi/GLM/OpenAI and Anthropic's compatibility
  endpoint). API key supplied later via `.env`; heuristic fallback stays.
- Hardware: GTX 1650 4GB (CUDA available, ctranslate2 4.7.2 detects 1 device).
  ASR default upgrades to whisper `medium` int8_float16 on CUDA; `large-v3` is
  opt-in only with automatic OOM fallback. Never assume >3GB free VRAM.
- Audio assets are user-supplied (copyright): SFX under `data/sfx/tracks/` with
  `data/sfx/library.json` manifest; more BGM under `data/bgm/tracks/`
  registered in `data/bgm/library.json`. Pipeline fails soft to current
  synthetic/default assets when files are missing.
- Teaser duration is dynamic and must not be long: proportional to export
  length, clamped (see child PRD), not a fixed 2-minute block like demo2.

## Child Task Map

| Wave | Task | Priority |
|---|---|---|
| 0 | `07-06-export-quality-report-cli` — automated per-export quality metrics; acceptance instrument for all other children | P0 |
| 1 | `07-06-llm-copywriting-engine` — pluggable cloud LLM producing titles/cover lines/summary/tags/teaser hints | P0 |
| 1 | `07-06-asr-quality-upgrade` — whisper medium on CUDA, zh-Hans normalization, domain prompt, VAD tuning | P0 |
| 2 | `07-06-teaser-robustness-transition` — non-empty dynamic teaser + transition card into main | P1 |
| 2 | `07-06-sfx-precision-multikill` — kill-timestamp-aligned coin SFX + multi-kill variants + asset library | P1 |
| 2 | `07-06-zoom-closeup-upgrade` — multiple short eased close-ups incl. chat-burst trigger | P1 |
| 2 | `07-06-bgm-arrangement-enhance` — 2-3 phase beds, crossfades, span-based source-music avoidance | P1 |
| 3 | `07-06-cover-visual-upgrade` — smart frame pick + demo2 typography + multi-candidate covers | P2 |

Wave order is a recommendation: wave-0 first (measurement), wave-1 next
(semantic foundations), wave-2 children are mutually independent, wave-3 last
(depends on LLM cover lines for best results).

## Cross-Child Integration Constraints

- Pipeline stage order is currently: stage-hints, segmenter, subtitles,
  highlight-planner, edit-planner, exporter, copywriter. LLM outputs consumed
  by the edit-planner (teaser hints) must be produced before edit-planner runs;
  the llm child owns this ordering design and must document the contract the
  teaser child consumes.
- Match-boundary guarantees from past condensed-editing work are inviolable:
  teaser segments never become the canonical match start; main segments start
  at validated boundaries.
- All new features stay behind the existing opt-in shape: default (non-publish)
  presets remain byte-identical in behavior unless flags are enabled.
- Every child must keep `data/` runtime artifacts out of git.

## Cross-Child Acceptance Criteria (final integration review)

- [ ] Regenerating the two 07-02 validation sessions
      (`session-20260617073649-4b5ec478` m02, `session-20260617073651-cf11bf9e`
      m02-04) with the publish preset plus all shipped children produces a
      quality report showing: teaser present on >=3 of 4 samples, subtitle
      active ratio >=55%, >=2 BGM beds where no dominant source music, all kill
      SFX within +/-1s of a `kda_change`, >=2 close-ups per export where
      triggers exist, and titles/cover lines that are not raw ASR excerpts.
- [ ] A fresh session from a different streamer/content (no demo1/demo2
      vocabulary) produces readable titles and cover lines (LLM path), proving
      the overfit keyword tables are no longer load-bearing.
- [ ] `pytest` suite passes; no regression in existing exporter/editing/
      highlights/copywriter tests.
- [ ] Backend specs updated (new `editing-quality` guide or extension of
      `export-configuration.md`) capturing quality thresholds and the
      keyword-overfit lesson.

## Out Of Scope

- External reference inserts ("引经据典" film clips) — remains deferred as
  decided in `06-25-demo-editing-upgrades`.
- Automatic downloading of any copyrighted music/SFX/video.
- Replacing validated match boundary detection.
- Facecam/streamer cutout compositing on covers (no facecam source in current
  recordings).

## Notes

- This parent task should not be the implementation target; it owns the task
  map, integration constraints, and the final integration review.
- Sub-agent dispatch has been unreliable in this environment (500 panics);
  children are expected to be implemented inline.
