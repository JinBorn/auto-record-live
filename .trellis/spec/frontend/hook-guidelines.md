# Hook Guidelines

> How hooks are used in this project.

---

## Overview

No frontend hooks exist yet in this repository.
When introduced, hooks should own reusable stateful logic and side-effect orchestration.

---

## Custom Hook Patterns

- Name hooks with `use` prefix.
- Keep hook return values explicit and stable in shape.
- Isolate browser APIs and subscriptions inside hooks, not components.

### Example Template

```tsx
export function useLiveSessionStatus(sessionId: string) {
  const [status, setStatus] = useState<"idle" | "loading" | "ready">("idle")
  // effect wiring here
  return { status }
}
```

---

## Data Fetching

- No frontend data-fetching stack is selected yet.
- First frontend implementation must document chosen library and cache policy here.
- Align API contracts with backend manifest and schema boundaries.

---

## Naming Conventions

- `useXxx` for hooks.
- `useXxxQuery` and `useXxxMutation` naming if query-library conventions are adopted.
- Avoid vague names like `useData` when domain is known.

---

## Common Mistakes

### Common Mistake: Duplicating stateful logic across components instead of extracting hooks

**Symptom**: Similar effects and state transitions are copy-pasted in multiple components.

**Cause**: Hook extraction deferred too long.

**Fix**: Move repeated effect and state sequences into a shared custom hook.

**Prevention**: If logic appears in two components, evaluate extraction before adding a third copy.
