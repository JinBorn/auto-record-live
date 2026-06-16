# Implementation Plan: Intelligent Highlight Editing (Condensed Mode)

## Implementation Checklist

### Phase 1: Configuration & Data Structures (基础层)

- [ ] **1.1 扩展配置模型** `src/arl/config.py`
  - [ ] 在 `HighlightSettings` 中添加 `mode` 字段
  - [ ] 添加所有 `condensed_*` 配置参数（见design.md）
  - [ ] 添加 `custom_tactical_keywords` 字段
  - [ ] 添加配置验证器（mode值域检查）
  - [ ] 验证：`pytest tests/test_config.py -k highlight`

- [ ] **1.2 定义内部数据结构** `src/arl/highlights/models.py`
  - [ ] 添加 `ClassifiedCue` dataclass
  - [ ] 添加 `ContentDensityResult` dataclass
  - [ ] 添加 `WindowDraft` dataclass（用于优化过程）
  - [ ] 验证：`pytest tests/highlights/test_models.py`（新建测试文件）

- [ ] **1.3 扩展关键词列表** `src/arl/highlights/service.py`
  - [ ] 保留现有 `_HIGHLIGHT_KEYWORDS`
  - [ ] 新增 `_TACTICAL_KEYWORDS` 常量
  - [ ] 添加关键词加载逻辑（合并custom_tactical_keywords）
  - [ ] 验证：手动检查关键词列表完整性

### Phase 2: Cue Classification Module (字幕分类)

- [ ] **2.1 实现字幕分类器** `src/arl/highlights/cue_classifier.py` (新建)
  - [ ] 实现 `classify_cues()` 函数
  - [ ] 实现 `_has_highlight_keyword()` (复用现有逻辑)
  - [ ] 实现 `_has_tactical_keyword()` (新增)
  - [ ] 实现 `_is_low_value()` 判断逻辑：
    - [ ] 长度检查
    - [ ] 重复率检查（Levenshtein距离）
    - [ ] 语气词占比检查
    - [ ] 闲聊话题检查
  - [ ] 实现 `_normalize_text()` 中英混合处理
  - [ ] 验证：`pytest tests/highlights/test_cue_classifier.py`（新建）
    - [ ] 测试关键词匹配
    - [ ] 测试战术术语识别
    - [ ] 测试低价值过滤
    - [ ] 测试中英混合文本

### Phase 3: Visual Analysis Module (视觉分析)

- [ ] **3.1 实现视觉分析器** `src/arl/highlights/visual_analyzer.py` (新建)
  - [ ] 实现 `analyze_visual_activity()` 主函数
  - [ ] 实现 `_sample_frames_for_condensed()` (10秒间隔采样)
  - [ ] 实现 `_compute_scene_change_rate()` (帧间差异)
  - [ ] 实现 `_compute_minimap_activity()` (右下角区域)
  - [ ] 实现 `_compute_edge_density_variance()` (边缘密度)
  - [ ] 实现 `_extract_region()` 区域裁切工具函数
  - [ ] 错误处理：视频打开失败、帧读取失败
  - [ ] 验证：`pytest tests/highlights/test_visual_analyzer.py`（新建）
    - [ ] 测试帧采样
    - [ ] 测试各项指标计算
    - [ ] 测试异常处理

- [ ] **3.2 集成到vision模块** (可选：复用已有采样)
  - [ ] 检查 `src/arl/vision/frame_sampler.py` 是否可复用
  - [ ] 如果可复用，调整采样间隔参数
  - [ ] 如果不可复用，保持独立实现

### Phase 4: Content Density Analyzer (内容密度分析)

