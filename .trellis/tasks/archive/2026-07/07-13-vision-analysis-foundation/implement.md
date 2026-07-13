# Implementation Plan

1. [x] Add typed reading/event/asset/state/metrics contracts and JSONL store paths.
2. [x] Add the versioned 1080p Chinese-client layout profile and geometry validation.
3. [x] Implement detector protocol, due-time coarse dispatcher, shared frame decode, failure isolation, and metrics.
4. [x] Implement refinement request unioning, 15% cap, frame budgets, and chunk-aware source timestamps.
5. [x] Add fingerprints/cache reuse and forced replacement behavior.
6. [x] Wire CLI, config, publish preset, postprocess ordering seam, reset, status, and audit logging.
7. [x] Test multi-detector single decoding, cache invalidation/hits, segmented boundaries, failures, and refinement caps.
8. [x] Update orchestration specs and run targeted/full checks.
