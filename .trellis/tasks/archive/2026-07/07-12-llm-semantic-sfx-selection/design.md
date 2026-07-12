# Design: LLM-Guided Semantic SFX Selection

## Summary

Extend the existing one-call copywriter semantic analysis with bounded SFX
recommendations. Deterministic code discovers source-time candidates and library
categories; the LLM may choose one category or `none` for each known candidate. The
edit planner later maps accepted source candidates into rendered timeline timestamps
and combines them with existing deterministic effects under one safety/rate-limit
pass.

The LLM never controls timestamps, file paths, gain, spacing, or total count.

## Architecture and Ownership

```text
SFX manifest
  -> shared category catalog (category + description + usable path state)

subtitle/KDA/highlight evidence
  -> deterministic semantic SFX candidate discovery
  -> stable candidate/evidence IDs
  -> existing copywriter semantic prompt (one call per match)
  -> validated SFX recommendations persisted on CopywriterSemanticAsset

edit timeline
  -> map accepted source candidate IDs into rendered seconds
  -> resolve selected category to a loaded SFX track
  -> merge with kill/transition/teaser deterministic hits
  -> dedupe, category limits, spacing, total cap
  -> EditPlanAsset.sound_effects
```

### Copywriter semantic layer owns

- prompt candidate/evidence construction;
- presenting only available/allowed categories plus `none`;
- schema validation and rejection of unknown IDs/categories/evidence;
- persistence and shadow reporting.

### Edit planner owns

- source-to-rendered-time mapping;
- active/shadow feature gating;
- library track/path/gain resolution;
- deterministic-effect priority;
- final spacing and count limits.

### Exporter owns

No new behavior. It continues to validate and mix `SoundEffectHit` rows.

## Shared SFX Catalog

Move or expose SFX manifest parsing through a dependency-neutral shared module so the
copywriter can inspect categories without importing `arl.editing` (editing already
depends on copywriter semantic models).

Catalog entries contain:

- `category`;
- resolved `path` for runtime selection;
- optional `gain_db`;
- optional human-readable `description` for the LLM prompt.

Arbitrary manifest category names remain supported. Reserved deterministic categories
(`kill_coin`, `multi_kill`, `transition_whoosh`, `teaser_impact`) are shown to the LLM
only if policy explicitly allows them; MVP semantic selection excludes them to avoid
competing with existing deterministic rules.

## Candidate Discovery

Create stable source-time `SemanticSfxCandidate` rows before the LLM call. Candidates
must be based on durable evidence, not free-form timestamps:

- subtitle cues with streamer-centric mistake/reaction/impact/projectile language;
- existing key/tactical windows with nearby subtitle evidence;
- KDA outcomes used only as context and never to create an optional semantic duplicate
  of a deterministic coin/multi-kill hit;
- existing semantic candidate/evidence IDs when the same moment is already represented.

Each candidate contains:

- stable ID from session, match, source start/end, and evidence IDs;
- bounded source start/end and anchor timestamp;
- short evidence text and evidence references;
- deterministic signal hints, not a preselected effect category.

Discovery is conservative and streamer-centric. High visual motion without text/KDA
attribution does not create a candidate in MVP.

## LLM Contract

Add an optional recommendation list to the existing semantic result:

```python
class SemanticSfxRecommendation(BaseModel):
    candidate_id: str
    category: str  # available category or "none"
    confidence: float
    evidence_refs: list[str]
    reason: str
```

Validation rules:

- candidate ID must exist exactly once;
- category must be `none` or a prompt-listed, policy-allowed, usable category;
- evidence refs must belong to that candidate or known match evidence;
- confidence is clamped/validated to `[0, 1]`;
- duplicate recommendations for a candidate are rejected;
- missing recommendations mean `none`;
- malformed SFX recommendations may be dropped without rejecting otherwise valid
  publishing semantics, unless structural corruption makes the whole response unsafe.

The prompt instructs the model to prefer `none`, remain streamer-centric, and never
infer a timestamp.

## Activation and Shadow Mode

Add independent SFX semantic controls rather than coupling rollout to all LLM story
semantics:

- semantic SFX enabled;
- semantic SFX shadow mode (default on initially);
- minimum confidence (recommended default `0.80`);
- maximum optional semantic hits per match (default `2`);
- minimum spacing from any other SFX;
- per-category maximum (default `1` for comedic/reaction categories).

Shadow mode persists proposed decisions and rejection reasons but does not modify
`EditPlanAsset.sound_effects`.

## Edit-Plan Mapping and Priority

For each validated active recommendation:

1. Find timeline segments containing the deterministic source anchor.
2. Prefer the main occurrence over teaser replay for optional semantic effects.
3. Map source time into rendered output seconds.
4. Reject candidates outside retained content.
5. Reject candidates within the configured spacing of deterministic KDA, transition,
   or teaser effects.
6. Resolve the category to an exact available library track.
7. Apply track gain with existing `[-60, 6]` clamping.
8. Rank remaining optional effects by confidence, evidence strength, and stable source
   order; accept at most two total and one per category by default.

Deterministic effects always win conflicts. Optional semantic effects never fall back
to `kill_coin`; a missing selected category means skip.

## Compatibility and Failure Behavior

- All new semantic fields are additive and default to empty lists for legacy assets.
- LLM disabled/unavailable/invalid: current deterministic SFX behavior is unchanged.
- SFX library missing: no optional semantic candidates are activated.
- Shadow mode: edit plans remain byte-equivalent in their sound-effect list.
- Existing semantic cache fingerprints include the catalog/candidate contract so a
  changed library or prompt schema causes recomputation.
- No machine cues are written into SRT files.

## Observability

Persist/report:

- candidate count and available categories;
- proposed `category/none`, confidence, and evidence refs;
- validation rejection reason;
- timeline mapping rejection reason;
- final accepted optional effects;
- conflicts with deterministic effects;
- per-match shadow summary.

## Validation Strategy

- Schema and adversarial-response tests for unknown IDs/categories/evidence.
- Candidate discovery tests for clear streamer mistake/reaction and non-streamer
  negatives.
- Mapping tests for main-vs-teaser, trimmed gaps, and deterministic-hit conflicts.
- Library tests for arbitrary categories/descriptions and missing files.
- LLM failure/cache/shadow compatibility tests.
- Representative real-match shadow reports reviewed before active mode is enabled.

