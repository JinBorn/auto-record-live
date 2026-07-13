# Design

Implement `arl.vision_analysis` as a stage owning orchestration, stores, fingerprints, layout profiles, detector registration, coarse scheduling, refinement merging, and metrics. Reuse `recording_resolver` for chunk-aware source mapping and `jsonl_store` for append-only typed assets. Existing `arl.vision` crop readers remain pure frame-level detector dependencies.

Use one due-time scheduler over the smallest enabled interval. A sampled frame is dispatched to detectors whose next due timestamp has arrived. Refinement requests are normalized to source ranges, unioned, capped, decoded once per union, and dispatched only to requesting detectors.

The initial layout profile accepts only 1920x1080. Unsupported geometry produces an explicit degraded asset rather than scaled crops.

Cache keys cover source manifest/files, stage schema, layout profile, detector versions/settings, sampling, and refinement policy. Latest compatible row wins.
