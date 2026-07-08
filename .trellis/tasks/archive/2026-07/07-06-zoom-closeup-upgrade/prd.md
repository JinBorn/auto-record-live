# Zoom close-up upgrade

## Goal

Replace the single static full-segment punch-in with multiple short, eased
close-ups triggered by kill moments and chat-burst activity, emulating demo2's
emphasis cuts (chat banter close-ups, kill close-ups).

## User Value

Demo2 uses brief zoom emphasis on chat trash-talk and fight moments. Our
current zoom scales one whole segment (up to 30s) statically, which reads as a
rendering artifact rather than an editorial choice.

## Requirements

- Sub-segment close-ups: a close-up is a 3-8s window cut from within an
  eligible timeline segment around a trigger timestamp, not the whole
  segment. The timeline contract gains the ability to represent a segment
  split with per-piece transforms; exporter renders the pieces seamlessly
  (A/V sync preserved across the split).
- Triggers, in priority order:
  1. `kda_change` kill events (center-weighted default anchor or configured
     anchor),
  2. chat-burst moments: detect rapid chat-region change via the existing
     vision frame sampler over the chat box area (pixel-diff/text-density
     heuristic is acceptable for v1) -> chat-anchored close-up,
  3. existing reason-based eligibility as fallback when no triggers exist.
- Ease animation: scale ramps in/out over 0.3-0.5s at close-up boundaries
  (implementation approach — zoompan vs. piecewise scale expressions — decided
  in design.md); constant-scale plateau in between; scale bounds stay within
  the validated 1.0-1.5 range.
- Defaults under publish preset: max close-ups per export 3 (env; was 1),
  minimum spacing 25s, per-close-up duration cap 8s; disabled state and legacy
  single-segment behavior remain available via env for rollback.
- Anchor correctness: chat anchor verified against actual 1080p LoL layout on
  local recordings (chat bottom-left); anchors documented with a captured
  frame in the task research notes.
- Plan freshness checks must treat pre-change plans as stale so reruns
  regenerate them.

## Out Of Scope

- Object tracking or champion-following camera.
- OCR-driven reading of chat text content (density/change only).
- Facecam close-ups (no facecam in current recordings).

## Acceptance Criteria

- [ ] Regenerated validation samples contain >=2 close-ups per export where
      triggers exist, each <=8s, verified via the quality-report CLI.
- [ ] Ease-in/out is present (filter-level unit assertions + manual visual
      spot check on one export).
- [ ] A chat-burst fixture (synthetic frames with changing chat region)
      produces a chat-anchored close-up.
- [ ] A/V sync and total duration are unchanged by segment splitting
      (duration-sum tests; existing exporter tests keep passing).
- [ ] Legacy mode (single static segment zoom) reproducible via env for
      rollback comparison.

## Notes

- Complex task: `design.md` (timeline split contract, ease implementation,
  chat-burst detector) + `implement.md` before start.
- Independent of other wave-2 children; only depends on the quality-report
  CLI for measurement.
