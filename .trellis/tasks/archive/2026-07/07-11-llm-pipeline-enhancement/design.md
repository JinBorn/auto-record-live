# Design — LLM 全流程增强

## 1. Current State

The post-process order is:

`subtitles -> highlight-planner -> copywriter-semantic -> edit-planner -> exporter -> copywriter-publishing`

The existing `CopywriterSemanticAsset` is already cached per match and consumed
by edit planning and publishing, but its result is publishing-centric. It has
titles, cover lines, summary, description, tags, hook text, and teaser source
ranges; it does not provide candidate-level story semantics or a durable primary
story contract.

## 2. Target Architecture

Keep one LLM call per match and expand the semantic asset into a shared contract
consumed by highlight planning, edit planning, and publishing.

Because deterministic highlight candidates must exist before the call, split
highlight planning into two deterministic phases:

1. `highlight-candidates`: produce candidate windows and hard-protection facts.
2. `semantic-analysis`: call the LLM once using subtitles, candidate windows,
   KDA facts, match metadata, and compact evidence excerpts.
3. `highlight-finalize`: apply semantic scores in shadow or active mode, then
   run existing merge, protection, continuity, and duration-budget logic.
4. `edit-planner` and `copywriter-publishing`: consume the same semantic asset.

The public post-process order becomes:

`subtitles -> highlight-candidates -> semantic-analysis -> highlight-finalize -> edit-planner -> exporter -> copywriter-publishing`

Compatibility option: keep the existing `highlight-planner` CLI as an
orchestrating facade over candidate + finalize phases.

### Implemented compatibility shape

To avoid duplicating the large condensed discovery/repair pipeline, the first
deterministic `HighlightPlanAsset` is used as the durable stable-candidate
snapshot. The one LLM call references stable IDs derived from those windows.
When active mode is enabled, postprocess performs one forced
`highlight-finalize` pass after semantic analysis. Candidate discovery remains
deterministic; semantic overlap multipliers are applied only inside the final
budget shrink value-density calculation. Shadow mode skips the second pass.

This preserves the existing CLI and JSONL contract while providing the planned
two-stage execution boundary. Weight zero produces no semantic references and
therefore follows the legacy finalizer path.

## 3. Shared Semantic Contract

Replace the publishing-only result shape with an additive structured result.
Names are illustrative; exact model names should follow repository conventions.

### Match story

- `story_status`: `strong_story | no_strong_story`
- `primary_angle`: concise factual story description or `None`
- `story_reason`: why this angle is supported
- `story_event_ids`: ordered references to deterministic candidate/event IDs
- `narrative_summary`: chronological cause -> event -> payoff summary

### Candidate semantics

For every candidate ID:

- `importance_score`: normalized 0-1
- `story_relevance_score`: normalized 0-1
- `emotion_score`: normalized 0-1
- `instructional_score`: normalized 0-1
- `outcome_clarity_score`: normalized 0-1
- `recommendation`: `keep | shorten | drop`
- `reason`: concise explanation
- `evidence_refs`: subtitle cue IDs and/or KDA event IDs

The LLM cannot return arbitrary timestamps. Unknown candidate IDs, evidence IDs,
or unsupported enum values invalidate the result.

### Publishing package

- three title candidates and one recommendation;
- cover lines, summary, description, tags, and hook line;
- teaser candidate IDs rather than free-form source timestamps;
- `claim_evidence`: factual title/cover claims mapped to event/evidence IDs.

All publishing fields must describe the selected `primary_angle`. When
`story_status=no_strong_story`, teaser guidance is empty and copy is neutral.

Fix current title contract drift: prompt and validator must both allow at most
45 compact characters.

## 4. Candidate Identity and Evidence

Candidate windows need stable IDs derived from deterministic input fields, for
example a hash of session, match, start, end, reason, and detector version.
Subtitle cues and KDA events likewise need stable evidence IDs in the prompt.
The persisted semantic asset stores only validated references.

This prevents timestamp hallucination and lets downstream code audit every LLM
decision against source evidence.

## 5. Ranking and Budget Integration

Semantic scores augment, but never replace, deterministic value density.

- Hard-protected KDA spans, boundary anchors, source-gap constraints, and budget
  limits remain unchanged.
- Apply a configurable semantic multiplier/bonus only to otherwise trimmable
  candidate value.
- `drop` makes a candidate an early trim target; it does not delete hard-
  protected spans.
- Preserve chronological output order. LLM story order is explanatory, not a
  request for non-linear editing.
- Clamp all scores and weights to configured ranges.

## 6. Shadow Mode and Rollout

Add settings such as:

- `ARL_LLM_STORY_ANALYSIS_ENABLED`
- `ARL_LLM_STORY_SHADOW_MODE` (default `true` initially)
- `ARL_HIGHLIGHT_SEMANTIC_WEIGHT`

In shadow mode:

- generate and cache the semantic asset;
- compute proposed keep/shorten/drop decisions and proposed publishing copy;
- do not alter final highlight windows or published package;
- emit a comparison report containing current vs proposed windows, duration,
  protected-event coverage, primary story, teaser decision, and copy.

After human approval, disable shadow mode to let semantic values influence the
existing deterministic finalizer. Rollback is the feature flag or weight zero.

## 7. Cache and Invalidation

Keep one LLM call per match. The input fingerprint must include:

- selected subtitle cues and their IDs/text/times;
- deterministic candidate IDs, ranges, reasons, and protected flags;
- KDA evidence;
- streamer name and available match metadata;
- model, schema version, prompt version, and relevant scoring configuration.

Exporter-only or rendering-only changes do not invalidate semantic analysis.

## 8. Failure and Degraded Operation

If the LLM is disabled, unavailable, times out, returns invalid JSON, references
unknown evidence, or makes unsupported claims:

- keep the current deterministic highlight and publishing fallback paths;
- record a structured failure reason;
- never block export solely because semantic analysis failed.

`no_strong_story` is a valid successful result, not a provider failure.

## 9. Validation

Initial shadow set:

1. `session-20260617073649-4b5ec478`, match 02 (mandatory reference).
2. `session-20260617073651-cf11bf9e`, match 03 (messy-teaser / no-story case).
3. `session-20260702092321-bc90812b`, match 01 (different streamer and long,
   protected-content-heavy match).

Plan-level comparison runs on all three without export. After review, export
only one or two approved samples for subjective viewing.

Acceptance checks include unchanged KDA coverage and hard constraints, stable
cache reuse, evidence-valid copy, coherent story/title/cover alignment, correct
`no_strong_story` behavior, and deterministic fallback on LLM failure.

Post-export LLM review is explicitly deferred.
