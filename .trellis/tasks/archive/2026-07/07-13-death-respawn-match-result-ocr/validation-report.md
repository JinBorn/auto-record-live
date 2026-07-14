# Validation Report

## Automated

- Full suite before final real-session tuning: 753 passed.
- Detector tests cover monotonic countdown evidence, distant digit rejection, death-state gating, result multi-frame confirmation, ambiguity rejection, shadow proposals, reset/status visibility, and shared refinement budgets.

## Representative 1080p Shadow Runs

| Session | Outcome | Reviewed evidence |
|---|---|---|
| `session-20260617073649-4b5ec478` | rejected safely | no confirmed new-signal event; no trim proposal |
| `session-20260617073651-cf11bf9e` | accepted death/respawn | death `354.933s`, respawn `399.267s`; start showed ~43s countdown and end frame showed full HP at fountain |
| `session-20260702092321-bc90812b` | accepted death/respawn | death `227.933s`, respawn `244.733s`; start showed ~14s countdown and end frame showed full HP at fountain |

Latest streaming-run wall times were approximately 90s, 164s, and 192s depending on source/refinement count. Runtime memory remained roughly 290-383MB after both coarse and refined frame readers were converted to generators.

## Defects Found During Shadow Review

1. List-returning coarse/refined samplers retained gigabytes of 1080p frames (3.3-5.3GB observed). Both visual-analysis paths now stream frames; real rerun stayed below 400MB.
2. Broad countdown OCR read champion-select/gameplay digits and merged distant `1` values. Countdown sequences now require temporal continuity and meaningful decrease.
3. Generic color-based win/loss fallback reported early-game HUD colors as results. It was removed; missing Chinese OCR yields no result event.
4. Whole-screen grayscale released death state early when skills/shop stayed colorful. Death/respawn now uses the fixed player HP-bar region inside KDA-death-triggered refinement.
5. A wide HP crop was contaminated by green shop icons. The final 1080p crop is `(750,990,400,60)` with a 2% green-pixel dead threshold.

## Rollout Decision

Keep death/respawn and match-result consumers in shadow mode. Death/respawn timing is credible on the two accepted samples, but production trimming remains deferred to the integration/performance child. Match result stays evidence-empty on this machine because the Tesseract executable/Chinese language backend is unavailable; no heuristic fallback is allowed.
