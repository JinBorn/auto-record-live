# Validation Report

## Automated Verification

- Focused semantic SFX/copywriter/editing/config tests: 144 passed.
- Final full project suite: 732 passed.
- Compileall and diff checks passed.
- Shadow regression verifies a validated `mistake` recommendation writes
  `copywriter-semantic-sfx-shadow-reports.jsonl` and does not create an edit plan.
- Active-mode regression maps a verified source anchor into the main rendered timeline,
  resolves the exact `mistake` track and gain, and rejects it when a deterministic
  effect occupies the configured spacing window.

## Representative Real-Subtitle Candidate Scan

The configured usable semantic categories were:

- `boom`
- `mistake`
- `pew`
- `transition_bruh`

Initial scanning showed that generic `伤害/爆炸` matches over-selected teammate
descriptions such as `我们ADC伤害很高`. Candidate discovery was tightened before final
verification:

- boom/projectile candidates require first-person attribution;
- `我们` is not treated as the streamer's own action;
- teammate/opponent markers suppress candidates without an independent first-person
  marker;
- generic substring `坏了` was replaced with more specific mistake evidence to avoid
  matching unrelated words such as `破坏了`.

After tightening, representative candidates included:

- `session-20260617073649-4b5ec478`, match 2: `我操坏了` -> `mistake` hint;
- the same match: `我靠什么伤害` -> `boom` hint (LLM may still choose `none`);
- `session-20260617073651-cf11bf9e`, match 4: explicit spoken mistake -> `mistake` hint;
- `session-20260616122238-2469b78a`, match 1: first-person projectile wording -> `pew` hint.

## Rollout State

No `ARL_LLM_API_KEY` is configured in the current environment, so actual model-quality
shadow review could not be run without new external credentials/cost authority.
Production safety is preserved:

- `ARL_LLM_SEMANTIC_SFX_SHADOW_MODE` defaults to `1`;
- active edit plans remain unchanged in shadow mode;
- active rollout must remain off until real-model shadow reports are reviewed.
