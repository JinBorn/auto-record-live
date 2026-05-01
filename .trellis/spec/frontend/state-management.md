# State Management

> How state is managed in this project.

---

## Overview

No frontend state-management library is in use yet because there is no frontend runtime module.

Use this file as the decision contract once frontend is introduced.

---

## State Categories

- Local component state: short-lived UI interaction state.
- Server state: data fetched from backend APIs or manifests.
- Global app state: only for cross-feature concerns once a frontend exists.
- URL state: query and path params for navigable filters and views.

---

## When to Use Global State

- Promote to global only if at least two distant features consume and mutate the same data.
- Do not promote purely because prop drilling feels inconvenient in one subtree.
- Prefer feature-local state and composable hooks first.

---

## Server State

- No cache library selected yet.
- Initial frontend implementation must document:
  - cache ownership
  - revalidation strategy
  - error and loading state conventions
- Keep transport DTOs separate from view-model shaping logic.

---

## Common Mistakes

### Common Mistake: Treating server state as local mutable state

**Symptom**: Stale UI data and inconsistent optimistic updates.

**Cause**: Server data copied into unrelated local state stores without synchronization rules.

**Fix**: Keep server state in a dedicated data-fetching and cache boundary.

**Prevention**: Document ownership and invalidation policy when selecting the frontend stack.
