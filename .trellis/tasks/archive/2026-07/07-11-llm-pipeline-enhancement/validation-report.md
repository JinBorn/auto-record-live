# Validation Report — LLM 全流程增强（首轮影子分析）

Generated: 2026-07-11

Mode: story analysis enabled, shadow mode enabled. No edit plan, export, or
publishing package was changed by this validation run.

## Runtime Findings

- The original 30-second timeout was too short for 6k-10k token prompts.
- Reducing selected subtitle cues from 160 to 80 lowered prompt size and made
  calls complete reliably with a temporary 120-second timeout.
- Real model output used common aliases (`score`, `evidence_ids`, list-shaped
  `claim_evidence`). The parser now normalizes these forms before strict unknown
  candidate/evidence validation.

## Sample Results

| Sample | Story | Current | Proposed drop | Result |
|---|---|---:|---:|---|
| `4b5ec478` m02 | `no_strong_story` | 708.5s | 0.0s | Safe downgrade; no teaser proposed, but no candidate-level ranking returned |
| `cf11bf9e` m03 | `strong_story` | 756.5s | 93.1s | **Gate fail**: proposes three teaser candidates despite human review saying the opening material is messy and may be omitted |
| `bc90812b` m01 | `no_strong_story` | 801.9s | 0.0s | Safe downgrade; no teaser proposed, but no candidate-level ranking returned |

## Quality Issues

1. `cf11bf9e` m03 incorrectly promotes a commentary topic into a strong teaser
   story. The prompt needs a stricter distinction between a discussable topic
   and a visually/event-supported opening hook.
2. The recommended title contains an entity error (`男枪` became `南枪`). Entity
   claims need validation/canonicalization against subtitle/game metadata before
   publishing reuse.
3. `no_strong_story` responses may omit candidate decisions entirely. This is
   acceptable for teaser omission, but insufficient for semantic trim ranking;
   the prompt/schema should require decisions for every supplied candidate even
   when no primary story exists.

## Gate Decision

Shadow infrastructure passes. Semantic influence remains disabled:

- keep `ARL_LLM_STORY_SHADOW_MODE=1`;
- do not run active semantic window removal on production assets;
- tighten strong-story/teaser evidence requirements;
- require complete candidate coverage and entity validation;
- rerun plan-level shadow validation before exporting any sample.

## Gate Tightening Rerun

After inspecting the raw model response, the semantic parser was updated to
normalize common aliases while retaining strict reference checks. Additional
gates now:

- remove teaser candidates unless their decision references KDA evidence or
  has both strong emotion and a clear outcome;
- require at least one candidate decision when candidates exist;
- instruct exact entity-name copying and reject the observed `南枪`/`男枪`
  homophone mismatch when canonical evidence is present.

`cf11bf9e` m03 rerun result:

- `story_status=no_strong_story`;
- zero teaser candidates;
- recommended title uses canonical `男枪`;
- 45.0s proposed drop from the low-value early discussion/death window;
- no edit plan or publishing package changed (shadow mode).

The known messy-teaser acceptance case now passes. Active mode remains disabled
because candidate decision coverage is still sparse and needs another prompt /
input compaction iteration before semantic trimming is trusted broadly.

## Candidate-Coverage Iteration

Candidates are now marked `semantic_required=true` only for meaningful
highlight/key-event/tactical windows. Technical continuity and boundary bridge
windows remain deterministic and do not consume LLM output. The model must
return decisions for every required candidate.

On the next m03 run, all seven key-event candidates were covered and the model
correctly returned `no_strong_story`, no teaser, and canonical `男枪` wording.
However, it proposed dropping 640.1s (about 85% of candidate duration), which is
far too aggressive. Shadow mode prevented any asset change.

The initial direct post-plan deletion experiment was removed. Active semantics
now run through a second highlight finalization pass and only adjust value
density inside the existing budget shrink algorithm. KDA protection, boundary
anchors, source-gap repair, chronological ordering, and budget exceptions stay
inside their established deterministic pipeline.

- shadow mode skips the second pass and cannot affect edit/publishing outputs;
- active mode force-runs `highlight-finalize` after the one semantic call;
- semantic weight zero produces no references and follows legacy density;
- no duplicate direct-deletion path remains.

Full regression after the two-stage integration: 723 tests passed.
