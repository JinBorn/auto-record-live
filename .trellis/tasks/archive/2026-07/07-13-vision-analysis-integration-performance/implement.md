# Implementation Plan

1. Select at least three representative 1080p Chinese-client sessions, including segmented and non-segmented input when available.
2. Capture legacy timer/KDA wall-time, frame/OCR counts, boundaries, KDA events, and output quality.
3. Run shared visual analysis and compare parity, metrics, cache behavior, refinement caps, and degradation paths.
4. Run downstream force-reprocess stages and prove no repeated coarse OCR on cache hits.
5. Generate death/result shadow reports and review proposed trims/endings against video.
6. Run quality reports and full automated checks.
7. Document rollout decision for legacy fallback removal and new-detector active mode.
8. Update parent acceptance, specs, validation report, and archive completed children/parent.
