# Protect Combat and Chase Continuity

## Goal

Ensure condensed match videos preserve the understandable cause-and-effect chain of
player-versus-player encounters. Viewers must be able to see how the streamer killed
an opponent, was killed, or escaped a chase without source-time jump cuts removing
critical actions inside the encounter.

## User Value

Combat is the main narrative payoff of a match video. A shorter edit is not useful if
the viewer sees an engagement begin and then teleports directly to a kill, death, or
escape outcome without the decisions and actions that produced it.

## Confirmed Facts

- Condensed planning currently uses subtitle-derived cues plus synthetic KDA change
  cues to select retained source windows.
- Every detected KDA cue span is a hard-protected interval and the quality report
  targets zero uncovered KDA events.
- Kill and death windows already receive configurable preroll and postroll.
- The planner can extend a retained window for spoken action-resolution commentary,
  protect death-like continuity entries, and insert short continuity snippets to keep
  adjacent source jumps at or below 45 seconds.
- `_trim_low_value_internal_gaps` can still split a retained window when a long span
  has little subtitle signal and visual activity is below its preservation threshold.
  This can create a jump cut inside a fight, teamfight, or chase even when the final
  KDA event remains covered.
- The existing visual analyzer produces match-level activity metrics; it does not
  currently expose a source-time combat-state interval contract.
- The original intelligent-editing requirement already states that key events must be
  complete and must not be cut in the middle of a teamfight.

## Requirements

- Treat an active fight, teamfight, or chase as a continuity-protected source interval.
- Do not introduce source-time cuts inside a protected combat interval.
- Preserve the encounter from sufficient setup/context through a clear resolution:
  kill, death, disengage, or successful escape.
- Apply the rule to encounters that end without a KDA change, including failed ganks
  and escapes.
- Determine encounter resolution adaptively from the available temporal signals rather
  than ending protection after one fixed inactivity duration. Continued attack/damage,
  pursuit, elevated local visual activity, combat-related narration, or a pending KDA
  outcome may keep the encounter active; consistent signal decay may end it.
- Any fixed time threshold must be a configurable debounce or safety fallback, not the
  primary definition of combat resolution.
- Resolve ambiguous or conflicting signals conservatively: prefer retaining a modest
  amount of extra footage over cutting an encounter before its outcome is clear.
- Permit a cut only after multiple disengagement signals agree; one weak or missing
  signal must not end an otherwise active encounter.
- Combat continuity protection must survive internal-gap trimming and final duration
  budget shrinking.
- When protected combat content makes the duration budget impossible, preserve story
  clarity and record an explicit budget exception instead of silently cutting the
  encounter.
- Keep existing guarantees for KDA coverage, speech boundaries, match-edge anchors,
  and export compatibility.

## Acceptance Criteria

- [ ] A retained fight ending in a streamer kill contains no source-time discontinuity
      between engagement setup and the confirmed kill resolution.
- [ ] A retained fight ending in the streamer's death contains no source-time
      discontinuity between engagement setup and the confirmed death resolution.
- [ ] A retained chase/escape with no KDA change contains no source-time discontinuity
      before disengagement or escape is understandable.
- [ ] A long non-combat low-value interval can still be trimmed; this feature does not
      disable condensed editing globally.
- [ ] Internal-gap trimming and final budget shrinking cannot cut through a protected
      combat interval.
- [ ] If protected intervals exceed the normal budget, the plan records a specific
      protected-combat budget exception.
- [ ] Existing KDA-uncovered, speech-boundary, maximum-source-gap, and duration tests
      remain green.
- [ ] New focused tests cover kill, death, escape/no-KDA, false-positive/non-combat,
      and over-budget cases.
- [ ] At least one representative real match is regenerated and visually reviewed for
      combat continuity before completion.

## Out of Scope

- Replacing the entire highlight-ranking model.
- Preserving every second of all combat-adjacent movement regardless of relevance.
- Adding game-client telemetry or invasive capture hooks unless repository evidence
  shows the existing video/subtitle/KDA signals cannot meet the acceptance criteria.

## Open Questions

- None currently blocking product planning.
