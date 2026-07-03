# Validation Report

Generated: 2026-07-03

Scope:

- `session-20260617073649-4b5ec478` match 02
- `session-20260617073651-cf11bf9e` matches 02, 03, 04
- Publish preset with highlight planner, edit planner, ASS burn-in, edit-plan export, copywriter packaging

Commands run:

```powershell
$env:ARL_POSTPROCESS_PRESET='publish'
.\.venv\Scripts\python.exe -m arl.cli highlight-planner --session-id session-20260617073649-4b5ec478 --match-index 2 --force-reprocess
.\.venv\Scripts\python.exe -m arl.cli highlight-planner --session-id session-20260617073651-cf11bf9e --match-indices 2,3,4 --force-reprocess
.\.venv\Scripts\python.exe -m arl.cli edit-planner --session-id session-20260617073649-4b5ec478 --match-index 2 --force-reprocess
.\.venv\Scripts\python.exe -m arl.cli edit-planner --session-id session-20260617073651-cf11bf9e --match-indices 2,3,4 --force-reprocess
.\.venv\Scripts\python.exe -m arl.cli exporter --session-id session-20260617073649-4b5ec478 --match-index 2 --force-reprocess
.\.venv\Scripts\python.exe -m arl.cli exporter --session-id session-20260617073651-cf11bf9e --match-indices 2,3,4 --force-reprocess
.\.venv\Scripts\python.exe -m arl.cli copywriter --session-id session-20260617073649-4b5ec478 --match-index 2 --force-reprocess
.\.venv\Scripts\python.exe -m arl.cli copywriter --session-id session-20260617073651-cf11bf9e --match-indices 2,3,4 --force-reprocess
```

## Summary

All regenerated exports are 1920x1080 at about 8.1 Mbps. KDA kill/death cue uncovered count is 0 for every sample. Final source-time gaps are capped at 45.0s. Teasers were not forced because the current plans had no high-confidence `highlight_keyword` teaser; main-only plans were emitted instead. Default coin SFX is present on `condensed_key_event` moments, rate-limited to 4 hits per edit.

| Sample | Export | Export min | Bitrate | Target min | Plan min | Max key event | Max source gap | Continuity | KDA uncovered | Subtitle active | Long no-sub gaps | BGM | SFX | Zoom |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `4b5ec478` m02 | `data\exports\bilibili\session-20260617073649-4b5ec478_match02.mp4` | 12.50 | 8.09 Mbps | 9.62 | 12.50 | 185.3s | 45.0s | 8.0% | 0/9 | 39.1% | 7, max 18.5s | 0 | 4 | 1 |
| `cf11bf9e` m02 | `data\exports\bilibili\session-20260617073651-cf11bf9e_match02.mp4` | 11.49 | 8.15 Mbps | 9.46 | 11.48 | 111.7s | 45.0s | 6.1% | 0/8 | 47.0% | 7, max 26.1s | 2 | 4 | 1 |
| `cf11bf9e` m03 | `data\exports\bilibili\session-20260617073651-cf11bf9e_match03.mp4` | 13.72 | 8.15 Mbps | 9.31 | 13.72 | 212.1s | 45.0s | 11.1% | 0/6 | 45.4% | 7, max 42.5s | 2 | 4 | 1 |
| `cf11bf9e` m04 | `data\exports\bilibili\session-20260617073651-cf11bf9e_match04.mp4` | 13.77 | 8.15 Mbps | 9.28 | 13.76 | 86.3s | 45.0s | 14.8% | 0/11 | 39.5% | 11, max 34.3s | 2 | 4 | 1 |

## Exceptions

- `4b5ec478` m02 has two `condensed_key_event` windows over 120s. They cover dense speech plus KDA changes, including a long kill/death protected span; KDA uncovered count remains 0.
- `cf11bf9e` m03 has two `condensed_key_event` windows over 120s. They are protected by dense speech/KDA coverage, including a long OCR reading gap around a kill/death change; KDA uncovered count remains 0.
- `cf11bf9e` m03 and m04 exceed the default 10% continuity target. The exception is required to keep adjacent source-time gaps at or below 45.0s after budget capping.
- Long no-subtitle gaps remain only inside `condensed_key_event` or `condensed_key_event + condensed_continuity` spans, not ordinary context windows. These are treated as protected silent fight/objective/death/KDA context.
- `4b5ec478` m02 has BGM count 0 because source-music detection reported an existing persistent music-like bed; SFX is still present.

## Copy/Cover Outputs

| Sample | Title | Cover lines |
|---|---|---|
| `4b5ec478` m02 | 装没钱人设 炒股经济学 | 装没钱人设 炒股经济学 |
| `cf11bf9e` m02 | 堆場式是咋的 就對面的人他也會 我玩的好不應該我被打爆了為什麼... | 堆場式是咋的 / 就對面的人他也會 / 我玩的好不應該我被打爆了 / 為什麼... |
| `cf11bf9e` m03 | 拿到我还是很有钱 A然后他会追我 不能后态多些 | 拿到我还是很有钱 / A然后他会追我 / 不能后态多些 |
| `cf11bf9e` m04 | 被粉丝认出来 过几个月又有钱了然后冲进去结果滤了 对呀是会亏的呀我的意思就是说... | 被粉丝认出来 / 过几个月又有钱了然后冲进 / 去结果滤了 / 对呀是会亏的呀我的意思就 |

