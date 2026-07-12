# Publish a Single Default Cover File

## Goal

Remove the duplicate rank-one cover image from each published video directory. The
normal package should contain `cover.jpg` only, not two byte-identical files named
`cover.jpg` and `cover-01.jpg`.

## Confirmed Facts

- Copywriter currently publishes `package.cover_path` as `cover.jpg`.
- It then iterates `package.cover_candidates` and publishes rank 1 again as
  `cover-01.jpg`.
- Real export directories contain byte-identical `cover.jpg` and `cover-01.jpg` files.
- The default `ARL_COPY_COVER_MAX_CANDIDATES` is already 1, so the normal workflow
  needs only one published cover.
- Processed candidate files and the `cover_candidates` manifest remain useful internal
  evidence and do not need to be removed.

## Requirements

- Publish the top-ranked cover only once as `cover.jpg`.
- Rank-one `CoverCandidate.published_path` must reference the same `cover.jpg` path so
  package metadata and completeness checks remain consistent.
- Do not create `cover-01.jpg` for the rank-one candidate.
- When an operator explicitly configures more than one candidate, preserve additional
  choices as `cover-02.jpg`, `cover-03.jpg`, and so on.
- Existing packages that already contain `cover-01.jpg` must be treated as needing
  repair/cleanup so a rerun removes the stale duplicate.
- Do not delete internal processed candidate images.

## Acceptance Criteria

- [x] A normal one-candidate package contains `video.*`, `cover.jpg`, and `upload.txt`,
      with no `cover-01.jpg`.
- [x] The published package row records `published_cover_path=.../cover.jpg`.
- [x] The rank-one candidate records the same path in `published_path`.
- [x] Output-completeness checks succeed without `cover-01.jpg`.
- [x] A stale `cover-01.jpg` is removed when the package is republished.
- [x] Explicit rank-two and later candidates still publish under their numbered names.
- [x] Copywriter and reset tests remain green.

## Out of Scope

- Removing internal cover candidate ranking or processed candidate images.
- Changing cover rendering, scoring, text, or frame selection.

## Open Questions

- None.
