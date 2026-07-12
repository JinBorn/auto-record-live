# Validation Report

## Automated Checks

- Focused highlight/config tests: 88 passed.
- Full project suite before the final performance refinement: 727 passed.
- `compileall -q src tests`: passed.
- `git diff --check`: passed (line-ending notices only).

## Real Match Plan Validation

Validated against `session-20260617073649-4b5ec478`, match 2, using copied manifests
inside a system temporary directory. Existing workspace assets were not rewritten.

To isolate the new continuity path from the pre-existing whole-match OCR/density cost,
the validation run disabled KDA scanning and match-level visual density. Combat-local
video activity remained enabled with production defaults.

Result:

- Runtime: 5.4 seconds.
- Combat evidence mode: `video+cue`.
- Detected protected combat intervals: 1.
- Protected combat duration: 58.6 seconds.
- Output windows: 29.
- Retained duration: 544.4 seconds.
- Maximum adjacent source gap: 45.0 seconds.
- Budget: 647.944 seconds; no budget exception required.
- Internal low-value trimming still removed 28.6 seconds outside protected combat,
  demonstrating that condensed editing remains active globally.

The earlier sequential decoder prototype exceeded the validation timeout. The final
implementation opens each relevant recording span once and performs timestamp seeks on
that handle; the same sample completed in 5.4 seconds.