- [ ] **4.1 实现内容分析器** `src/arl/highlights/content_analyzer.py` (新建)
  - [ ] 实现 `analyze_content_density()` 主函数
  - [ ] 实现 `_compute_highlight_event_density()` 
    - [ ] 统计key_event类别cue数量
    - [ ] 计算频率（events per minute）
  - [ ] 实现 `_compute_narration_density()`
    - [ ] 统计有效字幕总时长
    - [ ] 计算覆盖率（subtitle_duration / match_duration）
  - [ ] 实现 `_compute_content_density_score()` 加权计算
  - [ ] 实现 `_map_target_duration()` 映射逻辑
    - [ ] score > 0.8 → 20-25分钟
    - [ ] 0.5 < score ≤ 0.8 → 12-18分钟
    - [ ] score ≤ 0.5 → 6-10分钟
  - [ ] 集成 `VisualAnalyzer` 调用（可选开关）
  - [ ] 验证：`pytest tests/highlights/test_content_analyzer.py`（新建）
    - [ ] 测试密度计算
    - [ ] 测试权重加权
    - [ ] 测试目标时长映射
    - [ ] 测试边界情况（无字幕、无视觉）

### Phase 5: Window Optimizer (窗口优化器)

- [ ] **5.1 实现窗口优化器** `src/arl/highlights/window_optimizer.py` (新建)
  - [ ] 实现 `optimize_windows()` 主函数
  - [ ] **Phase 1: 初始窗口生成**
    - [ ] 实现 `_generate_initial_windows()`
    - [ ] 为key_event cue生成窗口（cue时间 ± context_padding）
    - [ ] 为tactical cue生成窗口
    - [ ] 为narration cue生成窗口（可选，根据密度）
  - [ ] **Phase 3: 窗口优化和剪裁**
    - [ ] 实现 `_merge_windows()` (gap < merge_gap_seconds)
    - [ ] 实现 `_remove_short_windows()` (duration < min_window_duration)
    - [ ] 实现 `_add_context_padding()` (关键事件前后padding)
    - [ ] 实现 `_remove_boring_gaps()` (gap > boring_gap_threshold)
  - [ ] **Phase 4: 时长控制**
    - [ ] 实现 `_adjust_to_target_duration()`
    - [ ] 按优先级排序窗口
    - [ ] 超出上限时：从低优先级删除
    - [ ] 低于下限时：放宽boring_gap_threshold恢复内容
    - [ ] clamp到 [6, 25] 分钟
  - [ ] **Phase 5: 质量检查**
    - [ ] 实现 `_validate_key_events_preserved()` 
    - [ ] 实现 `_validate_smooth_transitions()`
  - [ ] 验证：`pytest tests/highlights/test_window_optimizer.py`（新建）
    - [ ] 测试窗口生成
    - [ ] 测试合并逻辑
    - [ ] 测试时长控制
    - [ ] 测试优先级排序

### Phase 6: Service Integration (服务集成)

- [ ] **6.1 重构HighlightPlannerService** `src/arl/highlights/service.py`
  - [ ] 提取现有逻辑到 `_build_highlight_plan()` 方法
  - [ ] 实现 `_build_condensed_plan()` 方法
  - [ ] 实现模式路由逻辑：
    ```python
    if self.settings.mode == "highlight":
        return self._build_highlight_plan(...)
    elif self.settings.mode == "condensed":
        return self._build_condensed_plan(...)
    else:  # "disabled"
        return None
    ```
  - [ ] 在 `_build_condensed_plan()` 中编排调用：
    - [ ] 1. 解析srt → cues
    - [ ] 2. CueClassifier.classify_cues()
    - [ ] 3. ContentAnalyzer.analyze_content_density()
    - [ ] 4. WindowOptimizer.optimize_windows()
    - [ ] 5. 构造HighlightPlanAsset
  - [ ] 保持state管理、JSONL写入逻辑不变
  - [ ] 添加日志输出（mode、score、target_duration、windows数量）

- [ ] **6.2 更新reason字段值** 
  - [ ] condensed模式窗口使用新reason：
    - [ ] "condensed_key_event"
    - [ ] "condensed_tactical"
    - [ ] "condensed_context"
  - [ ] 确保ExporterService兼容新reason值（无需修改，reason仅用于诊断）

