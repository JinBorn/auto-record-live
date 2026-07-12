# Design: Adaptive Combat Continuity Protection

## Summary

Add a source-time combat continuity layer to condensed highlight planning. The layer
derives protected encounter intervals from existing deterministic evidence and a new
local temporal visual-activity signal. These intervals become hard cut exclusions for
both internal-gap trimming and final budget shrinking.

The design intentionally does not treat one fixed timeout as the encounter boundary.
It uses hysteresis: strong evidence starts or sustains an encounter, weaker recent
evidence keeps it alive, and several agreeing disengagement conditions are required to
end it.

## Existing Pipeline Boundary

The change belongs in `arl.highlights`, after cues/KDA events are available and before
any operation that may split or shrink retained windows:

```text
subtitle classification + KDA detection
  -> initial window optimization
  -> adaptive combat interval detection
  -> action-resolution / speech / bridge repair
  -> internal-gap trimming (combat-aware)
  -> restore/repair fixpoint
  -> final budget shrinking (combat-aware)
  -> HighlightPlanAsset
```

No exporter or edit-timeline contract changes are required. The planner still emits
ordinary `HighlightClipWindow` values.

## Data Model

Introduce internal planner-only value objects:

- `CombatSignalSample`: source timestamp plus normalized visual activity and evidence
  flags from cues/KDA anchors.
- `ProtectedCombatInterval`: start/end, confidence, and evidence reasons used for
  diagnostics and tests.

These are not persisted cross-stage in the first implementation. Plan logs and budget
exception text expose aggregate diagnostics. Persistence can be added later if the
quality report needs direct interval auditing.

## Signal Sources

### Strong start/sustain signals

- KDA event spans and their existing preroll/postroll context.
- Subtitle cues classified as key events or tactical combat cues (`fight`, `teamfight`,
  `gank`, and configured equivalents).
- Candidate-local temporal visual activity above the strong threshold.

### Weak sustain signals

- Narration continuing shortly after a strong combat cue.
- Visual activity above a lower release threshold.
- Short evidence gaps surrounded by strong activity, representing repositioning,
  brush/vision loss, crowd control, or chase pathing.

### Disengagement evidence

- Visual activity remains below the release threshold across multiple samples.
- No KDA, combat cue, or combat-related narration is pending nearby.
- The weak-evidence grace budget has decayed.

No single weak signal ends an encounter. Missing subtitles are neutral rather than
proof of disengagement.

## Temporal Visual Analysis

Refactor/reuse the existing frame-difference primitives in `visual_analyzer.py` to
support local time buckets instead of only one match-level aggregate score.

- Analyze only candidate neighborhoods around retained key/tactical windows and KDA
  anchors, avoiding a mandatory dense scan of the full match.
- Sample at a configurable short interval suitable for encounter boundaries.
- Compute a normalized local activity score from scene/frame change, minimap activity,
  and edge-density change using the existing weighting convention.
- Cache samples per recording/boundary within one planning run so overlapping
  candidate neighborhoods do not decode frames twice.
- If video is unavailable or decoding fails, degrade to cue/KDA-only protection and log
  the degraded evidence mode. Planning must still succeed.

## Adaptive State Machine

Use two activity thresholds (`enter > release`) and accumulated evidence rather than a
fixed 8-second rule:

1. `idle -> active`: enter on a strong anchor or sustained strong visual samples.
2. `active -> resolving`: strong evidence has stopped, but weak evidence/recent combat
   history still indicates the encounter may continue.
3. `resolving -> active`: any renewed strong evidence reopens the same interval.
4. `resolving -> idle`: close only after multiple consecutive low-activity samples and
   no nearby cue/KDA evidence.

Configurable seconds control sampling, lookaround, and maximum safety bounds, but the
actual end varies with evidence. A generous maximum interval is only a failsafe against
pathological false positives; reaching it is logged.

Nearby protected intervals are merged when the gap contains weak sustain evidence.
This preserves teamfight phases and chases with brief repositioning without merging
unrelated lane movement globally.

## Cut Protection Contract

Create one merged `protected_spans` set from:

- full KDA cue spans;
- adaptive protected combat intervals.

Pass the same set to both destructive stages:

- `_trim_low_value_internal_gaps` subtracts all protected spans from removable gaps.
- `_shrink_windows_to_budget` treats all protected spans as untrimmable, including when
  ranking low-density windows for removal.

Any later stage added after combat detection must either preserve these spans or rerun
the protection validation before asset construction.

## Budget and Failure Behavior

- Story continuity wins over the normal duration budget.
- If the plan cannot converge because combat intervals form the protected floor,
  `budget_exception_reason` explicitly reports combat-protected duration and interval
  count, alongside KDA protection details.
- Visual-analysis failure never fails highlight planning; it reduces confidence and
  falls back to deterministic cue/KDA evidence.
- The feature has an enable switch for rollback. Disabling it restores current planner
  behavior without changing stored assets.

## Compatibility

- Additive settings only; existing environments remain valid.
- No schema migration for `HighlightPlanAsset` in the first implementation.
- Existing match-level visual density analysis remains available and should share
  low-level helpers with the temporal analyzer rather than duplicate frame math.

## Validation Strategy

- Unit-test state transitions with synthetic signal samples.
- Planner tests verify protected spans survive internal trimming and budget shrinking.
- Regression tests cover cue/KDA-only fallback and unrelated high-motion/non-combat
  footage.
- Regenerate at least one existing real sample with known combat jumps; compare plan
  windows, duration, KDA coverage, maximum source gap, and the rendered encounter.

