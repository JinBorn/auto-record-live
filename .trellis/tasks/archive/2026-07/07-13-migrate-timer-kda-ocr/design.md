# Design

Wrap existing `read_timer` and `read_kda` as shared-stage detector adapters without changing crop/OCR algorithms. Persist coarse readings; derive KDA changes/refinement events in the visual stage using the current plausibility and stable-frame rules.

Segmenter and highlight planner read a shared asset view filtered to their source/match ranges. Timer consumer preserves its scene/refinement inputs; KDA consumer maps generic events back to compatibility `ClassifiedCue` and `HighlightPlanAsset.kda_events` shapes. Missing/incompatible/degraded detector sections trigger the legacy direct scan during rollout.
