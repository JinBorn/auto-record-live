# LLM copywriting engine

## Goal

Replace the demo-overfit keyword-template semantic layer in
`CopywriterService` with a pluggable cloud-LLM engine that produces structured
publishing copy (titles, cover lines, summary, description, tags) and teaser
recommendations from the full match transcript, with the existing heuristics
kept only as an offline fallback.

## User Value

The current `_summary_headline` / `_is_strong_compact_title` phrase tables are
hardcoded to demo1/demo2 vocabulary ("电刀AP机器人", "炒股经济学", ...). Any
other stream content degrades to raw ASR excerpts as titles — the 07-02
validation showed titles like "堆場式是咋的 就對面的人他也會...". A real
semantic layer is the single highest-leverage fix for upload-ready output.

## Decisions

- Cloud API only (local machine cannot host a capable model).
- Provider abstraction targets the OpenAI-compatible chat-completions wire
  format so one client covers DeepSeek, Qwen (DashScope compatible mode),
  Kimi/Moonshot, GLM, OpenAI, and Anthropic's OpenAI-compatibility endpoint.
  Recommended default provider: DeepSeek (`deepseek-chat`) for cost and
  Chinese quality; final choice is whatever base URL + key the user configures.

## Requirements

- Config via env (names indicative): `ARL_LLM_ENABLED` (default 0),
  `ARL_LLM_BASE_URL`, `ARL_LLM_API_KEY`, `ARL_LLM_MODEL`,
  `ARL_LLM_TIMEOUT_SECONDS`, `ARL_LLM_MAX_RETRIES`. Publish preset does NOT
  force-enable the LLM; it activates only when enabled and key present.
- Inputs assembled per session/match: streamer name, match duration, full
  subtitle cues with timestamps (token-budget truncation strategy that always
  keeps cues overlapping highlight windows plus head/tail context), highlight
  windows with reasons, KDA change events.
- Structured JSON output, schema-validated with bounded retries, then
  fallback to heuristics on persistent mismatch:
  - 3 title candidates (Bilibili style, <=30 chars, no raw-excerpt copies)
  - cover lines: 2-4 lines, <=10 chars per line, punchy stacked-headline style
  - summary <=96 chars; description 1-3 sentences; 5-8 tags
  - teaser recommendation: up to 3 source windows (start/end seconds snapped
    to provided candidate windows) each with a one-line hook reason
  - a `hook_line` usable by the teaser transition card
- Persist the LLM result as a durable per-session/match asset so reruns do not
  re-bill; `--force-reprocess` bypasses the cache. Log token usage per call.
- Pipeline ordering: teaser hints must exist before the edit-planner stage.
  Design must choose and document one mechanism (e.g. a semantic-hints stage
  after subtitles, or splitting copywriter into hint + package phases); the
  contract consumed by `07-06-teaser-robustness-transition` must be written
  into that task's inputs.
- Demote (do not delete) the hardcoded phrase tables: they remain the
  fallback path when LLM is disabled/unreachable; mark them clearly as
  fallback-only in code and spec.
- Failure behavior: network/auth/schema failures log a structured warning and
  fall back cleanly; postprocess never crashes because of the LLM stage.
- Tests use a fake in-process provider (no network); cover schema validation,
  truncation, caching, fallback, and title-quality guards.

## Out Of Scope

- Automatic provider/model benchmarking or multi-provider fan-out.
- Vision/multimodal input (frames) — transcript + events only for v1.
- Local model support (Ollama etc.).

## Acceptance Criteria

- [ ] With the fake provider, `copywriter` outputs LLM-derived title, cover
      lines, summary, tags, and evidence; the raw-excerpt title path is
      unreachable while the LLM succeeds.
- [ ] With `ARL_LLM_ENABLED=0` (default), current behavior and tests are
      unchanged.
- [ ] Teaser recommendation asset is persisted before edit-planner runs and
      its schema is documented for the teaser task.
- [ ] Cache prevents duplicate API calls across reruns (verified by fake
      provider call counting); `--force-reprocess` re-calls.
- [ ] A live smoke run against the user-configured provider on one validation
      session produces a coherent Chinese title that is not an ASR excerpt
      (manual review recorded in the task).
- [ ] `.env.example` documents the new variables including DeepSeek/Qwen/
      Anthropic-compat base-URL examples.

## Notes

- Complex task: requires `design.md` (provider client, prompt template, asset
  contract, stage ordering) and `implement.md` before `task.py start`.
- User will supply the API key in `.env` before the live smoke test; all other
  work proceeds without it.
