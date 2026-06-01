# Enforce actual-bitrate / 1080p quality gate across probe and recording

## Goal

Make "below-1080p or low-bitrate output" automatically rejected by the
recording pipeline rather than landing as a quietly-bad fixture in
`data/raw/`. Today the quality gate is metadata-only (qn / tier / advertised
bandwidth) and a stream can pass the gate while the CDN actually delivers
half the advertised bitrate.

## User Value

Operator stated explicitly: **"1080p 以下分辨率及低码率归为不可用状态."** The
previous fixture run (`archive/2026-05/05-27-record-dual-platform-1080p-fixtures`)
produced a bilibili fixture that passed every existing gate yet only delivered
3.81 Mbps real-world bitrate while the upstream API claimed
`origin_bitrate=8723`. Today there is no mechanism to detect that divergence;
the operator only finds out after a 30-minute capture by post-hoc `ffprobe`.

## Confirmed Facts (from code inspection, 2026-05-28)

- **bilibili probe** already implements a bitrate gate:
  `src/arl/windows_agent/bilibili_probe.py:368-375` rejects with
  `quality_below_min_bitrate:<actual><<configured>` when
  `_extract_bitrate_kbps()` returns a value below
  `settings.min_stream_bitrate_kbps` (default `4500` per
  `src/arl/config.py:81`, env `ARL_BILIBILI_MIN_STREAM_BITRATE_KBPS`).
  But `_extract_bitrate_kbps` reads `bandwidth / bitrate / bit_rate` from the
  API codec entry — these are **advertised** values; the actual CDN delivery
  bitrate is not checked. The 3.81 Mbps bilibili fixture passed because its
  codec entry advertised ~8723 kbps (well above the 4500 floor).
- **douyin probe** has only a tier gate
  (`src/arl/windows_agent/probe.py:503-521`) keyed off URL filename markers
  `_origin / _uhd / _hd / _sd / _md / _ld`. No bitrate gate at all.
  `min_quality_tier="uhd"` (config.py:62).
- **recorder** writes `data/raw/<session_id>/recording-source.mp4` with
  `ffmpeg -c copy` (`src/arl/recorder/service.py:457-477`) and does not run any
  post-record validation; success is defined purely as ffmpeg exit code 0.
- `qn=20000` (B 站 4K) is **not** requested today — `bilibili_probe.py:143`
  pins the request to `qn=10000`. Going higher needs a `大会员` SESSDATA AND
  the streamer to have enabled 4K push; either condition missing falls back
  silently.
- Cookie health is platform-scoped today: `arl/windows_agent/cookie_health.py`
  tracks `ARL_DOUYIN_COOKIE` and `ARL_BILIBILI_SESSDATA` separately; there is
  no concept of "membership tier of the logged-in account" — anonymous vs
  cookied is the only distinction.

## Requirements

- Select the highest available quality for each live room and platform first.
  The implementation must not cap other rooms/platforms to accommodate a weak
  Bilibili CDN result; fallback should move downward only when the higher
  quality candidate is unavailable or unrecordable.
- Add recorder-time actual-bitrate validation for direct-stream recordings.
  Validation must measure the bytes delivered by the selected CDN stream
  during the early recording window rather than trusting probe/API-advertised
  bitrate.
- Add recorder-time actual-resolution validation for direct-stream recordings.
  The actual recorded output must be 1080p or higher; output below 1080p is
  unusable even if bitrate is high enough.
- Keep recurring probe behavior scoped to the existing qn / tier /
  advertised-bitrate checks. This task must not add recurring probe-time
  pre-records that multiply CDN load every poll cycle.
- When early quality validation proves the selected stream is unusable, the
  recorder must stop the ffmpeg child, remove the partial
  `recording-source.mp4`, emit a recorder audit event with observed resolution
  and bitrate diagnostics, and avoid writing a usable `RecordingAsset`.
- The orchestrator must recognize the recorder-side quality rejection as a
  terminal quality failure for that recording job while leaving the live
  session eligible for a later `live_started` refresh to create a new
  recording job.
- Any minimum-bitrate threshold must be treated only as an auxiliary
  unusable-quality guard/diagnostic. The hard acceptance floor is actual
  resolution >=1080p.
- Browser-capture recordings are out of the initial quality gate unless the
  implementation discovers a low-risk way to measure them with the same
  contract.

## Acceptance Criteria

