# LLM 全流程增强规划

## Goal

Systematically identify and prioritize additional LLM integration points across
the video post-processing pipeline so generated videos have stronger content
understanding, titles, cover copy, narrative structure, and quality control,
while preserving deterministic fallbacks and bounded cost.

## Confirmed Context

- The project already uses an LLM semantic layer in copywriting, including
  title candidates, cover lines, summaries, descriptions, tags, hook lines,
  and teaser recommendations.
- The current prompt allows titles up to 45 compact characters, but
  `LlmCopywritingResult` still rejects recommended titles above 30 characters;
  planning must eliminate this prompt/schema drift.
- Human review explicitly wants richer copy (titles up to 45 compact Chinese
  characters) and prefers omitting low-quality teasers over forcing one.
- The current seven-sample regeneration remains a separate in-progress task;
  this planning task must not change or interrupt it.
- The opportunity review must cover the complete pipeline rather than only the
  publishing package.

## Requirements

- Inventory the current pipeline stages, their durable assets, and existing LLM
  calls or heuristic-only decisions.
- Identify candidate LLM uses for at least:
  - transcript/content understanding and topic segmentation;
  - highlight ranking and event explanation;
  - teaser/opening selection and narrative ordering;
  - title, cover-image copy, summary, description, and tags;
  - editing guidance such as zoom/SFX/BGM intent where appropriate;
  - post-export semantic quality review.
- For every candidate, document user value, required inputs, expected output
  contract, deterministic fallback, latency/cost impact, and failure risk.
- Separate MVP candidates from later experiments. Prefer LLM decisions where
  semantic understanding materially improves quality; retain deterministic
  code for timing, safety limits, rendering, and hard acceptance checks.
- Prioritize delivery in this order:
  1. whole-match content understanding and highlight/story quality;
  2. publishing-package quality, including title and cover copy;
  3. post-export LLM review is deferred and must not be implemented in the
     initial scope.
- Reuse one structured semantic analysis across multiple downstream consumers
  when possible instead of independently prompting each stage.
- Preserve offline/degraded operation when the LLM is disabled or unavailable.
- The LLM may score, explain, merge, remove, and order candidates produced by
  deterministic detectors (KDA, subtitle signals, chat bursts, and related
  evidence), but it must not directly invent arbitrary source-time windows.
- Deterministic code remains authoritative for source-time boundaries, KDA
  protection, maximum source gaps, duration budgets, and rendering safety.
- The initial narrative objective is highlight-first chronological editing:
  retain kills, team fights, mistakes, reversals, strong reactions, and other
  high-value events; add only the context needed to understand their cause or
  payoff; remove repetitive narration, routine farming, and long setup without
  a result; do not reorder the main story into a non-chronological montage.
- The initial release uses one shared editorial policy. It may include current
  match metadata such as streamer name, champion, role, and detected events,
  but it must not build or depend on persistent per-streamer style profiles.
- Target one structured LLM semantic-analysis call per match. Persist and reuse
  the result across highlight ranking, story summary, teaser guidance, title,
  cover copy, description, and tags. Invalidate the cache when transcript,
  deterministic candidates, relevant match metadata, model, schema, or prompt
  version changes. Ordinary regeneration should reuse the cached asset.
- Each match selects one primary story angle. Teaser guidance, hook line,
  recommended title, cover lines, summary, and description must all describe
  that same angle. Alternate title wording is allowed, but alternate titles
  must not switch to unrelated events or claims.
- Titles and cover copy may use suspense, contrast, and emotional language, but
  factual claims such as comeback, solo kill, multikill, instructional value,
  or final outcome must reference supporting deterministic events or transcript
  evidence. Unsupported claims are invalid and must fall back to safer copy.
- LLM semantic scores may contribute to value-density and duration-budget
  allocation among otherwise valid candidate windows. Deterministic KDA-span
  protection, boundary anchors, source-gap limits, duration budgets, and other
  hard constraints remain authoritative and cannot be overridden by the LLM.
- The semantic result may explicitly return `no_strong_story`. In that case,
  the pipeline must not force a teaser or promotional narrative. It should use
  deterministic key events with minimal context, generate neutral evidence-
  based publishing copy, persist the downgrade reason, and avoid treating the
  intentional omission as a quality failure.
- Roll out semantic ranking in shadow mode before it changes final edit plans.
  Shadow validation should use only 2-3 representative matches per iteration,
  not the full seven-match reference set, because full exports are too slow.
  Compare current decisions with LLM story, ranking, omission, and publishing-
  copy recommendations before enabling the edit-impact switch.
- `session-20260617073649-4b5ec478` match 02 is mandatory in the initial
  shadow-validation set.

## Acceptance Criteria

- [ ] Current and proposed LLM touchpoints are mapped stage by stage.
- [ ] Each proposed touchpoint includes benefits, risks, fallback, and cost.
- [ ] A recommended MVP scope and deferred scope are explicitly listed.
- [ ] Cross-stage semantic asset contracts and cache/invalidation behavior are
      defined in `design.md`.
- [ ] Implementation order, tests, rollout switches, and validation samples are
      defined in `implement.md`.
- [ ] Remaining product decisions are resolved with the user before task start.

## Out of Scope

- Replacing FFmpeg/rendering, subtitle timestamp arithmetic, or hard safety
  thresholds with free-form LLM output.
- Implementing changes before the user reviews and approves the final plan.
- Post-export LLM semantic review or automatic rework in the initial release.

## Open Questions

- None. Initial shadow set: `4b5ec478` match 02, `cf11bf9e` match 03, and
  `bc90812b` match 01.

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
