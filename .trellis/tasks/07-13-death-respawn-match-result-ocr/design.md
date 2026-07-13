# Design

Add two detector families on the 1080p Chinese layout profile:

- death/respawn: countdown crop OCR/template evidence plus death-like scene and KDA death evidence; use monotonic countdown/state confirmation and bounded refinement
- match result: Chinese victory/defeat templates/text plus temporal confirmation near scene/timer end transitions

Events and proposed downstream adjustments are persisted in shadow reports. Shadow mode calculates death-wait trim ranges, continuity guards, refined end timestamps, ending-context changes, and publishing facts, but production assets remain unchanged.

Active mode is a later configuration transition after at least three representative sessions are reviewed. Detector absence or ambiguity yields no event rather than inferred state.
