# Database Guidelines

> Database patterns and conventions for this project.

---

## Overview

Current MVP runtime does not use a relational or document database yet.
Durable state is file-backed under `data/tmp/` and validated with Pydantic models.

This file documents current persistence reality and migration guardrails for introducing a database later.

---

## Query Patterns

- Current reads are file loads through typed state stores, for example:
  - `OrchestratorStateStore.load()` reading `orchestrator-state.json`
  - `WindowsAgentStateStore.load()` reading `windows-agent-state.json`
- Current writes are either:
  - full-state JSON snapshots (`state_path.write_text(..., encoding="utf-8")`)
  - append-only JSONL events (`open(..., "a", encoding="utf-8")`)
- Do not introduce ad hoc partial updates against state files.
- Always round-trip through typed models when reading persisted state.
- All durable state files (`*-state.json`) and event/audit JSONL files must be read and written using explicit `encoding="utf-8"`. See the matching forbidden-pattern entry in `quality-guidelines.md`.

### Example (Current Pattern)

```python
if not self.state_path.exists():
    return OrchestratorStateFile()
raw = self.state_path.read_text(encoding="utf-8")
if not raw.strip():
    return OrchestratorStateFile()
return OrchestratorStateFile.model_validate_json(raw)
```

---

## Migrations

- There is no migration framework in the current codebase.
- "Migration" currently means additive JSON-compatible changes to persisted state and event payloads.
- When changing persisted schema:
  - prefer adding optional fields with defaults
  - avoid renaming and removing fields in one step
  - update producer + consumer + tests in the same task
- If SQL storage is introduced later, add an explicit migration tool section and version policy here before implementation.

---

## Naming Conventions

- State snapshot files:
  - `<stage>-state.json` (for example `orchestrator-state.json`)
- Event manifests:
  - `<stage>-events.jsonl` (for example `windows-agent-events.jsonl`)
  - `<asset>-assets.jsonl` (for example `recording-assets.jsonl`)
- Directory conventions:
  - durable runtime temp artifacts under `data/tmp/`
  - raw media under `data/raw/`
  - processed artifacts under `data/processed/`
  - exports under `data/exports/`

---

## Common Mistakes

### Common Mistake: Treating state files as schemaless blobs

**Symptom**: Runtime code starts indexing raw dicts and silently diverges from models.

**Cause**: Bypassing Pydantic model validation on load.

**Fix**: Parse persisted files into typed models first, then operate on model fields.

**Prevention**: Keep persistence access behind stage `*Store` classes.

### Common Mistake: Breaking backward compatibility in persisted payloads

**Symptom**: Existing local state causes crashes after code update.

**Cause**: Field rename or removal without transition path.

**Fix**: Ship additive fields first, keep old fields readable during transition, and update tests.

**Prevention**: Treat local persisted files as executable contract surfaces.
