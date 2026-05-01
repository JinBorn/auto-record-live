# Backend Development Guidelines

> Best practices for backend development in this project.

---

## Overview

This directory contains guidelines for backend development. Fill in each file with your project's specific conventions.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | Module organization and file layout | Partial |
| [Orchestration Contracts](./orchestration-contracts.md) | Windows agent and orchestrator event/state contracts | Active |
| [Database Guidelines](./database-guidelines.md) | Current file-backed persistence and migration guardrails | Active |
| [Error Handling](./error-handling.md) | Error types, handling strategies | Partial |
| [Quality Guidelines](./quality-guidelines.md) | Code standards, forbidden patterns | Active |
| [Logging Guidelines](./logging-guidelines.md) | Structured logging, log levels | Active |

---

## How to Fill These Guidelines

For each guideline file:

1. Document your project's **actual conventions** (not ideals)
2. Include **code examples** from your codebase
3. List **forbidden patterns** and why
4. Add **common mistakes** your team has made

The goal is to help AI assistants and new team members understand how YOUR project works.

---

## Current Backend Shape

The current MVP backend is a local file-driven pipeline:

- `src/arl/windows_agent/` probes a Douyin room and appends JSONL events.
- `src/arl/orchestrator/` tails the JSONL event log, maintains durable state, and writes an audit log.
- `src/arl/shared/contracts.py` defines cross-module enums and shared asset models.
- Persistent files under `data/tmp/` are part of the executable contract for local runs.

Read [Orchestration Contracts](./orchestration-contracts.md) before changing event payloads, state files, or session/job lifecycle logic.

---

**Language**: All documentation should be written in **English**.
