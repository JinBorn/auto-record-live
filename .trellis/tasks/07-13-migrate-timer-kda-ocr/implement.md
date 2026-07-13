# Implementation Plan

1. Register timer/KDA detector adapters with independent due intervals.
2. Port current KDA transition and stable-frame refinement rules into the shared stage without semantic changes.
3. Add a typed asset view for timer readings and KDA events by source/match range.
4. Make `VisionMatchDetector` prefer persisted timer evidence, with logged legacy fallback.
5. Make `HighlightPlannerService` prefer persisted KDA evidence and continue writing compatibility plan events.
6. Add legacy-vs-asset parity tests for boundaries, KDA changes, refinement timestamps, and segmented recordings.
7. Verify cached highlight/edit reruns execute zero coarse OCR calls.
8. Update specs and run targeted/full checks.
