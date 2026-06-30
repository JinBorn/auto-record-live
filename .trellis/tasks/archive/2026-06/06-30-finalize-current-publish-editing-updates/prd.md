# Finalize Current Publish Editing Updates

## Goal

Validate and finish the current uncommitted publish-editing, segmentation, audio, copywriting, status, and README updates without broadening the scope.

## Requirements

- Preserve the recent user-facing fixes:
  - teaser clips remain optional and are not forced from generic condensed windows
  - BGM starts with main content, not over teaser content
  - BGM selection is content/style-aware and avoids always choosing the same tracks when equivalent library candidates exist
  - BGM stays subordinate to source audio through mix logic rather than fixed timestamp-specific checks
  - condensed exports avoid cutting active streamer speech mid-sentence
  - weak short titles gain enough context to be understandable
- Keep the long-running recording documentation current:
  - README explains `.env` setup, `windows-supervisor.ps1`, optional autostart, logs, manual selected recording, postprocess, publish preset, and troubleshooting
  - README remains valid UTF-8 and does not retain mojibake from the previous version
- Verify the existing code and test changes rather than starting a new feature branch of behavior.
- Do not commit or include local runtime data under `data/`.

## Acceptance Criteria

- [x] Relevant targeted tests pass for editing, exporter, highlight planner, copywriter, segmenter, status, config, and vision changes.
- [x] README checks pass for whitespace/encoding and the documented commands match current CLI/script entrypoints.
- [x] Current uncommitted changes are reviewed for accidental runtime data or unrelated files before commit.
- [x] Trellis task is updated through finish workflow after validation, including any necessary spec/journal updates.

## Notes

- This is a lightweight finish/validation task. PRD-only planning is sufficient unless validation uncovers new design work.
- Existing uncommitted source changes predate this task; do not revert them unless explicitly requested.
