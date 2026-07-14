# Implementation Plan

1. [x] Define 1080p Chinese-client crops, synthetic fixtures, and detector-specific confidence rules.
2. [x] Implement countdown recognition, monotonic stabilization, candidate/refinement requests, and death/respawn event derivation.
3. [x] Implement Chinese victory/defeat recognition and multi-frame confirmation.
4. [x] Add shadow adjustment models/stores for proposed death trimming, continuity protection, match-end refinement, and result facts.
5. [x] Wire config/status/reset/quality visibility without mutating production output by default.
6. [x] Test unreadable frames, false single reads, KDA disagreement, state recovery, result ambiguity, and budget caps.
7. [x] Produce and human-review shadow reports for three representative sessions (two accepted death/respawn transitions, one evidence-safe rejection).
8. [x] Update editing/export/orchestration specs and run targeted/full checks.
