# Design

Treat integration as a rollout gate, not another detector implementation. Run the publish pipeline with shared timer/KDA active and death/result shadowed. Capture a legacy baseline and new-stage metrics on the same recordings/configuration. Validate outputs, cached reruns, failure fallback, and human-visible proposals.

Acceptance requires <=1.25x legacy timer+KDA wall time, one coarse decode schedule, zero cached coarse OCR, <=15% refinement union, parity for existing signals, and three-session review for new signals. The final report decides whether to keep or remove legacy scans and whether new signals are ready for active mode.
