# Implementation Plan

1. [x] Read the highlight planner's SRT parsing, speech-chain, trim, and final budget-protection paths plus applicable backend specs.
2. [x] Add sentence-span boundary logic using subtitle timing and terminal punctuation, reusing existing helpers where possible.
3. [x] Update final budget speech protection so the current sentence can finish beyond the old 3-second cap while retaining a pathological safety bound.
4. [x] Add focused unit tests for the reported partial-sentence pattern, adjacent completed sentences, and bounded long commentary.
5. [x] Run targeted highlight-planner/config tests, then the full suite.
6. [x] Update backend export/editing-quality specs. The reported session's generated assets are not present locally, so direct visual re-export remains a follow-up validation step.

## Risk / Rollback

- Risk: punctuation-poor ASR may merge too much speech. Mitigate with timing gaps plus a hard safety bound and sentence-boundary fallback.
- Rollback: retain the configuration switch/default as the operational fallback; code changes remain isolated to highlight boundary selection.
