# Auth-ready 1080p stream gating for Douyin and Bilibili

## Goal

Implement strict availability gating for the two configured rooms so that:

1. Streams below 1080p are treated as unavailable.
2. Low bitrate variants are treated as unavailable.
3. Login credential wiring stays supported so higher-quality variants can be unlocked later.

Current rooms:

- Douyin: https://live.douyin.com/356635625680 (streamer: 顺顺剑圣)
- Bilibili: https://live.bilibili.com/22907643 (streamer: 阳光男孩小丑熊)

## Requirements

1. Bilibili probe quality gate:
- Keep selecting the best candidate from playinfo response.
- Treat candidate as unavailable when it does not meet minimum quality threshold.
- Default threshold must enforce 1080p baseline (`current_qn >= 400`).
- If bitrate metadata exists and is below minimum bitrate threshold, treat as unavailable.

2. Douyin probe quality gate:
- Keep selecting the best URL candidate.
- Enforce minimum quality tier so `_hd/_sd/_md/_ld` are unavailable by default.
- Default threshold must enforce 1080p-grade tier (`uhd` or higher).
- If selected stream URL is below threshold, treat as unavailable.

3. Auth-ready behavior:
- Preserve and document credential injection paths:
  - `ARL_BILIBILI_SESSDATA`
  - `ARL_DOUYIN_COOKIE`
- With valid credentials configured later, probes should be able to pass quality gate and emit live/direct-stream snapshots.

4. Configurability:
- Add env-configurable thresholds for:
  - Bilibili minimum qn
  - Minimum bitrate kbps (global probe gate)
  - Douyin minimum quality tier
- Keep defaults strict (1080p-or-above only).

5. Contract behavior:
- When quality gate fails, probe should emit `state=offline` with explicit reason.
- Do not silently downgrade to lower-quality direct stream.
- Keep existing platform headers propagation contract unchanged.

## Acceptance Criteria

- Bilibili: candidates with `current_qn < 400` are unavailable.
- Douyin: selected URL tier below `uhd` is unavailable.
- Bitrate gate rejects candidates below configured threshold when bitrate field is present.
- Existing cookie/SESSDATA injection tests still pass.
- Updated/new unit tests cover threshold pass/fail paths.

