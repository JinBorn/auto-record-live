# Technical Design: Intelligent Highlight Editing (Condensed Mode)

## Overview

扩展现有的 `HighlightPlannerService` 支持 `condensed` 模式，实现激进剪辑策略，生成6-25分钟的精华版视频用于长视频平台投放。

## Architecture

### Module Boundaries

```
src/arl/highlights/
├── service.py              # HighlightPlannerService 主入口（扩展）
├── models.py               # 数据模型（扩展reason字段）
├── content_analyzer.py     # 新增：内容密度分析器
├── visual_analyzer.py      # 新增：视觉活跃度分析器
├── cue_classifier.py       # 新增：字幕分类器（key_event/tactical/narration/low_value）
└── window_optimizer.py     # 新增：condensed窗口优化器

src/arl/config.py           # 扩展 HighlightSettings 配置
```

### Responsibilities

#### `HighlightPlannerService` (扩展)
- 模式路由：根据 `settings.highlights.mode` 分发到不同的planning逻辑
- highlight模式：保持现有逻辑不变（重构提取到 `_build_highlight_plan`）
- condensed模式：调用新的 `_build_condensed_plan`
- 共享逻辑：cue解析、状态管理、JSONL写入

#### `ContentAnalyzer` (新增)
- 计算 `highlight_event_density`：关键词cue频率
- 计算 `narration_density`：有效字幕覆盖率
- 调用 `VisualAnalyzer` 获取 `visual_activity`（如果启用）
- 加权计算 `content_density_score`
- 根据score映射 `target_duration`

#### `VisualAnalyzer` (新增)
- 采样视频帧（10秒间隔，condensed模式专用）
- 计算场景变化率：相邻帧差异
- 计算小地图活跃度：右下角区域变化率
- 计算边缘密度变化：画面复杂度方差
- 综合输出 `visual_activity` 评分

#### `CueClassifier` (新增)
- 为每个字幕cue打标签：`key_event` / `tactical` / `narration` / `low_value`
- 关键词匹配：复用现有 `_HIGHLIGHT_KEYWORDS`
- 战术术语匹配：新增 `_TACTICAL_KEYWORDS`
- 低价值识别：长度、重复率、语气词占比
- 支持中英混合文本

#### `WindowOptimizer` (新增)
- Phase 1: 生成初始窗口（基于分类后的cues）
- Phase 3: 窗口合并、剔除、padding
- Phase 4: 时长控制（按优先级削减/恢复）
- Phase 5: 质量检查（关键事件完整性）

## Data Flow

```
Input:
  MatchBoundary (session_id, match_index, started_at, ended_at)
  SubtitleAsset (path to .srt file)
  Settings (HighlightSettings with mode="condensed")

Flow:
  1. HighlightPlannerService.run()
     ├─ 读取 boundaries 和 subtitles
     ├─ 根据 mode 分发
     └─ condensed路径 → _build_condensed_plan()

  2. _build_condensed_plan()
     ├─ 解析 .srt → list[_SrtCue]
     ├─ CueClassifier.classify_cues() → list[ClassifiedCue]
     ├─ ContentAnalyzer.analyze() → ContentDensityResult
     │   ├─ 计算 highlight_event_density
     │   ├─ 计算 narration_density
     │   ├─ 调用 VisualAnalyzer.analyze() → visual_activity
     │   └─ 加权计算 score → target_duration
     └─ WindowOptimizer.optimize() → list[HighlightClipWindow]
         ├─ Phase 1: 初始窗口生成
         ├─ Phase 3: 合并、剔除、padding
         ├─ Phase 4: 时长控制
         └─ Phase 5: 质量检查

  3. 返回 HighlightPlanAsset
     ├─ windows: list[HighlightClipWindow]
     ├─ reason标记: "condensed_key_event" / "condensed_tactical" / "condensed_context"
     └─ 写入 highlight-plans.jsonl

Output:
  HighlightPlanAsset (与highlight模式共享同一数据结构)
  ExporterService 按照 windows 裁切并拼接视频
```

## Key Data Structures

### ClassifiedCue (新增)
```python
@dataclass
class ClassifiedCue:
    started_at_seconds: float
    ended_at_seconds: float
    text: str
    category: str  # "key_event" | "tactical" | "narration" | "low_value"
    priority: float  # 1.0 / 0.7 / 0.4 / 0.0
```

### ContentDensityResult (新增)
```python
@dataclass
class ContentDensityResult:
    highlight_event_density: float
    narration_density: float
    visual_activity: float
    content_density_score: float
    target_duration_seconds: float
```

### HighlightClipWindow (现有，扩展reason)
```python
class HighlightClipWindow(BaseModel):
    started_at_seconds: float
    ended_at_seconds: float
    reason: str  # 扩展值域：
                 # highlight模式: "highlight_keyword" / "narration" / "match_start_context"
                 # condensed模式: "condensed_key_event" / "condensed_tactical" / "condensed_context"
```

## Configuration Schema

