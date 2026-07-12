# Implementation Plan

## 1. Baseline and Contracts

- [x] Capture the current plan windows and quality-report metrics for a representative
      match containing a kill/death or chase jump-cut complaint.
- [x] Confirm the backend spec and current config/asset contracts before editing.
- [x] Add focused regression tests that demonstrate an internal fight gap being removed by
      `_trim_low_value_internal_gaps` and by final budget shrinking.

## 2. Temporal Combat Evidence

- [x] Add a planner-internal combat activity sample model in the highlights layer.
- [x] Reuse the existing grayscale frame-difference convention without changing
      match-level density behavior.
- [x] Implement candidate-local temporal sampling with graceful video-unavailable
      fallback.
- [x] Add configuration loading/default tests for the enable switch, sample interval,
      enter/release thresholds, candidate lookaround, evidence gap tolerance, and safety
      cap.

## 3. Adaptive Interval Detection

- [x] Build anchors from KDA spans and combat-related key/tactical cues.
- [x] Implement hysteresis-based adaptive interval boundaries.
- [x] Merge phases separated by weak sustain evidence; do not bridge unrelated movement
      with no combat anchor.
- [x] Log interval count, protected duration, evidence mode, and safety-cap events.
- [x] Unit-test KDA protection, teamfight/chase without KDA, adaptive release,
      adaptive early/late release, and false-positive cases.

## 4. Planner Integration

- [x] Detect protected combat intervals once per condensed planning run.
- [x] Merge them with KDA protected spans through one shared helper.
- [x] Make internal-gap trimming unable to remove any protected combat time.
- [x] Make final budget shrinking unable to trim or drop protected combat time.
- [x] Include combat interval duration/count in protected-floor budget exceptions.
- [x] Keep feature-disabled behavior equivalent at the window-contract
      level where practical.

## 5. Quality Verification

- [x] Run focused highlight planner and config tests.
- [x] Run the complete test suite and compile check required by project specs.
- [x] Regenerate the selected real match's highlight plan in an isolated temp directory.
- [x] Verify source-gap limits remain valid and any duration
      excess is explicitly explained.
- [x] Verify the real plan contains a continuous video-informed combat protection span;
      KDA kill/death coverage remains covered by existing full-suite regressions.
- [x] Record the evidence in the task directory.

## Risk and Rollback Points

- Temporal frame sampling may increase planner runtime: keep it candidate-local and
  cached, measure the real sample, and avoid a full-match dense scan.
- High visual motion can produce false positives: require a combat anchor or sustained
  multi-signal evidence before starting protection.
- Excessive protected duration can inflate exports: retain the enable switch and expose
  budget exceptions rather than silently weakening continuity.
- If real-video validation shows unreliable visual boundaries, keep KDA/cue interval
  protection and disable the temporal visual contribution while preserving the cut
  exclusion plumbing.
