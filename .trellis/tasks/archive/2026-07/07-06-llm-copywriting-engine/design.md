# LLM copywriting engine design

## Architecture

The copywriting upgrade is a two-phase copywriter design:

1. **Semantic hint phase, before edit-planner**
   - Reads subtitle assets, match boundaries, highlight plans, recording/session metadata, and optional KDA cues.
   - Calls an OpenAI-compatible chat-completions provider when enabled and configured.
   - Persists one durable semantic result per `(session_id, match_index)` before edit-planner runs.
   - The edit-planner consumes only the teaser recommendation subset from this asset.

2. **Publishing package phase, after exporter**
   - Keeps the existing `CopywriterService` responsibility of writing `CopyAsset`, `PublishingPackage`, cover output, and published package aliases.
   - Reuses the cached semantic result when present and valid.
   - Falls back to the current heuristic copy generation when LLM is disabled, unavailable, or schema validation fails.

This avoids making edit-planner depend on final export/copy assets while still allowing LLM teaser hints to influence teaser selection.

## Module Boundaries

- `src/arl/config.py`
  - Owns all `ARL_LLM_*` environment parsing and validation.
- `src/arl/copywriter/models.py`
  - Owns durable Pydantic payloads for LLM semantic results, teaser recommendations, provider metadata, and copywriter state additions.
- `src/arl/copywriter/llm.py` or equivalent copywriter-local module
  - Owns the OpenAI-compatible client, fake-provider seam, retry handling, response parsing, and schema validation.
- `src/arl/copywriter/service.py`
  - Owns orchestration, prompt input assembly, cache behavior, heuristic fallback, and package generation.
- `src/arl/postprocess/service.py`
  - Runs the semantic hint phase after `highlight-planner` and before `edit-planner`, then runs the publishing package phase after `exporter`.
- `src/arl/editing/service.py`
  - Reads semantic teaser hints as an optional signal. Existing highlight/subtitle selection remains the fallback.

No new heavyweight dependencies are introduced. HTTP calls should use the existing `httpx` dependency.

## Data Flow

```text
subtitles + boundaries + highlight plans + recording/session metadata
  -> copywriter semantic phase
  -> data/tmp/copywriter-semantic-assets.jsonl
  -> edit-planner teaser selection
  -> exporter
  -> copywriter publishing phase
  -> CopyAsset + PublishingPackage + cover/publish files
```

## Durable Contracts

Add a semantic asset JSONL manifest under `data/tmp/`:

```text
data/tmp/copywriter-semantic-assets.jsonl
```

Suggested model shape:

```python
class TeaserRecommendation(BaseModel):
    source_start_seconds: float
    source_end_seconds: float
    hook_reason: str

class LlmCopywritingResult(BaseModel):
    title_candidates: list[str]
    recommended_title: str
    cover_lines: list[str]
    summary: str
    description: str
    tags: list[str]
    hook_line: str | None = None
    teaser_recommendations: list[TeaserRecommendation] = Field(default_factory=list)

class CopywriterSemanticAsset(BaseModel):
    session_id: str
    match_index: int
    source_subtitle_path: str
    source_highlight_plan_path: str | None = None
    provider: str
    model: str
    prompt_fingerprint: str
    input_fingerprint: str
    result: LlmCopywritingResult
    token_usage: dict[str, int] = Field(default_factory=dict)
    status: str
    created_at: datetime
```

The status should distinguish `generated`, `fallback`, and invalid-provider paths if a row is persisted for fallback diagnostics.

## Provider Contract

Environment keys:

- `ARL_LLM_ENABLED`, default `0`
- `ARL_LLM_BASE_URL`
- `ARL_LLM_API_KEY`
- `ARL_LLM_MODEL`, default `deepseek-chat`
- `ARL_LLM_TIMEOUT_SECONDS`, default `30`
- `ARL_LLM_MAX_RETRIES`, default `2`
- `ARL_LLM_MAX_INPUT_CUES`, default chosen conservatively after implementation inspection

The provider sends an OpenAI-compatible request:

```http
POST {base_url}/chat/completions
Authorization: Bearer <api_key>
Content-Type: application/json
```

The prompt requires JSON-only output. The service validates JSON with Pydantic and retries bounded times on invalid JSON/schema mismatch.

## Prompt Input Assembly

Input includes:

- streamer name when known
- session id and match index
- match duration
- subtitle cues with timestamps
- highlight windows with reasons
- KDA cue summaries when available

Truncation must keep:

- cues overlapping highlight windows
- cues around recommended/high-priority windows
- head and tail context
- enough surrounding text for title/summary coherence

The prompt must explicitly reject raw leading subtitle excerpts as titles and require concise simplified-Chinese upload copy.

## Caching

The semantic phase skips an LLM call when a latest semantic asset exists for the same `(session_id, match_index)` and the input/prompt/model fingerprint matches.

`force_reprocess=True` bypasses the cache and appends/replaces a fresh semantic result, matching existing stage reprocess behavior.

The publishing package phase should not re-call the provider when a valid semantic asset exists.

## Fallback

If LLM is disabled, missing `ARL_LLM_API_KEY`, network/auth fails, or schema validation remains invalid after retries:

- Log one compact copywriter warning.
- Use existing heuristic copy generation.
- Do not crash postprocess.
- Keep heuristic phrase tables, but treat them as fallback-only in naming/comments/spec.

Default behavior with `ARL_LLM_ENABLED=0` must remain compatible with current tests.

## Teaser Contract for Editing

The edit-planner consumes semantic teaser recommendations only if:

- A semantic asset exists for the match.
- Recommended windows are within the match boundary.
- Each recommendation overlaps or snaps to an existing highlight window/candidate window.
- The window satisfies editing teaser duration constraints.

When invalid or absent, edit-planner uses the current highlight/subtitle teaser path.

Teaser-child input contract:

```python
CopywriterSemanticAsset.result.teaser_recommendations[*].source_start_seconds
CopywriterSemanticAsset.result.teaser_recommendations[*].source_end_seconds
CopywriterSemanticAsset.result.teaser_recommendations[*].hook_reason
CopywriterSemanticAsset.result.hook_line
```

## Compatibility

- Publish preset does not force-enable LLM.
- Non-publish defaults remain unchanged.
- Existing `CopyAsset` and `PublishingPackage` consumers keep working.
- New semantic manifest is additive and file-backed like other stage assets.
- `postprocess-reset` should remove semantic rows/state for selected sessions when resetting copywriter/editing outputs.

## Risks

- Stage split can make cache/state handling subtle. Keep semantic state separate from final copywriter state if needed.
- Provider JSON can be malformed. All LLM outputs must pass typed validation.
- Teaser hints can select bad windows. Editing must validate/snap hints instead of trusting provider coordinates.
- Network calls can make unattended postprocess slow. Timeouts and retries must be bounded.

