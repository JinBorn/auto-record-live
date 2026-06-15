# Fix export quality loss and incomplete match boundaries

## Goal

Fix two critical issues with exported match videos:
1. Videos were truncated to 3-4 minute highlights instead of full 20-30 minute matches
2. Video quality severely degraded (2.2 Mbps output from 3.2-3.9 Mbps source)

Users need complete, high-quality match recordings suitable for replay and upload.

## Problem Context

### Issue 1: Incomplete Videos
- Highlight Planner was enabled by default, condensing full matches into highlight reels
- 31-minute match → 3-4 minute export
- Users expected complete matches, not highlights

### Issue 2: Quality Loss  
- CRF (Constant Rate Factor) mode produced variable bitrate around 2.2 Mbps
- Source recordings: 3.2-3.9 Mbps
- **Quality loss: ~40%**, causing visible degradation
- No configuration option for fixed bitrate mode existed

## Solution Implemented

### 1. Disable Highlight Condensing
**Change**: `.env`
```bash
ARL_HIGHLIGHT_PLANNER_ENABLED=0
```

### 2. Add Fixed Bitrate Support
**Changes**:
- `src/arl/config.py`: Added `ffmpeg_bitrate` and `ffmpeg_max_bitrate` fields
- `src/arl/exporter/service.py`: Created `_video_quality_args()` method
- `.env`: Set `ARL_EXPORT_FFMPEG_BITRATE=4000k`, `ARL_EXPORT_FFMPEG_MAX_BITRATE=5000k`

**Logic**: Prefer fixed bitrate (quality preservation) over CRF (size optimization)

## Verification Results

### Before Fix
- Duration: 3-4 minutes (highlights only)
- Bitrate: 2.2-2.5 Mbps  
- Example: 31-min match → 240 MB (4-min highlight)

### After Fix
| Video | Duration | Bitrate | File Size | Source |
|-------|----------|---------|-----------|--------|
| match01 | 31 min | 4.45 Mbps | 987 MB | 3.18 Mbps |
| match02 | 24 min | 4.41 Mbps | 757 MB | 3.87 Mbps |
| match03 | 18 min | 3.89 Mbps | 503 MB | ~3.5 Mbps |

✅ Complete matches exported  
✅ Bitrate equals or exceeds source  
✅ No quality loss

## Requirements

1. Export complete match boundaries without truncation
2. Preserve source video quality (bitrate ≥ source)
3. Support fixed bitrate mode via environment config
4. Maintain backward compatibility with CRF mode
5. Work with GPU hardware encoding (NVENC)

## Acceptance Criteria

- [x] `ARL_HIGHLIGHT_PLANNER_ENABLED=0` disables highlight condensing
- [x] `ARL_EXPORT_FFMPEG_BITRATE` enables fixed bitrate mode
- [x] Fixed bitrate mode uses `-b:v`, `-maxrate`, `-bufsize` FFmpeg args
- [x] CRF mode still works when bitrate not configured (backward compat)
- [x] Config changes loaded from `.env` correctly
- [x] Exported videos have complete duration (match boundaries)
- [x] Exported bitrate ≥ source bitrate
- [x] GPU encoding works with fixed bitrate
- [x] Verified with real recordings (3 matches, 18-31 min each)

## Files Changed

1. `.env` - Export configuration
2. `src/arl/config.py` - Config schema + env loading
3. `src/arl/exporter/service.py` - Quality args method
4. `data/tmp/exporter-state.json` - Runtime state (cleared for testing)

## Notes

- **Implementation already complete** - this task documents and finalizes the work
- Fixed bitrate chosen over CRF tuning because NVENC CRF behavior differs from CPU encoding
- Bufsize set to 8M (~2x max bitrate) for smooth encoding
- Original CRF mode preserved for users preferring smaller files
