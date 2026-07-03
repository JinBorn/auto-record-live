# Condensed Highlight Duration Tightening

## Goal

Make publish-mode condensed League videos more highlight-dense and closer to the `data/demo2` pacing, while preserving important gameplay events even when the streamer is silent during fights.

## Background

Recent generated exports still run around 13-18 minutes. Analysis of `data/demo2` and current exports found:

- `data/demo2` duration is about 7:38 and has subtitle-active time around 67%.
- Current exports keep about 50%-69% of the source match and run about 13:31-18:31.
- Current exports have subtitle-active time around 36%-45%, many 8s+ no-subtitle gaps, and very long `condensed_key_event` windows.
- Some current key-event windows are over 550s, which means KDA preservation is protecting too much surrounding low-value gameplay.
- User explicitly requires that trimming must not depend too heavily on subtitles, because the streamer may be silent during fights.

## Requirements

- Tighten condensed outputs with a dynamic target range of about 7-20 minutes, scaled by source match length and composite highlight density. Do not force all matches into 7-9 or 7-11 minutes.
- Use a composite event score instead of subtitle-only trimming:
  - KDA kill/death changes remain protected.
  - Visual action/fight signals must protect silent combat.
  - Subtitle cues protect narration and punchlines but are not the only retention signal.
  - Continuity snippets protect source-time/KDA/game-clock jumps but should not dominate the edit.
- Compress low-value no-subtitle gaps inside retained windows, except near protected fight/death/KDA events.
- Split oversized `condensed_key_event` windows into smaller event islands when their interiors contain long low-value spans.
- Reduce match-start and match-end context when no high-signal action or speech is present.
- Keep existing anti-regression guarantees:
  - no abrupt alive/farming to death-countdown jump;
  - no KDA kill/death event hidden by cuts;
  - no mid-sentence subtitle/speech cuts;
  - no full-span or near-full-match fallback when a condensed plan exists.
- Preserve current export quality controls, including fixed bitrate behavior and subtitle retiming.

## Acceptance Criteria

- [ ] For the current validation set (`session-20260617073649-4b5ec478_match02`, `session-20260617073651-cf11bf9e_match02/03/04`), regenerated edit plans use a dynamic 7-20 minute target based on source duration, KDA/fight density, visual action, and narration density.
- [ ] No latest plan has a single `condensed_key_event` segment longer than 120s unless it is justified by continuous protected fight/KDA/visual action density.
- [ ] No-subtitle gaps inside the rendered edit longer than 8s are either compressed or classified as protected silent combat/objective/death context.
- [ ] `condensed_continuity` total duration stays below 10% of rendered duration by default, unless required to satisfy max source-gap continuity.
- [ ] Every detected KDA kill/death cue remains fully covered by `condensed_key_event` or `highlight_keyword`, not only continuity snippets.
- [ ] Adjacent source-time gaps in final edit/highlight plans remain within `condensed_boring_gap_threshold_seconds`.
- [ ] Speech-boundary protection still prevents cutting through active subtitle cues.
- [ ] Tests cover silent fight retention without subtitle cues, long no-subtitle gap compression, oversized key-event splitting, KDA preservation, and continuity budget caps.
- [ ] Manual validation report for regenerated sample exports includes duration, bitrate, subtitle-active ratio, long no-subtitle gap count, max source gap, KDA uncovered count, BGM/SFX counts, and any exception reasons.

## Out Of Scope

- New ASR model selection or subtitle transcription quality changes.
- New BGM/SFX design, except ensuring duration trimming does not regress current audio instructions.
- UI changes.
- Reworking match boundary detection.

## Decisions

- Duration targeting is dynamic, not fixed: about 7-20 minutes is acceptable when longer source matches contain more high-quality highlight content. The planner should still remove low-value waits, walking, farming, and repeated no-signal gaps instead of preserving length for its own sake.
