# Avoid Cutting Active Speech

## Goal

Make condensed edits finish the streamer's current spoken thought before a source-time jump, avoiding abrupt mid-sentence cuts such as `session-20260617073649-4b5ec478_match02` around rendered time `00:52`.

## Confirmed Facts

- Condensed highlight windows already pass through speech-boundary protection based on SRT cues.
- Speech cues separated by at most the configured speech-chain gap are treated as one continuous thought.
- The final duration-budget shrink stage caps post-shrink speech extension at `3.0s` per window (`condensed_budget_max_speech_extension_seconds`).
- A sentence or subtitle chain extending more than 3 seconds beyond the selected boundary can therefore still be cut while the streamer is speaking.
- Match start's short context marker is intentionally exempt and is not part of this change.

## Requirements

- A normal condensed content window must not end halfway through the current spoken sentence merely because finishing that sentence needs more than the old 3-second extension cap.
- Continuous commentary lasting tens of seconds remains trimmable at sentence boundaries; the planner must not treat all nearby speech as one indivisible span.
- Keep the condensed duration budget effective; speech completion must have a bounded safeguard against pathological ASR cues or near-continuous speech.
- Preserve KDA/combat protection, continuity bridging, match-edge validation, and existing teaser/export behavior.
- Add focused regression coverage for a speech chain that crosses the old 3-second extension cap.
- Reprocess or otherwise inspect the reported match-02 sample when the local pipeline assets needed for reproduction are available.

## Acceptance Criteria

- [x] A retained window ending during a normal multi-cue sentence (for example, after only “我今天中午” in “我今天中午还没有吃饭”) is extended through that sentence instead of cutting at the old 3-second cap.
- [x] Multiple sentences in a long stretch of continuous commentary can still be separated at a sentence boundary.
- [x] Pathological long or continuously merged speech cannot extend a window without a configured upper bound.
- [x] Existing condensed duration-budget, KDA preservation, continuity, and speech-boundary tests pass.
- [x] A new regression test demonstrates the former mid-sentence cut and passes with the fix.
- [x] The behavior and configuration contract are recorded in the backend editing/export spec.

## Out of Scope

- Changing Whisper transcription accuracy or subtitle display timing.
- Re-ranking highlight content or changing teaser selection.
- Removing the condensed duration budget.

## Product Decision

- Protect the current sentence, not an arbitrarily long run of continuous speech. Long commentary may be cut between sentences.
