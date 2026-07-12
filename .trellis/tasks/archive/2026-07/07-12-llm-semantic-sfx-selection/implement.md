# Implementation Plan

## 1. Baseline and Shared Catalog

- [x] Capture current SFX plans and semantic assets on representative matches.
- [x] Expose semantic SFX manifest parsing through a shared dependency-neutral module.
- [x] Preserve arbitrary category support and ignore missing/unusable files.
- [x] Add optional `description` support and catalog tests.

## 2. Semantic Candidate Contract

- [x] Add stable semantic SFX candidate IDs and prompt evidence rows.
- [x] Implement conservative streamer-centric candidate discovery from subtitles,
      KDA context, and retained/highlight evidence.
- [x] Add positive mistake/impact fixtures and teammate/opponent
      negative fixtures.

## 3. One-Call LLM Schema and Validation

- [x] Add optional SFX recommendations to copywriter semantic models and shared views.
- [x] Extend the existing prompt with available categories, descriptions, candidates,
      `none`, confidence, and streamer-centric rules.
- [x] Validate candidate/category/evidence IDs and duplicate decisions.
- [x] Include SFX catalog/candidates/schema in semantic input/prompt fingerprints.
- [x] Keep unrelated publishing semantics usable when only SFX rows are invalid.

## 4. Shadow Reporting

- [x] Add SFX-specific enable/shadow/confidence/count/spacing/category-limit settings.
- [x] Persist proposed/rejected shadow decisions with reasons.
- [x] Ensure shadow mode does not change `EditPlanAsset.sound_effects`.

## 5. Edit Planner Activation

- [x] Map accepted source anchors into retained main timeline positions.
- [x] Resolve selected categories without fallback to coin effects.
- [x] Merge deterministic and optional candidates under deterministic-first conflict
      handling, spacing, per-category limit, and total optional cap.
- [x] Preserve existing kill/multi-kill/transition/teaser behavior and exporter contract.

## 6. Verification and Rollout

- [x] Run focused copywriter, editing, config, and semantic contract tests.
- [x] Run full pytest and compile checks.
- [x] Validate shadow-asset generation end-to-end with a bounded fake provider.
- [x] Scan representative real subtitles and record candidate/false-positive
      observations in the task directory.
- [x] Keep production activation off until real-LLM shadow review is accepted.

## Risk and Rollback

- Prompt/schema growth may invalidate semantic caches: version/fingerprint the change.
- Overuse can make edits noisy: default to `none`, confidence >=0.80, max two optional
  hits, one per category, deterministic effects win.
- Category semantics may vary by user library: expose manifest descriptions to the LLM
  and reject missing categories at activation time.
- Roll back by disabling semantic SFX; deterministic plans remain unchanged.
