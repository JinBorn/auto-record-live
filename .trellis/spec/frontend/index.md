# Frontend Development Guidelines

> Best practices for frontend development in this project.

---

## Overview

This directory defines frontend conventions for this repository.
Current repository state has no frontend runtime app yet.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | Module organization and file layout | Active (No frontend runtime yet) |
| [Component Guidelines](./component-guidelines.md) | Component patterns, props, composition | Active (No component layer yet) |
| [Hook Guidelines](./hook-guidelines.md) | Custom hooks, data fetching patterns | Active (No hooks yet) |
| [State Management](./state-management.md) | Local state, global state, server state | Active (No frontend state layer yet) |
| [Quality Guidelines](./quality-guidelines.md) | Code standards, forbidden patterns | Active |
| [Type Safety](./type-safety.md) | Type patterns, validation | Active (No TypeScript app yet) |

---

## Current Frontend Shape

There is no frontend application module in this repository.

- No `src/frontend`, `apps/web`, or React and Vue component tree exists.
- Current runtime is backend-only Python under `src/arl/`.
- Browser automation scripts under `scripts/` are acquisition tooling, not frontend UI.

Frontend guidelines therefore document:
- current "not present yet" reality
- mandatory constraints for introducing frontend code without conflicting with backend pipeline contracts

---

**Language**: All documentation should be written in **English**.
