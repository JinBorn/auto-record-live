# Export Configuration Guidelines

> Export quality configuration patterns, FFmpeg encoding strategies, and quality preservation contracts.

---

## Overview

The exporter service (`src/arl/exporter/service.py`) transcodes match boundaries from raw recordings into final deliverable videos. This document covers:

1. Quality control configuration (CRF vs fixed bitrate)
2. Hardware vs software encoding tradeoffs
3. Highlight condensing vs full match export
4. Common quality preservation patterns

---

## Quality Control Modes

### Mode 1: Fixed Bitrate (Quality Preservation)

**When to use**: Preserve source recording quality without degradation

**Configuration**:
```bash
ARL_EXPORT_FFMPEG_BITRATE=4000k
ARL_EXPORT_FFMPEG_MAX_BITRATE=5000k
ARL_EXPORT_FFMPEG_PRESET=p7  # NVENC preset
ARL_EXPORT_USE_HARDWARE_ENCODING=1
```

**FFmpeg args generated**:
```bash
-b:v 4000k -maxrate 5000k -bufsize 8M -preset p7
```

**Use case**: 
- Source recordings at 3-4 Mbps need output at same or higher bitrate
- User wants broadcast-quality exports
- File size is less important than quality

**Behavior**:
- Output bitrate guaranteed ≥ configured value
- Complex scenes can burst up to maxrate
- Predictable file sizes: ~30 MB/min at 4 Mbps

### Mode 2: CRF (Size Optimization)

**When to use**: Balance quality and file size, prefer smaller files

**Configuration**:
```bash
# Don't set ARL_EXPORT_FFMPEG_BITRATE
ARL_EXPORT_FFMPEG_CRF=18
ARL_EXPORT_FFMPEG_PRESET=slow
```

**FFmpeg args generated**:
```bash
-crf 18 -preset slow
```

**Use case**:
- Storage constrained
- Acceptable quality loss (~20-40%)
- Variable bitrate acceptable

**Behavior**:
- Output bitrate varies based on scene complexity
- Smaller files than fixed bitrate mode
- Quality varies: simple scenes use less bitrate, complex scenes use more

---

## Decision Rule: When to Use Which Mode

```python
def choose_quality_mode(requirements):
    if requirements.preserve_source_quality:
        return "fixed_bitrate"  # Set ARL_EXPORT_FFMPEG_BITRATE
    elif requirements.minimize_file_size:
        return "crf"  # Use default CRF
    else:
        return "fixed_bitrate"  # Default to quality preservation
```

### Common Scenario Matrix

| Source Bitrate | Target Use Case | Recommended Mode | Config |
|----------------|-----------------|------------------|--------|
| 3-4 Mbps | Broadcast/Upload | Fixed 4 Mbps | `BITRATE=4000k` |
| 3-4 Mbps | Archive/Storage | CRF 18 | `CRF=18` |
| 2-3 Mbps | Web sharing | Fixed 3 Mbps | `BITRATE=3000k` |
| 2-3 Mbps | Low bandwidth | CRF 23 | `CRF=23` |

---

## Hardware Encoding Considerations

### NVENC (NVIDIA GPU Encoding)

**Key Differences from CPU Encoding**:

1. **CRF behavior differs**: NVENC CRF mode often produces **lower bitrate** than CPU libx264 at the same CRF value
   - Example: libx264 CRF 18 → 3.5 Mbps, h264_nvenc CRF 18 → 2.2 Mbps
   - **Recommendation**: Use fixed bitrate mode with NVENC for quality preservation

2. **Preset names differ**:
   - CPU: `ultrafast`, `veryfast`, `fast`, `medium`, `slow`, `slower`, `veryslow`
   - NVENC: `p1` (fastest) through `p7` (highest quality)
   - **Always use `p7` with NVENC** for quality work

3. **Speed advantage**: 10-20x faster than CPU encoding
   - 30-minute video: NVENC ~3 min, CPU ~30-60 min

**Configuration**:
```bash
ARL_EXPORT_USE_HARDWARE_ENCODING=1
ARL_EXPORT_FFMPEG_VIDEO_CODEC=h264
ARL_EXPORT_FFMPEG_PRESET=p7
```

**When hardware encoding fails**: The service falls back to CPU automatically (not implemented in current version, but codec selection supports both)

---

## Highlight Condensing vs Full Match Export

### Full Match Export (Default)

**Configuration**:
```bash
ARL_HIGHLIGHT_PLANNER_ENABLED=0
ARL_EXPORT_USE_HIGHLIGHT_PLANS=0
```

**Behavior**:
- Exports complete match from `started_at_seconds` to `ended_at_seconds`
- No time windows applied
- Duration = full match length (15-40 minutes typical)

**Use case**: 
- User wants complete game recordings
- Review full gameplay
- Upload to platforms that handle long videos

### Highlight Condensing