### Phase 7: Testing & Validation (测试验证)

- [ ] **7.1 单元测试覆盖**
  - [ ] 所有新增模块达到80%+覆盖率
  - [ ] 边界情况测试（空字幕、极端时长、无视觉）
  - [ ] 运行：`pytest tests/highlights/ -v --cov=src/arl/highlights`

- [ ] **7.2 集成测试**
  - [ ] 编写端到端测试 `tests/pipeline/test_condensed_highlight.py`
  - [ ] 模拟完整流程：boundary + subtitle → condensed plan
  - [ ] 验证plan结构、windows合理性、时长范围
  - [ ] 测试模式切换（highlight ↔ condensed）

- [ ] **7.3 回归测试**
  - [ ] 运行全量测试套件：`pytest tests/`
  - [ ] 确保highlight模式行为不变
  - [ ] 确保exporter、segmenter等下游服务不受影响
  - [ ] 运行：`pytest tests/pipeline/test_cli_unattended.py`

- [ ] **7.4 性能验证**
  - [ ] 准备30分钟测试视频
  - [ ] 测量condensed模式总耗时
  - [ ] 测量视觉分析单独耗时
  - [ ] 验证：视觉分析开销 < 30%，总耗时 < 90秒

### Phase 8: Real-world Validation (真实数据验证)

**前置条件：用户提供真实录像**

- [ ] **8.1 准备测试数据**
  - [ ] 高密度对局录像（频繁团战）
  - [ ] 中密度对局录像（正常节奏）
  - [ ] 低密度对局录像（发育为主）
  - [ ] 中英混合字幕录像
  - [ ] 极端时长录像（<15分钟 / >50分钟）

- [ ] **8.2 生成condensed plans**
  - [ ] 配置 `mode = "condensed"`
  - [ ] 运行：`arl highlights --session-id <id>`
  - [ ] 检查生成的plan文件

- [ ] **8.3 导出condensed视频**
  - [ ] 配置 `use_highlight_plans = True`
  - [ ] 运行：`arl export --session-id <id>`
  - [ ] 验证导出成功、时长在范围内

- [ ] **8.4 人工质量评估**
  - [ ] 观看导出视频，检查：
    - [ ] 关键事件是否完整保留
    - [ ] 转场是否流畅
    - [ ] 字幕同步是否正常
    - [ ] 是否有突兀跳跃
  - [ ] 对比condensed版本与完整版本
  - [ ] 记录问题和改进点

### Phase 9: Parameter Tuning (参数调优)

**基于真实数据验证结果**

- [ ] **9.1 权重调优**
  - [ ] 调整内容密度权重（50% / 25% / 15% / 10%）
  - [ ] 调整优先级权重（1.0 / 0.7 / 0.4）
  - [ ] 调整视觉分析权重（50% / 30% / 20%）

- [ ] **9.2 阈值调优**
  - [ ] 调整静默期阈值（默认60秒）
  - [ ] 调整boring gap阈值（默认120秒）
  - [ ] 调整采样间隔（默认10秒）
  - [ ] 调整合并gap（默认8秒）

- [ ] **9.3 目标时长映射调优**
  - [ ] 验证score与时长映射是否合理
  - [ ] 调整density阈值（0.8 / 0.5）
  - [ ] 调整各档位时长范围

- [ ] **9.4 关键词列表补充**
  - [ ] 根据实际字幕补充遗漏的战术术语
  - [ ] 优化低价值对话识别规则

### Phase 10: Documentation & Polish (文档完善)

- [ ] **10.1 代码文档**
  - [ ] 为所有新增函数添加docstring
  - [ ] 添加类型注解
  - [ ] 添加关键逻辑的注释

- [ ] **10.2 用户文档**
  - [ ] 更新 README.md（condensed模式介绍）
  - [ ] 更新配置文档（新增配置项说明）
  - [ ] 添加使用示例和最佳实践