### HighlightSettings 扩展
```python
class HighlightSettings(BaseModel):
    enabled: bool = True
    mode: str = "highlight"  # "highlight" | "condensed" | "disabled"
    
    # === highlight模式参数（现有，保持不变）===
    cue_padding_seconds: float = 6.0
    highlight_padding_seconds: float = 22.0
    merge_gap_seconds: float = 10.0
    keep_edge_seconds: float = 30.0
    min_boundary_duration_seconds: float = 600.0
    min_reduction_seconds: float = 120.0
    min_retained_seconds: float = 480.0
    min_retained_fraction: float = 0.55
    max_windows: int = 8
    
    # === condensed模式参数（新增）===
    # 内容密度权重
    condensed_weight_highlight_events: float = 0.5
    condensed_weight_narration: float = 0.25
    condensed_weight_visual: float = 0.15
    condensed_weight_baseline: float = 0.1
    
    # 目标时长映射
    condensed_target_duration_range: tuple[int, int] = (6, 25)  # 分钟
    condensed_high_density_threshold: float = 0.8
    condensed_low_density_threshold: float = 0.5
    condensed_high_density_duration_range: tuple[int, int] = (20, 25)
    condensed_mid_density_duration_range: tuple[int, int] = (12, 18)
    condensed_low_density_duration_range: tuple[int, int] = (6, 10)
    
    # 窗口生成参数
    condensed_context_padding_seconds: float = 5.0
    condensed_merge_gap_seconds: float = 8.0
    condensed_min_window_duration_seconds: float = 3.0
    condensed_silent_gap_threshold_seconds: float = 60.0
    condensed_boring_gap_threshold_seconds: float = 120.0
    
    # 优先级权重
    condensed_priority_key_event: float = 1.0
    condensed_priority_tactical: float = 0.7
    condensed_priority_narration: float = 0.4
    
    # 低价值对话过滤
    condensed_low_value_min_length: int = 3
    condensed_low_value_similarity_threshold: float = 0.8
    condensed_low_value_repeat_window_seconds: float = 30.0
    
    # 视觉分析
    condensed_use_visual_analysis: bool = True
    condensed_visual_sample_interval_seconds: float = 10.0
    condensed_visual_weight_scene_change: float = 0.5
    condensed_visual_weight_minimap: float = 0.3
    condensed_visual_weight_edge_density: float = 0.2
    
    # 用户自定义术语
    custom_tactical_keywords: list[str] = []
```

## Compatibility & Migration

### Backward Compatibility
- 默认 `mode = "highlight"`，未升级配置的用户保持原有行为
- `ExporterService` 无需修改，继续读取 `HighlightPlanAsset.windows`
- `HighlightPlanAsset` 数据结构不变，仅扩展 `reason` 字段值域

### State File Compatibility
- `HighlightPlannerStateFile` 无需修改
- `processed_match_keys` 对 highlight 和 condensed 模式通用
- 如果用户切换模式，会触发 replanning（检测到 plan 与 boundary 不匹配）

### Config Migration
- 新增字段均有默认值，旧配置可直接加载
- 用户需显式设置 `mode = "condensed"` 才启用新功能

## Performance Considerations

### Visual Analysis Overhead
- **采样成本**：10秒间隔，30分钟视频约180帧
- **解码成本**：opencv `VideoCapture` 随机seek + read，约0.3秒/帧
- **分析成本**：帧间差异、区域裁切、边缘检测，约0.2秒/帧
- **总计**：180帧 × 0.5秒 = 90秒（符合性能要求 < 30%增量）

### Optimization Strategies
1. **缓存采样帧**：condensed模式的10秒采样可复用对局检测的20秒采样（每2帧取1）
2. **并行处理**：视觉分析可与字幕解析并行
3. **延迟计算**：仅在 `mode = "condensed"` 时才调用 `VisualAnalyzer`

### Memory Footprint
- 180帧 × 1080p × 3通道 × 4字节 ≈ 1.4 GB（未压缩）
- 实际存储为numpy数组，约200 MB
- 处理完后立即释放，不持久化

## Error Handling

### Visual Analysis Failures
- 视频无法打开 → 降级为仅字幕分析，记录warning
- 采样帧异常 → 跳过该帧，继续处理剩余帧
- visual_activity计算失败 → 设为0.0，不影响其他维度

### Subtitle Quality Issues
- 无字幕文件 → 仅依赖视觉分析，如果视觉也失败则跳过condensed plan生成
- 字幕为空或全是placeholder → 降低narration_density权重，提升visual权重

### Edge Cases
- 对局时长 < 6分钟 → 不生成condensed plan（无剪辑价值）
- 所有cue都是low_value → 仅保留对局边缘（开始/结束各30秒）
- target_duration计算后超出range → clamp到 [6, 25] 分钟

## Testing Strategy

### Unit Tests
- `CueClassifier`: 关键词匹配、战术术语识别、低价值过滤
- `ContentAnalyzer`: 密度计算、权重加权、目标时长映射
- `VisualAnalyzer`: 场景变化率、小地图活跃度、边缘密度
- `WindowOptimizer`: 窗口合并、时长控制、优先级排序

### Integration Tests
- 端到端：模拟boundary + subtitle → condensed plan → 验证windows合理性
- 模式切换：highlight ↔ condensed 切换后状态正确

### Regression Tests
- highlight模式行为不变
- 完整对局导出不受影响
- 配置向后兼容

## Rollout Plan

### Phase 1: Core Implementation
- 实现 `CueClassifier`, `ContentAnalyzer`, `WindowOptimizer`
- 扩展 `HighlightPlannerService` 支持模式路由
- 单元测试覆盖

### Phase 2: Visual Analysis
- 实现 `VisualAnalyzer`
- 集成到 `ContentAnalyzer`
- 性能验证

### Phase 3: Integration & Testing
- 端到端测试
- 真实数据验证（用户提供录像）
- 参数调优

### Phase 4: Polish & Documentation
- 错误处理完善
- 日志输出优化
- 用户文档更新
