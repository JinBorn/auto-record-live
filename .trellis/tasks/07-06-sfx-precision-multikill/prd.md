# SFX precision and multi-kill variants

## Goal

Fire the coin/kill sound effect at the actual kill moment (not at segment
starts), distinguish single kills from multi-kills, and source sounds from a
user-supplied `data/sfx` library with the current synthetic coin as fallback.

## User Value

Demo2 plays a coin-drop sound exactly when the streamer scores a solo kill.
Our current implementation drops a synthesized coin at the start of any
key-event segment (up to 4 per export), which reads as arbitrary rather than
reactive.

## Requirements

- SFX asset library: `data/sfx/library.json` manifest (template already
  scaffolded) + `data/sfx/tracks/` files, schema mirroring the BGM library
  conventions with Chinese usage notes. Categories for v1:
  `kill_coin` (single kill), `multi_kill` (double and above),
  `transition_whoosh` (used by the teaser task), `teaser_impact` (optional).
  Missing files skip that category with a log; empty/missing manifest falls
  back to the synthetic coin (current behavior preserved).
- Kill-time alignment: map `kda_change` source timestamps into rendered
  timeline positions (source->timeline mapping over the concatenated
  segments) and schedule hits at those positions with a small lead/lag offset
  knob; tolerance goal +/-0.5s. Segments without a mapped KDA event inside
  them fall back to the current segment-start rule only if the segment reason
  is SFX-eligible.
- Multi-kill detection: kills increasing by >=2 within a short window in the
  KDA OCR series, or multi-kill announcement keywords (双杀/三杀/四杀/五杀 and
  English equivalents) in subtitle cues near the timestamp, selects the
  `multi_kill` variant.
- Deaths do not trigger coin SFX (kill-only), matching the demo semantics.
- Rate limiting stays: configurable minimum interval (default 20s) and
  per-export cap (default raised 4 -> 6, env-overridable).
- Quality-report integration: report each hit's delta to the nearest
  `kda_change`; the SFX-to-kill delta metric becomes meaningful and is added
  to the report if not already present.

## Out Of Scope

- Emotion/reaction-driven SFX ("wow" moments) — still excluded per 06-25.
- Automatic downloading of sound assets.
- Per-champion or per-event sound theming.

## Acceptance Criteria

- [ ] On regenerated validation samples, every emitted kill SFX lands within
      +/-1s of a `kda_change` timestamp (quality-report verified); the
      +/-0.5s goal is recorded as measured.
- [ ] A fixture with a double-kill KDA delta selects the `multi_kill` asset
      when present, `kill_coin` otherwise.
- [ ] With no `data/sfx` manifest, behavior degrades to the current synthetic
      coin (existing tests keep passing).
- [ ] Timeline mapping utility is unit-tested including edge cases (event in
      trimmed-out gap, event in teaser segment, event near segment edges).
- [ ] README documents the sfx library format and where to drop files.

## Notes

- Medium complexity: short `design.md` for the source->timeline mapping and
  variant selection, plus `implement.md`, before start.
- User will download real coin/whoosh sounds into `data/sfx/tracks/`; all
  logic must be testable with tiny generated WAV fixtures.
