# Implementation Plan

1. [x] Register timer/KDA detector adapters with independent due intervals.
2. [x] Port current KDA transition and stable-frame refinement rules into the shared stage without semantic changes.
3. [x] Add a typed asset view for timer readings and KDA events by source/match range.
4. [x] Make `VisionMatchDetector` prefer persisted timer evidence, with logged legacy fallback.
5. [x] Make `HighlightPlannerService` prefer persisted KDA evidence and continue writing compatibility plan events.
6. [x] Add legacy-vs-asset parity tests for boundaries, KDA changes, refinement timestamps, and segmented recordings.
7. [x] Verify cached highlight/edit reruns execute zero coarse OCR calls.
8. [x] Update specs and run targeted/full checks.