**Configuration**:
```bash
ARL_HIGHLIGHT_PLANNER_ENABLED=1
ARL_EXPORT_USE_HIGHLIGHT_PLANS=1
```

**Behavior**:
- Reads `HighlightPlanAsset` from `data/tmp/highlight-plans.jsonl`
- Exports only time windows marked as highlights
- Uses FFmpeg select filters to concatenate segments
- Duration = sum of highlight windows (3-8 minutes typical)

**Use case**:
- Social media clips
- Quick replay summaries
- Bandwidth-constrained sharing

---

## Quality Preservation Contract

### Signatures

```python
# Config schema
class ExportSettings(BaseModel):
    ffmpeg_bitrate: str | None = None          # e.g., "4000k"
    ffmpeg_max_bitrate: str | None = None      # e.g., "5000k"
    ffmpeg_crf: int = 18                       # Fallback when bitrate not set
    ffmpeg_preset: str = "slow"                # CPU or NVENC preset
    use_hardware_encoding: bool = False
    use_highlight_plans: bool = False
```

```python
# Service method
def _video_quality_args(self) -> list[str]:
    """Generate quality control arguments: bitrate or CRF mode."""
    args = ["-preset", self.settings.export.ffmpeg_preset]
    
    if self.settings.export.ffmpeg_bitrate:
        # Fixed bitrate mode (quality preservation)
        args.extend(["-b:v", self.settings.export.ffmpeg_bitrate])
        if self.settings.export.ffmpeg_max_bitrate:
            args.extend(["-maxrate", self.settings.export.ffmpeg_max_bitrate])
            args.extend(["-bufsize", "8M"])
    else:
        # CRF mode (size optimization)
        args.extend(["-crf", str(self.settings.export.ffmpeg_crf)])
    
    return args
```

### Validation & Error Matrix

| Condition | Error / Behavior |
|-----------|------------------|
| `ffmpeg_bitrate` set without `ffmpeg_max_bitrate` | Valid - uses only average bitrate |
| `ffmpeg_bitrate` and `ffmpeg_crf` both set | Bitrate takes precedence, CRF ignored |
| Neither `ffmpeg_bitrate` nor `ffmpeg_crf` set | Falls back to CRF=18 (default) |
| `use_hardware_encoding=1` but no GPU available | FFmpeg command fails, service falls back to placeholder |
| `bufsize` too small for `maxrate` | FFmpeg encoding may stall; use `bufsize ≥ 2 * maxrate` |

### Good/Base/Bad Cases

**Good - Quality Preservation**:
```bash
# Source: 3.5 Mbps
ARL_EXPORT_FFMPEG_BITRATE=4000k
ARL_EXPORT_FFMPEG_MAX_BITRATE=5000k
# Output: 4.0-4.5 Mbps (quality preserved)
```

**Base - Balanced**:
```bash
# Source: 3.5 Mbps  
ARL_EXPORT_FFMPEG_CRF=18
# Output: ~2.8 Mbps (minor quality loss, smaller file)
```

**Bad - Excessive Quality Loss**:
```bash
# Source: 3.5 Mbps
ARL_EXPORT_FFMPEG_CRF=23  # Default in older versions
ARL_EXPORT_FFMPEG_PRESET=veryfast
# Output: ~1.5 Mbps (40%+ quality loss, visible artifacts)
```

### Tests Required

**Unit tests** (in `tests/test_config.py`):
- [ ] `ffmpeg_bitrate` loads from env correctly
- [ ] `ffmpeg_max_bitrate` loads from env correctly  
- [ ] Defaults preserved when env not set

**Integration tests** (in `tests/pipeline/test_ffmpeg_resilience.py`):
- [ ] Fixed bitrate mode generates correct FFmpeg args
- [ ] CRF mode generates correct FFmpeg args when bitrate unset
- [ ] Hardware encoding uses NVENC codec when enabled
- [ ] Exported bitrate ≥ configured bitrate (tolerance ±10%)

**E2E verification** (manual):
- Export real recording and verify `ffprobe` bitrate matches config
- Compare visual quality against source

### Wrong vs Correct

#### Wrong - Using CRF with NVENC for Quality Preservation

```bash
# NVENC CRF produces lower bitrate than expected
ARL_EXPORT_USE_HARDWARE_ENCODING=1
ARL_EXPORT_FFMPEG_CRF=10
# Output: 2.2 Mbps from 3.5 Mbps source (quality loss)
```

**Why it's wrong**: NVENC CRF behavior differs from CPU encoding; same CRF value produces lower bitrate

#### Correct - Using Fixed Bitrate with NVENC

```bash
# Fixed bitrate guarantees output quality
ARL_EXPORT_USE_HARDWARE_ENCODING=1
ARL_EXPORT_FFMPEG_BITRATE=4000k
ARL_EXPORT_FFMPEG_MAX_BITRATE=5000k
ARL_EXPORT_FFMPEG_PRESET=p7
# Output: 4.0-4.5 Mbps (quality preserved, 10x faster)
```

