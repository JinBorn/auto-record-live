# Validation Report — Human Review Fixes

## Outcome

The automated implementation and quality gates pass. Seven exports were
generated in the initial batch, and three representative samples were fully
regenerated after the final subtitle/KDA-timing corrections. The final
representative reports contain zero warnings. Subjective viewing by Jinson is
still required before the task can be archived.

## Feedback Coverage

1. **Kill SFX timing and gain** — Implemented at `-7dB`. The original planned
   fixed `-1.5s` offset was superseded after real-video diagnosis: publish mode
   now refines each coarse KDA change to the first stable video frame and uses
   zero fixed offset. Unconfirmed transitions omit the decorative SFX.
2. **At most two subtitle rows** — Enforced by clamping cue `i` to cue `i+2`'s
   start; regression tests cover native overlap and smoothing-created overlap.
3. **No stale/early subtitles** — Final publish defaults preserve source timing
   (`min_duration=0`, `max_gap_fill=0`) with a short trailing hold. This
   supersedes the intermediate 2.5s/1.5s smoothing proposal after visual review.
4. **Skip added BGM when source music dominates** — Majority threshold changed
   from `0.60` to `0.35`; span-level avoidance remains intact.
5. **One cover** — `cover_max_candidates=1` by default, env-overridable.
6. **Zoom timing** — Unanchored fallback zooms are disabled by default; KDA and
   chat-burst anchors remain.
7. **Bad teaser may be omitted** — Weak semantic teaser suggestions are
   rejected. Main-only plans persist `teaser_omitted_reason`, and the quality
   report treats a recorded omission as intentional.
8. **Longer titles** — Prompt and schema limit increased to 45 compact
   characters; cover-line and summary limits are unchanged.

## Automated Verification

- `python -m pytest -q`: **723 passed** in 79.56s.
- `python -m compileall -q src tests`: **passed**.
- `git diff --check`: **passed**.
- Initial seven-sample export batch completed. Five reports were already clean;
  two fresh-streamer samples only missed the former artificial subtitle-active
  threshold (52.0% and 53.1%). The final threshold is 40% after removing stale
  subtitle holds.
- Final representative full-chain reruns:

| Sample | Export | Subtitle active | Teaser | SFX | Zoom | Warnings |
|---|---:|---:|---:|---:|---:|---:|
| `session-20260617073649-4b5ec478` m02 | 11.11 min | 40.3% | 0 | 6 | 3 | 0 |
| `session-20260617073651-cf11bf9e` m03 | 11.61 min | 54.2% | 0 | 2 | 3 | 0 |
| `session-20260702092321-bc90812b` m01 | 12.42 min | 66.6% | 0 | 2 | 3 | 0 |

All three have maximum adjacent source gap `45.0s`, zero uncovered KDA events,
and zero quality-report warnings. The first sample was also rechecked afterward
against current assets at 40.9% subtitle activity with zero warnings.

## Remaining Human Gate

Jinson should watch representative sections and confirm:

- kill SFX lands on the visible kill moment and is loud enough;
- subtitles no longer linger or stack beyond two rows;
- zooms only occur on meaningful moments;
- main-only openings feel better than weak teasers;
- the single cover and longer title are acceptable.

Do not archive the task until this subjective review is acknowledged.
