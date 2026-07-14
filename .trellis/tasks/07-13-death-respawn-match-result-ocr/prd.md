# Death, Respawn, and Match Result Recognition

## Goal

Add the first new shared-vision signals: player death/respawn state and confirmed victory/defeat result.

## Requirements

- Detect readable respawn countdowns with confidence and temporal monotonicity.
- Combine countdown, death-like visual evidence, and KDA death changes into bounded state events.
- Refine death start/respawn completion only around candidates.
- Detect Chinese-client victory/defeat text with temporal confirmation and versioned templates/text rules.
- Optimize initial crops/templates for the 1920x1080 Chinese-client layout; English and other client-language profiles are deferred.
- Feed death state into highlight wait trimming/continuity protection.
- Feed match result into boundary validation, ending context, and publishing facts without making OCR mandatory.
- Default both new detector consumers to shadow mode: persist detected events and proposed trim/end adjustments, but do not mutate production boundaries or highlight windows.

## Acceptance Criteria

- [x] Death-to-respawn state is stable across intermittent unreadable frames.
- [x] Long respawn waits produce a shadow trim proposal while death setup and reaction remain protected.
- [x] Result recognition produces a shadow match-end proposal and win/loss fact.
- [x] False single-frame result/countdown reads are rejected.
- [x] Detector failures preserve legacy behavior.
- [x] Shadow reports for at least three representative sessions include accepted/rejected evidence and proposed downstream changes before active rollout.

## Out of Scope

- Team score, objectives, level, items, and generic center-banner OCR.