- [ ] **10.3 日志优化**
  - [ ] 添加condensed模式运行日志
  - [ ] 输出content_density_score
  - [ ] 输出target_duration vs actual_duration
  - [ ] 输出各phase的处理时间

- [ ] **10.4 错误处理完善**
  - [ ] 添加友好的错误提示
  - [ ] 处理边缘情况的降级逻辑
  - [ ] 添加debug模式输出

## Validation Commands

### 单元测试
```bash
# 配置测试
pytest tests/test_config.py -k highlight -v

# 字幕分类器测试
pytest tests/highlights/test_cue_classifier.py -v

# 视觉分析器测试
pytest tests/highlights/test_visual_analyzer.py -v

# 内容分析器测试
pytest tests/highlights/test_content_analyzer.py -v

# 窗口优化器测试
pytest tests/highlights/test_window_optimizer.py -v

# 全量highlights模块测试
pytest tests/highlights/ -v --cov=src/arl/highlights
```

### 集成测试
```bash
# condensed端到端测试
pytest tests/pipeline/test_condensed_highlight.py -v

# 回归测试
pytest tests/pipeline/test_cli_unattended.py -v
pytest tests/pipeline/test_segmenter_service.py -v
pytest tests/pipeline/test_subtitles_service.py -v
```

### 手动验证
```bash
# 生成condensed plan
python -m arl.cli highlights --session-id <session_id>

# 检查生成的plan
cat data/tmp/highlight-plans.jsonl | jq 'select(.session_id == "<session_id>")'

# 导出condensed视频
python -m arl.cli export --session-id <session_id>

# 查看导出日志
cat data/tmp/exporter-events.jsonl | jq 'select(.session_id == "<session_id>")'
```

### 性能测试
```bash
# 测量condensed模式耗时
time python -m arl.cli highlights --session-id <session_id>

# 对比highlight模式耗时
# 修改配置 mode = "highlight"
time python -m arl.cli highlights --session-id <session_id>
```

## Risky Files & Rollback Points

### Risky Files (需要谨慎修改)
1. **`src/arl/highlights/service.py`**
   - 风险：重构可能破坏现有highlight模式
   - 缓解：先提取到独立方法，确保测试通过后再重构
   - 回滚：git revert到重构前状态

2. **`src/arl/config.py`**
   - 风险：配置字段变更可能导致加载失败
   - 缓解：所有新字段提供默认值，添加validator
   - 回滚：移除新增字段，恢复默认配置

### Rollback Points
- **P1完成后**：配置和数据结构ready，可独立测试
- **P2-P4完成后**：核心模块完成，可进行单元测试验证
- **P6完成后**：集成完成，可进行端到端测试
- **P7完成后**：测试通过，可进行真实数据验证
- **P9完成后**：参数调优完成，可发布

## Dependencies

### Python Packages (已有)
- opencv-python (视觉分析)
- numpy (数值计算)
- pydantic (配置模型)
- pytest (测试框架)

### Optional (如果需要Levenshtein距离)
- python-Levenshtein (字符串相似度计算)
- 或使用内置difflib替代（性能略低但无额外依赖）

## Estimated Effort

- Phase 1-2: 2-3小时（配置、数据结构、字幕分类）
- Phase 3: 3-4小时（视觉分析，需要调试opencv逻辑）
- Phase 4: 2小时（内容密度分析，逻辑相对简单）
- Phase 5: 4-5小时（窗口优化器，逻辑复杂，需要仔细测试）
- Phase 6: 2小时（服务集成）
- Phase 7: 3-4小时（测试编写和调试）
- Phase 8-9: 依赖真实数据，时间不定（用户提供录像后）
- Phase 10: 1-2小时（文档完善）

**总计：17-23小时开发时间（不含真实数据验证和调优）**

## Notes

- 优先完成P1-P7，形成可测试的MVP
- P8-P9需要用户提供真实录像，可能需要多轮迭代
- 各phase内部的实现顺序可以灵活调整，但phase之间有依赖关系
- 建议每完成一个phase就commit一次，便于回滚
