# End-to-End Reference Validation

## Goal

Validate the demo-inspired editing package end to end after the completed child
tasks: publishing metadata/cover planning, ASS subtitle styling, teaser-first
edit plans, local BGM/SFX audio instructions, and punch-in zoom transforms.

This task is a final integration pass. It should catch cross-stage wiring gaps,
not reopen the deferred external reference insert feature.

## User Value

The individual features already have focused tests. The remaining risk is that
they do not work together as an upload-style package: metadata may not prefer
highlight cues, edit plans may not render when exporter flags are enabled, ASS
sidecars may not be used inside edit-plan rendering, BGM/SFX input indexes may
collide, or punch-in filters may disappear when subtitles/audio are also active.

## Confirmed Facts

- `data/demo1` and `data/demo2` contain large local reference videos, cover
  images, and ASS subtitle files. They are user-local reference material and
  must not be committed or used as automated test fixtures.
- The current task scope excludes external reference inserts / "引经据典" clips.
- Existing tests cover most single-feature behavior:
  - copywriter metadata and optional cover rendering
  - SRT to ASS conversion and ASS exporter wiring
  - teaser-before-main edit-plan generation and rendering
  - local audio bed/SFX instructions and exporter audio mixing
  - punch-in transform model and exporter filter graph
  - postprocess stage order includes `edit-planner`
- Existing exporter command tests cover edit plan, audio, and zoom separately,
  but do not yet assert the combined render path with ASS subtitles, audio
  mixing, and punch-in transforms in one plan.
- `PostProcessService` runs:
  `stage-hints-semantic -> segmenter -> subtitles -> highlight-planner ->
  edit-planner -> exporter -> copywriter`.
- Exporter only writes `ExportAsset` rows when ffmpeg export succeeds; pure
  no-ffmpeg runs defer export.

## Requirements

- Add an automated integration-style validation that exercises the completed
  editing package across stage boundaries using small generated or stubbed
  fixtures.
- Verify metadata/copy output prefers high-signal highlight cues and produces
  title, summary, cover lines, tags, and evidence.
- Verify edit plans preserve teaser-before-main ordering, keep exactly one full
  main segment, and do not emit insert/source-path segments.
- Verify exporter command construction for the combined path:
  edit-plan rendering + ASS subtitle burn-in + BGM/SFX audio mixing + punch-in
  transform.
- Verify the postprocess stage order remains compatible with the editing
  package.
- Add a manual reference checklist that maps demo-derived expectations to
  generated artifacts and commands the operator can run locally.
- Keep automated tests small and deterministic. Do not process the full
  `data/demo1` or `data/demo2` videos in CI-style tests.
- If a real ffmpeg smoke test is added, it must use a tiny generated media file
  and skip cleanly when ffmpeg/ffprobe or required render support is unavailable.

## Acceptance Criteria

- [x] Focused regression tests still pass for copywriter, subtitles, editing,
      exporter, postprocess, and config.
- [x] A new integration/validation test covers highlight cues flowing into
      edit-plan/copywriter artifacts without relying on `data/demo*` files.
- [x] A combined exporter command test asserts ASS sidecar usage, teaser/main
      concat, punch-in filters, BGM/SFX inputs, and `amix` all appear together.
- [x] The combined validation confirms no `role="insert"` or `source_path`
      segment is required for the current scope.
- [x] A manual reference checklist exists and covers cover readability,
      teaser-before-main behavior, subtitle position/style, low-volume BGM/SFX,
      and punch-in emphasis.
- [x] Broad validation commands are documented and run before finishing the
      parent task.

## Out Of Scope

- Implementing external reference inserts / "引经据典" clips.
- Rendering or comparing full `data/demo1` / `data/demo2` videos automatically.
- Pixel-perfect visual matching to the demo covers or subtitles.
- Automatic audio loudness analysis that separates BGM from game/voice audio.
- New UI for manually reviewing generated edit plans.

## Open Questions

None block planning. Recommended scope is a contract-level integration test, one
combined exporter command regression, an optional tiny ffmpeg smoke test if it is
cheap, and a manual reference checklist.
