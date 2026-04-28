# Quality Guidelines

> Code quality standards for frontend development.

---

## Overview

No frontend runtime code exists yet.
This file defines the required quality bar for the first frontend module introduced into this repository.

---

## Forbidden Patterns

- Shipping frontend code without automated lint and type-check wiring.
- Using `any` and unchecked type assertions as a default pattern.
- Mixing business logic, network calls, and heavy rendering logic in one component file.
- Duplicating API payload typing across features without shared contracts.

---

## Required Patterns

- Add explicit frontend quality commands before merging frontend code:
  - lint
  - type-check
  - tests
- Keep API contracts typed and validated at boundaries.
- Add regression tests for every fixed frontend bug once frontend exists.
- Document chosen frontend stack conventions in this directory as soon as the first app lands.

---

## Testing Requirements

- Component logic should have unit tests for non-trivial behavior.
- Feature flows should have integration tests for core user journeys.
- Critical user paths should include end-to-end coverage once a UI exists.
- Accessibility checks should be part of CI for interactive flows.

---

## Code Review Checklist

- Are lint, type-check, and test commands defined and green for frontend changes?
- Are component props and API payloads fully typed?
- Is state ownership clear (local vs server vs global)?
- Are accessibility basics met for interactive elements?
- Do new frontend conventions get reflected in `.trellis/spec/frontend/*.md`?
