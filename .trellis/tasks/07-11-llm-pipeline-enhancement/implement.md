# Implement — LLM 全流程增强

## 1. Contracts and Configuration

- [x] Define stable candidate/evidence IDs and the expanded match semantic
      result models.
- [x] Add strict validation for score ranges, enums, known references, story
      consistency, teaser candidate IDs, and claim evidence.
- [x] Align title prompt and validator at 45 compact characters.
- [x] Add story-analysis, shadow-mode, semantic-weight, and schema-version
      settings with env overrides and safe defaults.
- [x] Add backward-compatible parsing/migration behavior for existing
      `CopywriterSemanticAsset` rows.

## 2. Split Highlight Planning

- [x] Extract deterministic candidate generation from final merge/protection/
      budget convergence without changing existing output.
- [ ] Persist or pass a candidate asset containing IDs, ranges, reasons, KDA
      protection metadata, and evidence references.
- [x] Keep the existing CLI/service behavior as a compatibility facade.
- [x] Add parity tests proving candidate+finalize with semantic weight zero is
      identical to the current planner.

## 3. One-Call Semantic Analysis

- [x] Expand prompt input with candidate IDs, KDA evidence, selected subtitle
      evidence, match metadata, and explicit editorial policy.
- [x] Expand the structured output with story status, primary angle,
      candidate-level scores/actions, evidence refs, and unified publishing
      copy.
- [x] Reject arbitrary timestamps and unknown references.
- [x] Include prompt/schema/model/input/config in cache fingerprints.
- [x] Preserve current retry, token-usage, cache, and deterministic fallback
      behavior.

## 4. Shadow Comparison

- [x] Compute proposed semantic ranking without mutating final plans when
      shadow mode is enabled.
- [x] Write a per-match comparison artifact/report with window changes,
      durations, protected coverage, story decision, teaser decision, and copy.
- [x] Surface semantic status and shadow results in status reporting.

## 5. Active Ranking Integration

- [x] Blend validated semantic scores into existing value-density calculation
      using a bounded configurable weight.
- [x] Ensure hard-protected spans, anchors, max gaps, and budgets cannot be
      bypassed.
- [x] Preserve chronological output order.
- [x] Implement `no_strong_story`: no forced teaser, neutral evidence-based
      copy, persisted reason, and no false quality warning.

## 6. Publishing Reuse

- [x] Make title, cover lines, summary, description, tags, and hook consume the
      same primary story and evidence references.
- [x] Validate factual claims against referenced deterministic evidence.
- [x] Fall back to safe heuristic copy when claims or story consistency fail.
- [x] Ensure only one LLM call is made per unchanged match across planning and
      publishing runs.

## 7. Automated Verification

- [x] Unit tests for semantic models, validation, cache invalidation, unknown
      references, unsupported claims, and 45-character titles.
- [ ] Planner parity tests with semantic weight zero / LLM disabled.
- [x] Budget and KDA-protection regression tests with extreme semantic scores.
- [x] Shadow-mode tests proving outputs do not alter edit plans or publishing.
- [x] Active-mode tests for drop and `no_strong_story`.
- [x] Full `pytest -q` regression suite.

## 8. Three-Sample Shadow Gate

- [x] Generate comparison reports for `4b5ec478` m02, `cf11bf9e` m03, and
      `bc90812b` m01 without exporting video.
- [x] Review story angle, event ranking, proposed omissions, teaser decision,
      title/cover alignment, duration, and protected coverage.
- [x] Adjust prompt/weights only with versioned cache invalidation.
- [ ] After plan approval, export one or two selected samples for subjective
      viewing; do not require all seven reference exports.

## 9. Rollout and Documentation

- [x] Keep shadow mode default-on for the first release.
- [ ] Document flags, cache behavior, fallbacks, evidence contracts, and
      operational cost.
- [ ] Update relevant Trellis specs and write a validation report.
- [ ] Obtain user approval before disabling shadow mode or starting active
      semantic influence in production.
