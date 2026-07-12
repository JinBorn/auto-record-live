# LLM-Guided Semantic SFX Selection

## Goal

Use more of the configured SFX library by allowing bounded LLM semantic analysis to
choose an appropriate effect category for verified edit moments, while preserving
deterministic timing, safety limits, and reliable fallback behavior.

## User Value

Current videos mostly use the same coin-drop sound. Contextual effects such as mistake,
impact, projectile, awkward-transition, reaction, and celebratory variants can make
edits feel more expressive when they are used sparingly and at understandable moments.

## Confirmed Facts

- `data/sfx/library.json` already contains categories beyond the four categories the
  edit planner requests, including `mistake`, `boom`, `pew`, and
  `transition_bruh`.
- The SFX manifest loader accepts arbitrary non-empty category strings and optional
  per-track gain; the asset library is not the limiting layer.
- Current deterministic candidate generation requests only `kill_coin`, `multi_kill`,
  `transition_whoosh`, and `teaser_impact`.
- Kill timestamps are mapped from durable KDA events into the rendered timeline and
  are protected by existing precision/rate-limit tests.
- The project already persists one structured copywriter semantic asset per match.
  Edit planning consumes that asset for teaser and transition decisions.
- Existing semantic contracts reject unknown candidate/evidence references and do not
  accept arbitrary LLM timestamps.
- Previous SFX requirements intentionally excluded generic emotion/reaction SFX because
  there was no reliable bounded semantic decision path at that time.

## Requirements

- Generate deterministic semantic-SFX candidates from real timeline segments,
  subtitle cues, KDA events, and existing semantic evidence IDs.
- Assign each candidate a stable ID and a deterministic source/output timestamp before
  invoking the LLM.
- Give the LLM only the available SFX categories plus an explicit `none` choice.
- The LLM may select a category, confidence, and evidence-backed reason; it must not
  create timestamps, paths, gains, or candidate IDs.
- Validate every LLM selection against known candidates, known evidence, available
  library categories, and configured safety policy.
- Preserve deterministic kill/multi-kill, transition, and teaser-impact behavior unless
  an explicitly defined semantic policy safely augments it.
- Missing/disabled/timed-out/invalid LLM analysis must keep the current deterministic
  edit plan and must never fail export.
- Apply global rate limits, per-category limits, minimum spacing, and total SFX caps
  after semantic and deterministic candidates are combined.
- Avoid duplicate effects at the same moment; deterministic KDA/transition effects win
  over optional semantic effects.
- Persist enough decision metadata to audit why a semantic SFX was accepted or
  rejected without putting machine annotations into visible subtitles.
- Support shadow mode so candidate/LLM decisions can be evaluated without changing
  production edit plans.
- Default to conservative production usage: at most two optional semantic SFX per
  match, excluding deterministic kill, transition, and teaser effects.
- Require high confidence and clear evidence; `none` is the preferred response when
  the moment or category fit is ambiguous.
- Validate representative real matches in shadow mode before active rollout.
- Keep MVP streamer-centric: recommend semantic effects only for the streamer's own
  action, outcome, or clearly attributable reaction. Do not annotate ordinary teammate
  or opponent moments.

## Acceptance Criteria

- [x] A high-confidence mistake moment can select the configured `mistake` track at a
      precomputed timeline timestamp.
- [x] Impact/projectile/awkward-transition categories can be selected only when present
      in the loaded library and allowed by policy.
- [x] `none` or low confidence emits no optional semantic effect.
- [x] Unknown candidate IDs, categories, evidence IDs, or timestamps are rejected.
- [x] Semantic effects never displace or duplicate a deterministic kill/multi-kill hit.
- [x] Total hit count, spacing, gain bounds, and exporter validation remain enforced.
- [x] LLM failure, disabled mode, or missing library follows the current deterministic
      behavior.
- [x] Shadow reports support proposed/rejected decisions; representative real subtitle
      candidates were reviewed locally, and real-model review remains an activation gate
      because this environment has no API credentials.
- [x] Focused editing/copywriter tests and the full suite pass.

## Out of Scope

- Allowing the LLM to invent source or rendered timestamps.
- Automatically downloading or generating sound assets.
- Replacing KDA OCR or deterministic transition timing.
- Audio-content recognition inside arbitrary SFX files.

## Open Questions

- None currently blocking product planning.