---

## Common Mistakes

### Mistake 1: Expecting CRF Mode to Preserve Quality

**Symptom**: Exported videos have visible quality degradation despite high source bitrate

**Cause**: CRF mode optimizes for file size, not quality preservation

**Fix**: 
```bash
# Before (quality loss)
ARL_EXPORT_FFMPEG_CRF=18

# After (quality preserved)
ARL_EXPORT_FFMPEG_BITRATE=4000k
ARL_EXPORT_FFMPEG_MAX_BITRATE=5000k
```

**Prevention**: Default to fixed bitrate mode for user-facing exports; use CRF only for archival/storage

### Mistake 2: Forgetting to Disable Highlight Planner

**Symptom**: Exported videos are only 3-4 minutes instead of full 20-30 minute matches

**Cause**: `ARL_HIGHLIGHT_PLANNER_ENABLED` defaults to enabled in older versions, and `use_highlight_plans` defaults to True

**Fix**:
```bash
ARL_HIGHLIGHT_PLANNER_ENABLED=0
ARL_EXPORT_USE_HIGHLIGHT_PLANS=0
```

**Prevention**: Always explicitly set both flags based on user intent

### Mistake 3: Using Fast Presets with NVENC

**Symptom**: Lower quality than expected despite fixed bitrate

**Cause**: NVENC preset affects quality-per-bitrate; p1-p3 sacrifice quality for speed

**Fix**:
```bash
# Before
ARL_EXPORT_FFMPEG_PRESET=p1  # Fast but low quality

# After
ARL_EXPORT_FFMPEG_PRESET=p7  # Highest quality
```

**Prevention**: Always use `p7` with NVENC; the speed gain from p1 is minimal compared to the quality loss

---

## Design Decisions

### Decision: Prefer Fixed Bitrate Over CRF for Quality Preservation

**Context**: Users reported exported videos had "极差" (extremely poor) quality compared to source recordings. Investigation showed CRF mode producing 2.2 Mbps from 3.5 Mbps sources.

**Options Considered**:
1. Tune CRF value lower (e.g., CRF=10)
2. Switch to fixed bitrate mode
3. Use two-pass encoding

**Decision**: We chose fixed bitrate mode (#2) because:
- **Predictable**: Output bitrate guaranteed to match or exceed configured value
- **Simpler**: One-pass encoding, no need for two-pass overhead  
- **NVENC-compatible**: Works identically with hardware encoding
- **User-friendly**: "4000k" is easier to understand than "CRF 10"

**Tradeoff**: Larger file sizes than CRF (typically 20-30% larger), but quality preservation was the primary requirement

**Implementation**:
```python
def _video_quality_args(self) -> list[str]:
    if self.settings.export.ffmpeg_bitrate:
        # Prefer fixed bitrate when configured
        return ["-b:v", self.settings.export.ffmpeg_bitrate, ...]
    else:
        # Fallback to CRF for backward compatibility
        return ["-crf", str(self.settings.export.ffmpeg_crf)]
```

**Extensibility**: To add two-pass encoding in the future, create a third mode controlled by `ARL_EXPORT_USE_TWO_PASS=1`

### Decision: Separate `use_highlight_plans` from `highlight_planner_enabled`

**Context**: Highlight planner may run for analysis/diagnostics even when user wants full match exports

**Decision**: Two independent flags:
- `ARL_HIGHLIGHT_PLANNER_ENABLED`: Whether to run highlight detection (Stage)
- `ARL_EXPORT_USE_HIGHLIGHT_PLANS`: Whether exporter reads and applies plans

**Why**: Allows highlight analysis without forcing condensed exports

---

## Environment Variable Reference

| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `ARL_EXPORT_FFMPEG_BITRATE` | string | None | Fixed average bitrate (e.g., "4000k") |
| `ARL_EXPORT_FFMPEG_MAX_BITRATE` | string | None | Maximum burst bitrate (e.g., "5000k") |
| `ARL_EXPORT_FFMPEG_CRF` | int | 18 | CRF value when bitrate not set |
| `ARL_EXPORT_FFMPEG_PRESET` | string | "slow" | CPU preset or NVENC p1-p7 |
| `ARL_EXPORT_USE_HARDWARE_ENCODING` | bool | False | Use NVENC if available |
| `ARL_EXPORT_USE_HIGHLIGHT_PLANS` | bool | False | Apply highlight condensing |
| `ARL_HIGHLIGHT_PLANNER_ENABLED` | bool | False | Run highlight detection stage |

---

## Related Documentation

- [Orchestration Contracts](./orchestration-contracts.md) - Cross-module contracts including exporter state
- [Quality Guidelines](./quality-guidelines.md) - General code quality standards
- [Logging Guidelines](./logging-guidelines.md) - Logging exporter operations

---

**Last Updated**: 2026-06-15 (Task: fix-export-quality-preservation)
