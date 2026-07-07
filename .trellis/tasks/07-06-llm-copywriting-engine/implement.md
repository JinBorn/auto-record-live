# LLM copywriting engine implementation plan

## Checklist

1. Add config
   - Add `LlmSettings` to `Settings`.
   - Load `ARL_LLM_*` env vars in `load_settings()`.
   - Document variables in `.env.example`.
   - Keep publish preset from force-enabling LLM.

2. Add models and provider seam
   - Add semantic result and teaser recommendation Pydantic models.
   - Add an OpenAI-compatible provider client using `httpx`.
   - Add an in-process fake provider seam for tests.
   - Add schema validation and bounded retry behavior.

3. Split copywriter behavior into semantic and publishing phases
   - Add a semantic manifest path under `data/tmp/`.
   - Build prompt inputs from subtitles, highlight plans, boundaries, streamer metadata, and KDA/highlight evidence.
   - Add fingerprint-based cache and `force_reprocess` bypass.
   - Keep the existing heuristic generation as fallback-only.

4. Wire CLI and postprocess ordering
   - Extend the copywriter command only if needed to expose semantic-only/final phases; otherwise keep public CLI simple and route through service methods.
   - Update `PostProcessService` order so semantic hints run after `highlight-planner` and before `edit-planner`, while final publishing still runs after `exporter`.
   - Update postprocess order tests.

5. Teach edit-planner to consume teaser hints
   - Load latest semantic asset by `(session_id, match_index)`.
   - Validate/snap hint windows against highlight windows and editing duration constraints.
   - Keep current teaser selection as fallback.
   - Include semantic asset fingerprint or relevant fields in stale-plan detection.

6. Update reset/status/reporting touchpoints
   - Ensure `postprocess-reset` clears semantic manifest/state rows for selected sessions.
   - Consider whether `StatusService` should surface semantic asset counts.
   - Ensure `quality-report` copywriter fields continue to read final publishing packages.

7. Tests
   - Config env loading and defaults.
   - Provider request construction, fake response parsing, invalid JSON/schema retry, and fallback.
   - Semantic cache prevents duplicate provider calls; force reprocess calls again.
   - LLM success makes raw-excerpt title path unreachable.
   - LLM disabled preserves current behavior.
   - Postprocess stage order includes semantic hints before edit-planner and final publishing after exporter.
   - Edit-planner consumes valid teaser recommendations and ignores invalid ones.
   - Reset removes semantic artifacts for target sessions.

8. Validation
   - Run focused tests:
     ```powershell
     .\.venv\Scripts\python.exe -m pytest tests/pipeline/test_copywriter_service.py tests/pipeline/test_editing_service.py tests/pipeline/test_postprocess_service.py tests/pipeline/test_postprocess_reset_service.py tests/test_config.py
     ```
   - Run full suite:
     ```powershell
     .\.venv\Scripts\python.exe -m pytest tests
     ```
   - If user has configured `ARL_LLM_*`, run one smoke copywriter pass on a validation session and record the generated title in this task.

## Review Gate Before Start

- Confirm the two-phase copywriter design is acceptable:
  - semantic hints before edit-planner
  - final publishing package after exporter
  - heuristic copy remains fallback-only
- Confirm live provider smoke test may be skipped until `.env` has an API key.

## Rollback Points

- Revert postprocess ordering and edit-planner semantic-hint consumption first if teaser behavior regresses.
- Disable LLM via `ARL_LLM_ENABLED=0` to restore current copywriter behavior.
- Remove semantic manifest rows with postprocess reset if cached provider output is bad.

