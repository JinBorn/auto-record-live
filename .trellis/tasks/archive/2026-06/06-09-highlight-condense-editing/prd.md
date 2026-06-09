# Improve highlight condense editing

## Goal

Improve post-recording editing so final exported videos are not just whole
match-length recordings. The pipeline should:

- end a match export at the actual game-ending moment when a reliable
  post-game / nexus-destroyed signal is available;
- optionally condense low-value gameplay stretches, especially jungler
  farming/pathing with no meaningful fight, objective, or live narration;
- keep the unattended pipeline conservative enough that it does not fabricate
  a highlight edit when signals are weak.

## Requirements

- The MVP reference case is `session-20260609062446-e18326fb_match01.mp4`.
  User observation: the game ends at `21:54` when the nexus/base explodes, but
  the current export continues until the manually supplied next `in_game` hint
  boundary at `30:00`.
- The pipeline must support using `post_game` / game-over signals as match end
  anchors. An `in_game` start plus later `post_game` signal should produce a
  boundary ending near the post-game signal instead of the next `in_game` hint
  or recording duration.
- The pipeline must preserve current safety behavior for weak signals: when no
  reliable `in_game` or edit signal exists, it should still defer instead of
  exporting an unreviewed full recording.
- Highlight condensation must target user-facing publishability: keep fights,
  kills/deaths, objective moments, tower/base pressure, strong live narration,
  and game-ending moments; remove or shorten low-action jungler farming/pathing
  when there is no notable live narration.
- The output should remain discoverable through the existing generated artifact
  manifests so `arl status`, copywriting, and reset flows continue to work.

## Confirmed Facts

- Current `SegmenterService` derives match boundaries only from `in_game`
  starts. Non-`in_game` hints, including `post_game`, are ignored when building
  boundary ends.
- Current boundary behavior uses the next `in_game` start as the prior match's
  end; if no later `in_game` exists, the boundary ends at recording duration.
- `stage_text` and subtitle-derived stage signals already know the
  `post_game` stage and include English/Chinese game-over keywords.
- Current exporter defers low-confidence full-recording fallback boundaries,
  which is the desired safety behavior for sessions without reliable edit
  signals.
- There is no existing artifact or service for "condensed highlight windows" or
  ffmpeg concat-based highlight exports.
- This task is created while an unrelated H.265 export feature change is still
  uncommitted in the working tree; implementation should avoid mixing concerns
  unless the user explicitly wants both committed together.

## Out of Scope

- Full computer-vision understanding of League of Legends UI in the first
  iteration.
- ML-based fight detection trained from gameplay video.
- Perfect automatic editor decisions for every streamer/game style.
- Replacing the existing conservative defer behavior for no-signal sessions.

## Open Questions

- Resolved: first implementation should use a conservative strategy:
  - fix game-end trimming using `post_game` / game-over signals;
  - add conservative highlight condensation for obviously low-value downtime;
  - avoid aggressive automatic cuts that would create jarring transitions or
    remove important live narration.

## Acceptance Criteria

- [x] Given an `in_game` start and a later `post_game` hint/signal for the same
      match, segmenter emits a boundary ending at the `post_game` time instead
      of the next `in_game` hint or recording duration.
- [x] The reference sample can be represented so match 1 ends around `21:54`
      rather than `30:00`.
- [x] Existing no-signal sessions still produce low-confidence fallback
      boundaries and remain deferred by exporter unless explicitly forced.
- [x] If highlight condensation is included in this task, the condensed export
      is manifest-backed, idempotent, resettable, and test-covered.
- [x] Tests cover multi-match sessions with both `in_game` and `post_game`
      hints, including ordering and out-of-range signal behavior.
- [x] Conservative condensation avoids hard-to-follow jump cuts by adding
      configurable padding around retained moments and by preserving enough
      context between adjacent retained windows.

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
