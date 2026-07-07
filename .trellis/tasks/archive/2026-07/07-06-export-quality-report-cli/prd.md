# Export quality report CLI

## Goal

Turn the manual validation table from
`07-02-condensed-highlight-duration-tightening/validation-report.md` into a
repeatable CLI command that scores publish exports, so every sibling task in
`07-06-demo2-quality-parity` has an objective acceptance instrument.

## User Value

Today quality verification is a hand-run collection of ffprobe/grep steps.
A single command that emits the same metrics table makes regressions visible
immediately and removes per-task ad-hoc measurement work.

## Requirements

- New CLI subcommand (e.g. `python -m arl.cli quality-report`) accepting
  `--session-id`, `--match-index`/`--match-indices`, and `--all-latest`
  selectors consistent with existing CLI stages.
- For each selected export, compute and report at least:
  - export duration vs. condensed target duration and plan duration
  - container bitrate and resolution
  - subtitle active ratio (percent of export time covered by cues) and the
    top-N longest no-subtitle gaps with their timeline positions
  - KDA kill/death cue uncovered count (reuse existing highlight/vision data)
  - teaser: segment count and total seconds
  - BGM: bed count, per-bed source path, switch timestamps
  - SFX: hit count, timestamps, and (when kda events are available) the delta
    between each hit and the nearest `kda_change` source timestamp
  - zoom: transformed segment count and per-segment duration
  - copywriter: title, cover lines, and whether the title equals a raw leading
    subtitle excerpt (heuristic flag)
- Output a human-readable Markdown table plus a machine-readable JSON file
  under `data/processed/<session>/reports/`, overwriting per match on rerun.
- Threshold checks with a warnings section (initial defaults, all
  env-overridable): subtitle active ratio >=55%, max source gap <=45s, teaser
  1-3 segments, SFX <=6 hits, zoom 1-4 segments. Threshold violations set a
  non-zero exit code only when `--strict` is passed.
- No new heavyweight dependencies; reuse ffprobe helpers, plan/subtitle/export
  assets, and jsonl stores already in the codebase.

## Acceptance Criteria

- [ ] Running the command against the existing local sessions
      `session-20260617073649-4b5ec478` (m02) and
      `session-20260617073651-cf11bf9e` (m02-04) reproduces the 07-02 report
      numbers within rounding tolerance, without regenerating exports.
- [ ] JSON + Markdown artifacts are written under
      `data/processed/<session>/reports/` and never under the repo tree.
- [ ] Unit tests cover metric computation on synthetic fixture assets
      (no ffmpeg execution, no real recordings required).
- [ ] `--strict` exit-code behavior is tested.
- [ ] README gains a short usage section for the command.

## Notes

- Lightweight-to-medium task; PRD plus a short `implement.md` checklist is
  expected to be sufficient. Design doc optional.
- Sibling tasks will extend the report (e.g. SFX-to-kill deltas become
  meaningful after `07-06-sfx-precision-multikill`); keep metric extraction
  modular so fields can be added without reshaping the command.
