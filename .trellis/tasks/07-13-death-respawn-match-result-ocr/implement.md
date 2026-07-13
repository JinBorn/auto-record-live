# Implementation Plan

1. Collect/define 1080p Chinese-client crop fixtures and detector-specific confidence rules.
2. Implement countdown recognition, monotonic stabilization, candidate/refinement requests, and death/respawn event derivation.
3. Implement Chinese victory/defeat recognition and multi-frame confirmation.
4. Add shadow adjustment models/stores for proposed death trimming, continuity protection, match-end refinement, and result facts.
5. Wire config/status/reset/quality visibility without mutating production output by default.
6. Test unreadable frames, false single reads, KDA disagreement, state recovery, result ambiguity, and budget caps.
7. Produce shadow reports for at least three representative sessions.
8. Update editing/export/orchestration specs and run targeted/full checks.
