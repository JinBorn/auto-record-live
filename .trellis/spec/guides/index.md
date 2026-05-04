# Thinking Guides

> **Purpose**: Expand your thinking to catch things you might not have considered.

---

## Why Thinking Guides?

**Most bugs and tech debt come from "didn't think of that"**, not from lack of skill:

- Didn't think about what happens at layer boundaries → cross-layer bugs
- Didn't think about code patterns repeating → duplicated code everywhere
- Didn't think about edge cases → runtime errors
- Didn't think about future maintainers → unreadable code

These guides help you **ask the right questions before coding**.

---

## Available Guides

| Guide | Purpose | When to Use |
|-------|---------|-------------|
| [Code Reuse Thinking Guide](./code-reuse-thinking-guide.md) | Identify patterns and reduce duplication | When you notice repeated patterns |
| [Cross-Layer Thinking Guide](./cross-layer-thinking-guide.md) | Think through data flow across layers | Features spanning multiple layers |

---

## Quick Reference: Thinking Triggers

### When to Think About Cross-Layer Issues

- [ ] Feature touches 3+ layers (API, Service, Component, Database)
- [ ] Data format changes between layers
- [ ] Multiple consumers need the same data
- [ ] You're not sure where to put some logic

→ Read [Cross-Layer Thinking Guide](./cross-layer-thinking-guide.md)

### When to Think About Code Reuse

- [ ] You're writing similar code to something that exists
- [ ] You see the same pattern repeated 3+ times
- [ ] You're adding a new field to multiple places
- [ ] **You're modifying any constant or config**
- [ ] **You're creating a new utility/helper function** ← Search first!

→ Read [Code Reuse Thinking Guide](./code-reuse-thinking-guide.md)

---

## Pre-Modification Rule (CRITICAL)

> **Before changing ANY value, ALWAYS search first!**

```bash
# Search for the value you're about to change
grep -r "value_to_change" .
```

This single habit prevents most "forgot to update X" bugs.

---

## Agent Execution Discipline (CRITICAL)

> **Main agent runs tasks inline. Do NOT dispatch sub-agents ("子线程", `Agent` / `Task` tool) to run task work for you.**

Applies to every workflow phase — `trellis-brainstorm`, `trellis-before-dev`, `trellis-implement` semantics, `trellis-check`, `trellis-update-spec`, `trellis-break-loop`, `trellis-finish-work` — and to ad-hoc requests.

### Forbidden

- Spawning a sub-agent (`Agent` / `Task` tool, including `general-purpose`, `Explore`, `trellis-implement`, `trellis-check`, `Plan`) to execute steps the main agent should perform directly: reading files, running tests/lint/type-check, editing code, drafting commits, answering the user.
- Treating sub-agents as a "context offload" mechanism for routine work. Sub-agent transcripts are opaque to the user; the main agent loses traceability of every decision the sub-agent made.
- Wrapping a single shell command, file read, or edit in a sub-agent call.

### Allowed

- The main agent calls `Bash`, `Read`, `Edit`, `Write`, `Glob`, `Grep`, `WebFetch`, `WebSearch` directly.
- `trellis-research` sub-agent dispatch is permitted **only** for research-heavy work that would otherwise burn 3+ inline `WebFetch` / `WebSearch` / `gh api` calls (per workflow-state breadcrumb). Even then, prefer 1–2 targeted inline calls when feasible.
- A user explicitly asking the main agent to dispatch a sub-agent overrides this rule for that turn only.

### Why

- Sub-agents run with no shared context; their answers must be re-validated by the main agent anyway, so dispatching them for routine work doubles the cost.
- The main agent's transcript is the single source of truth the user reviews. Sub-agent fan-out makes "trust but verify" impossible at human review speed.
- `/trellis-check` and similar quality skills are explicitly written as a single-thread checklist — running them in the main thread keeps spec/lint/test results inline and reviewable.

### Reference

- Workflow-state breadcrumb (`.claude/hooks/inject-workflow-state.py` output) reinforces this: only `trellis-research` is named as an allowed dispatch target, and only for the research-heavy threshold.

---

## How to Use This Directory

1. **Before coding**: Skim the relevant thinking guide
2. **During coding**: If something feels repetitive or complex, check the guides
3. **After bugs**: Add new insights to the relevant guide (learn from mistakes)

---

## Contributing

Found a new "didn't think of that" moment? Add it to the relevant guide.

---

**Core Principle**: 30 minutes of thinking saves 3 hours of debugging.