- Candidate selection prefers the highest quality available for the room and
  platform; tests or fixtures prove the implementation does not force all
  platforms/rooms down to a Bilibili-specific bitrate.
- A direct-stream recording whose early observed resolution is below 1080p is
  terminated within the early validation window, its partial
  `recording-source.mp4` is removed, and no recording asset is emitted for
  that job.
- The recorder audit log contains `quality_below_actual_resolution`
  diagnostics including observed width/height, required minimum resolution,
  observed bitrate when available, session id, job id, source type, and
  decision/failure fields compatible with existing recorder audit validation.
- The orchestrator consumes the recorder quality event without treating it as
  an unknown event, marks the recording job terminal, and clears active job
  linkage when appropriate so a later probe event can create fresh work for
  the same live session.
- Tests cover pass/fail cases for the resolution gate, bitrate diagnostics,
  cleanup of the partial file, and orchestrator handling of the quality
  rejection event.
- Existing Bilibili advertised-bitrate probe tests and Douyin tier-gate tests
  continue to pass unchanged.

## Out of Scope (provisional)

- Implementing a B 站 4K (qn=20000) end-to-end record path. Even with a 大会员
  SESSDATA, the upstream streamer must have enabled 4K push; without that, the
  result is identical to `bluray`. May be a follow-up if 4K-enabled streamers
  enter the test set.
- Re-encoding low-bitrate streams to artificially-higher bitrate. `-c copy` is
  intentional (zero CPU cost, exact source bytes). Re-encoding would defeat the
  fixture purpose.
- Automatic SESSDATA refresh / login flow. Operator supplies cookies manually
  per the existing `cookie health gate` task (`05-14-cookie-health-gate-oncall-workflow`).

## Confirmed Decisions

- **Detection point: recording-time early validation (Q1=B, 2026-05-28;
  refined 2026-06-01).**
  recorder starts ffmpeg as today; ffprobe measures actual resolution and
  bitrate immediately after the direct-stream capture attempt.
  Below 1080p is unusable: clean up the partial
  `recording-source.mp4`, emit `quality_below_actual_resolution` (with the
  observed width/height and bitrate diagnostics), and let the orchestrator roll a new session on the next
  `live_started`. probe layer is **not** changed for this gate (probe still
  enforces qn / tier / advertised-bitrate gates as today).

  Rationale: probe-time short pre-record would multiply CDN / CPU load on
  every 15s probe cycle; post-record 30-min verdict wastes capacity for the
  full window. Recording-time early validation costs ~30s per false start.

- **Scope: 4K / qn=20000 path deferred (Q2=A, 2026-05-28).** This task
  delivers only the actual-bitrate gate at recording-time. `qn` requests
  stay at `10000`; candidate selection / `大会员` cookie tier detection /
  4K-enabled streamer test fixtures are explicitly out of scope and become a
  follow-up task once a 4K-pushing streamer is available for end-to-end
  testing.

- **Quality policy: prefer highest available, do not cap globally
  (2026-06-01).** The operator rejected a fixed bitrate cap. The system should
  choose the highest available quality per live room/platform and only step
  down when the higher candidate is unavailable or unrecordable. Bilibili's
  observed low actual bitrate must not reduce the quality selected for other
  rooms or platforms.

- **Unusable threshold: actual resolution must be 1080p or higher
  (2026-06-01).** Keep an unusable-quality gate, but base the hard failure on
  actual recorded resolution. Anything below 1080p is unusable. Bitrate remains
  useful for diagnostics and future tuning, but it must not become a global cap
  that lowers other rooms/platforms. This decision supersedes earlier
  "bitrate floor" wording in this task.

## Open Questions (blocking planning)

1. **Detection point** — ~~resolved as Q1=B above~~.
2. **Unusable-quality threshold** - resolved:
   resolved: actual resolution must be 1080p or higher; below 1080p is
   unusable.
3. **4K / 大会员 path** — ~~resolved as Q2=A above (deferred to follow-up).~~

## Notes

- Test rooms supplied 2026-05-28: 抖音
  `https://live.douyin.com/190626328582` (小柴), 哔哩哔哩
  `https://live.bilibili.com/21733448` (阿帅派克). Use as live targets for
  probe/recorder validation during implementation.
- Probably a complex task — touches probe + recorder + orchestrator
  + config + cookie_health. Will need `design.md` + `implement.md` before
  `task.py start`.
