# Directory Structure

> How frontend code is organized in this project.

---

## Overview

There is currently no frontend runtime code in this repository.

When frontend code is introduced, follow this structure instead of mixing UI files into `src/arl/`.

---

## Directory Layout

```text
apps/
  web/
    src/
      app/
      features/
      components/
      hooks/
      state/
      types/
      lib/
```

---

## Module Organization

- Keep backend runtime under `src/arl/` only.
- Frontend app code must live under a dedicated app boundary such as `apps/web/`.
- Organize frontend by feature first, then shared primitives.
- Do not place UI-only code in `scripts/` (that directory is for automation tooling).

---

## Naming Conventions

- Use kebab-case for route and feature folders.
- Use PascalCase for component files.
- Use `useXxx` naming for hooks.
- Keep shared types in `types/` or colocated per feature when private.

---

## Examples

- Current repository examples (absence is the key signal):
  - backend-only runtime at `src/arl/`
  - no UI component tree under `src/`

When adding a frontend app, include at least one concrete path example in this file from the new codebase.
