# Component Guidelines

> How components are built in this project.

---

## Overview

There are no frontend components in the current repository.
This file defines required conventions for the first frontend component layer.

---

## Component Structure

- Prefer one component per file with explicit typed props.
- Keep rendering components pure when possible.
- Move side effects and data-fetching to hooks and services, not inline in presentational components.

### Example Template

```tsx
type MatchCardProps = {
  title: string
  durationSeconds: number
}

export function MatchCard({ title, durationSeconds }: MatchCardProps) {
  return <article>{title} - {durationSeconds}s</article>
}
```

---

## Props Conventions

- No `any` props.
- Prefer exact object props over broad index signatures.
- If a prop is optional, handle default behavior explicitly.

---

## Styling Patterns

- No styling system is established yet.
- First frontend implementation must choose one system and document it here with real examples.
- Avoid mixing multiple styling systems in initial setup.

---

## Accessibility

- Use semantic HTML elements first.
- Interactive elements must be keyboard reachable.
- Prefer visible labels for controls rather than placeholder-only labels.

---

## Common Mistakes

### Common Mistake: Embedding data-fetching and orchestration directly in render-heavy components

**Symptom**: Components become large, hard to test, and difficult to reuse.

**Cause**: Missing separation between UI rendering and orchestration logic.

**Fix**: Move orchestration into dedicated hooks and services and keep component inputs declarative.

**Prevention**: Enforce typed props and thin rendering components in reviews.
