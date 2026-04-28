# Directory Structure

> How backend code is organized in this project.

---

## Overview

<!--
Document your project's backend directory structure here.

Questions to answer:
- How are modules/packages organized?
- Where does business logic live?
- Where are API endpoints defined?
- How are utilities and helpers organized?
-->

The current backend is a single Python package under `src/arl/` with one module per pipeline stage.

- Keep runtime logic inside the stage module that owns the state transition.
- Put cross-stage enums and asset payload models in `src/arl/shared/contracts.py`.
- Keep environment and path wiring in `src/arl/config.py`.
- Keep the CLI entrypoint in `src/arl/cli.py` thin. It should route to services, not implement business logic.

---

## Directory Layout

``` 
src/arl/
├── cli.py
├── config.py
├── shared/
│   ├── contracts.py
│   ├── jsonl_store.py
│   └── logging.py
├── windows_agent/
│   ├── models.py
│   ├── probe.py
│   ├── service.py
│   └── state_store.py
├── orchestrator/
│   ├── event_reader.py
│   ├── models.py
│   ├── service.py
│   └── state_store.py
├── recorder/
│   ├── models.py
│   └── service.py
├── segmenter/
│   ├── models.py
│   └── service.py
├── subtitles/
│   ├── models.py
│   └── service.py
└── exporter/
    ├── models.py
    └── service.py

tests/
├── pipeline/
│   └── test_post_live_pipeline.py
└── orchestrator/
    └── test_service.py
```

---

## Module Organization

<!-- How should new features/modules be organized? -->

- `windows_agent/`
  - Owns Douyin probing, browser/session-bound discovery, and append-only agent event emission.
  - Do not place orchestrator state or downstream media logic here.
- `orchestrator/`
  - Owns durable session state, recording job lifecycle, cursor management, and audit events.
  - If a change mutates session/job state, it belongs here unless the state is shared contract schema.
- `recorder/`, `segmenter/`, `subtitles/`, `exporter/`
  - Each stage should own one service module first.
  - If a stage grows, split by responsibility inside that stage directory before creating cross-stage utility modules.
- `shared/`
  - Only reusable contracts and tiny generic helpers belong here.
  - Do not move stage-specific file handling or heuristics into `shared/` just to avoid imports.

### Preferred Growth Pattern

When a stage outgrows a single `service.py`, split like this:

```text
src/arl/<stage>/
  service.py
  models.py
  store.py
  ffmpeg.py
```

Keep the public stage entrypoint as `service.py` unless there is a strong reason to rename it.

---

## Naming Conventions

<!-- File and folder naming rules -->

- Use snake_case for modules, files, functions, and local variables.
- Name long-running components as `<Stage>NameService`, for example `RecorderService`.
- Name durable payload models with explicit nouns such as `SessionRecord`, `RecordingJobRecord`, and `SubtitleAsset`.
- Name file-backed persistence helpers as `*Store`.
- Name append-only event readers and writers by behavior, for example `event_reader.py`, not vague names like `utils.py`.
- Avoid generic top-level files such as `helpers.py`, `common.py`, or `misc.py`.

---

## Examples

<!-- Link to well-organized modules as examples -->

- Good example for stage ownership: `src/arl/orchestrator/service.py`
- Good example for typed durable models: `src/arl/orchestrator/models.py`
- Good example for cross-stage contract placement: `src/arl/shared/contracts.py`

## Forbidden Structure Patterns

- Do not add business logic directly in `cli.py`.
- Do not let one stage write another stage's state files directly.
- Do not create shared helpers before the same pattern exists in at least two places.
- Do not introduce database-only abstractions while the MVP still uses file-backed durable state unless the task explicitly migrates that boundary.
