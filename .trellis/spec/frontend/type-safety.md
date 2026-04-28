# Type Safety

> Type safety patterns in this project.

---

## Overview

Current runtime is Python plus Pydantic on backend.
No TypeScript frontend package exists yet.

When TypeScript is introduced, this file is the mandatory contract for type-safety conventions.

---

## Type Organization

- Keep feature-private types close to the feature.
- Keep cross-feature API DTOs in a shared `types/` boundary.
- Do not duplicate backend contract names with incompatible shapes.

---

## Validation

- Runtime validation library is not selected yet.
- If frontend validates backend payloads, choose one library and document parse and fallback behavior here.
- Keep compile-time TypeScript types and runtime validators aligned.

---

## Common Patterns

- Prefer discriminated unions for UI states (`idle/loading/ready/error`).
- Use narrow literal unions instead of wide `string` where states are finite.
- Centralize reusable type guards when runtime narrowing is required.

---

## Forbidden Patterns

- Avoid `any` in app code.
- Avoid unchecked `as` casts for API payloads.
- Avoid implicit `unknown` to concrete type transitions without validation.
