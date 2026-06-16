# 智能剪辑：生成长视频平台精华版本

## Goal

从完整对局视频中剔除无聊、无高光、无有效内容的片段，生成适合B站、YouTube等长视频平台投放的精华版本（10-25分钟）。

## User Value

- 完整对局视频时长过长（通常30-50分钟），直接上传不符合长视频平台的最佳观看时长
- 通过智能剪辑可以提升内容密度，保留精彩部分，提高观看完成率
- 为后续短视频平台投放打下基础

## Context

当前系统已有的能力：
- 视觉检测系统：场景分类（in_game/loading/other）、计时器OCR、对局边界检测
- 字幕生成：Whisper ASR生成字幕文件（.srt）
- 高光检测：基于关键词（kill/dragon/baron等）和字幕时间戳检测高光片段
- 高光导出：通过`HighlightPlanAsset`保存高光窗口，Exporter可以按照windows列表裁切并拼接视频

当前`HighlightPlannerService`行为：
- 输入：完整对局boundary + 字幕cues
- 输出：一组时间窗口（windows），保留有价值片段
- 窗口合并：gap < 10秒的窗口会合并
- 质量门槛：
  - 必须有足够的削减量（min_reduction_seconds=120秒）
  - 必须保留足够内容（min_retained_seconds=480秒 或 min_retained_fraction=0.55）
  - 如果只有1个窗口且几乎覆盖全程，则不生成plan（认为没有剪辑价值）

## Requirements

### 核心功能
- [ ] 支持从完整对局生成精华版导出
- [ ] 剔除无聊片段（无对话、无游戏事件、低活动度场景）
- [ ] 保留精彩片段（击杀、团战、大龙、推塔等关键事件）
- [ ] 保留必要上下文（对局开局/结局、关键事件前后的过渡）
- [ ] 控制精华版时长在10-25分钟范围内

### 质量保证
- [ ] 剪辑后的视频流畅连贯，无突兀跳跃
- [ ] 保留关键事件的完整性（不在团战中途切断）
- [ ] 字幕与画面同步

### 技术约束
- [ ] 复用现有的`HighlightPlannerService`和窗口机制
- [ ] 与现有导出流程集成
- [ ] 不影响现有的完整对局导出功能

## Acceptance Criteria

### 功能正确性
- [ ] **配置生效**：
  - [ ] `HighlightSettings.mode = "condensed"` 时生成condensed plan
  - [ ] `mode = "highlight"` 时保持原有行为不变
  - [ ] `mode = "disabled"` 时不生成任何plan
  
- [ ] **时长控制**：
  - [ ] 对于30分钟完整对局，condensed版本时长在6-25分钟范围内
  - [ ] 高密度对局（score > 0.8）生成20-25分钟精华版
  - [ ] 中密度对局（0.5 < score ≤ 0.8）生成12-18分钟精华版
  - [ ] 低密度对局（score ≤ 0.5）生成6-10分钟精华版
  
- [ ] **内容保留**：
  - [ ] 所有关键事件（kill/dragon/baron等）都被保留
  - [ ] 关键事件前有3-5秒上下文铺垫
  - [ ] 战术沟通对话被正确识别和保留
  - [ ] 无聊gap（>120秒静默期）被完全剔除
  - [ ] 低价值对话被正确过滤
  
- [ ] **导出质量**：
  - [ ] 生成的视频流畅无卡顿
  - [ ] 字幕与画面同步（误差<0.5秒）
  - [ ] 窗口转场自然，无突兀跳跃
  - [ ] 音频连续性正常

### 性能要求
- [ ] 视觉分析增量开销 < 原有处理时间的30%
- [ ] 10秒采样间隔下，30分钟视频的分析时间 < 90秒
- [ ] 不启用condensed时无额外性能开销

### 回归测试
- [ ] 现有highlight模式功能不受影响
- [ ] 现有完整对局导出功能不受影响
- [ ] 现有配置向后兼容（未设置mode时默认highlight行为）

### 边界情况测试
- [ ] **不同语言字幕**：
  - [ ] 纯中文字幕对局
  - [ ] 纯英文字幕对局
  - [ ] 中英混合字幕对局
  
- [ ] **极端时长对局**：
  - [ ] 短对局（<15分钟）condensed后时长合理（不过度剪辑）
  - [ ] 长对局（>50分钟）condensed后时长在范围内
  
- [ ] **极端内容密度**：
  - [ ] 全程激战对局（几乎无发育期）保留合理比例
  - [ ] 极低密度对局（几乎无事件）仍能生成最小时长精华版
  
- [ ] **无字幕/字幕质量差**：
  - [ ] 无字幕时仅依赖视觉分析生成plan
  - [ ] 字幕识别错误率高时能降级处理

### 测试数据需求
**由用户后续提供真实录像**，覆盖：
- 高密度对局：频繁团战、击杀的激烈对局
- 中密度对局：正常节奏的对局
- 低密度对局：发育为主、较少战斗的对局
- 中英混合字幕对局
- 极端时长对局（<15分钟 / >50分钟）

### 测试方法
1. **自动化测试**：
   - condensed plan生成逻辑单元测试
   - 窗口合并、时长计算算法测试
   - 内容密度评分计算测试
   - 视觉分析模块测试
   
2. **集成测试**：
   - 端到端流程：录像 → 字幕 → condensed plan → 导出
   - 不同配置组合测试
   
3. **手动验证**：
   - 实际导出3-5个condensed视频
   - 人工观看评估质量（内容连贯性、关键事件完整性）
   - 对比condensed版本与完整版本

## Confirmed Decisions

### 剪辑强度和目标时长策略
- 目标时长范围：**6-25分钟**（根据对局内容动态调整）
- 采用**模式化配置 + 动态策略**结合的方案：
  - 新增`export_mode`配置：`full` / `highlight` / `condensed`
  - `condensed`模式下，根据对局时长和高光内容密度动态计算目标时长
  - 精彩对局（高光密集）→ 保留更多内容，接近25分钟
  - 平淡对局（高光稀疏）→ 激进剪辑，可能只保留6-10分钟
- 动态策略需要评估"内容密度"指标

### 内容密度评估指标
- 采用**多维度加权评分**方案：
  ```
  content_density_score = (
      0.5 * highlight_event_density +  # 高光事件频率
      0.25 * narration_density +       # 有效字幕密度
      0.15 * visual_activity +         # 视觉活跃度
      0.1 * baseline_activity          # 基础活跃度
  )
  ```
- 初始权重比例：50% / 25% / 15% / 10%（后续可根据实际效果调整）
- **视觉分析实现**（MVP阶段）：
  - **采样策略**：condensed模式使用10秒间隔采样（比对局检测的20秒更密集）
  - **实现3个低成本指标**：
    1. **场景变化率**（scene_change_rate）：相邻帧差异，反映画面动态程度
    2. **小地图活跃度**（minimap_activity）：右下角小地图区域变化，反映全局战斗分布
    3. **边缘密度变化**（edge_density_variance）：画面复杂度变化（多英雄聚集 vs 单人发育）
  - **视觉活跃度综合计算**：
    ```
    visual_activity = (
        0.5 * scene_change_rate +
        0.3 * minimap_activity +
        0.2 * edge_density_variance
    )
    ```
  - **触发条件**：仅在condensed模式时开启视觉分析（highlight/full模式不使用）
  - **性能优化**：复用scene_classifier的区域裁切逻辑，避免重复解码
- 目标时长映射：
  - score > 0.8 → 20-25分钟
  - 0.5 < score ≤ 0.8 → 12-18分钟
  - score ≤ 0.5 → 6-10分钟

### 无聊片段识别标准
**应该剔除的片段：**
1. **静默发育期**：
   - 连续N秒以上无字幕活动（初始阈值：60秒，可配置）
   - 无关键词事件
   - （如果有视觉分析）场景变化率极低
2. **重复性内容**：
   - 相似的补兵、清野场景
   - 连续的回城/复活等待
3. **低价值对话**：
   - 闲聊、重复抱怨等非战术沟通
   - 识别方式：简单规则匹配（优先），暂不引入复杂NLP

**必须保留的片段：**
1. **关键事件**：击杀、团战、大龙、推塔、胜败（复用现有关键词）
2. **上下文过渡**：关键事件前3-5秒的铺垫
3. **战术沟通**：包含位置、技能、装备等游戏术语的对话

**实现策略：**
- 在现有window基础上，增加"无聊片段过滤"规则
- 对于narration类型的window，评估密度，低密度窗口降级或剔除
- Gap处理：相邻window之间的gap超过阈值（初始值：120秒）时，完全剔除该gap
- 可配置的阈值参数：
  - `silent_gap_threshold_seconds`: 静默期阈值（默认60秒）
  - `boring_gap_threshold_seconds`: 无聊gap阈值（默认120秒）
  - `tactical_keywords`: 战术术语列表（扩展现有关键词）
- **细粒度分类**（探索性）：
  - 区分"经济期"（补兵、清野）和"战术准备期"（视野、集结）
  - 战术准备期可能包含重要的团队沟通，优先级高于纯经济期

### 战术术语和低价值对话识别

**战术术语扩展**（初始列表，支持用户自定义追加）：

```python
_TACTICAL_KEYWORDS = (
    # 召唤师技能（中英）
    "flash", "tp", "teleport", "ignite", "heal", "cleanse", "exhaust", "barrier", "ghost",
    "闪现", "传送", "TP", "点燃", "治疗", "净化", "虚弱", "屏障", "疾跑",
    
    # 位置和移动
    "top", "mid", "bot", "jungle", "river", "lane", "bush", "上路", "中路", "下路", 
    "打野", "野区", "河道", "线", "草丛", "三角草",
    "gank", "push", "retreat", "roam", "回防", "抓", "推", "撤", "守", "游走", "支援",
    
    # 装备和经济
    "build", "item", "gold", "buy", "装备", "出装", "经济", "补刀", "买", "合成",
    "神话", "传说", "破败", "无尽", "火炮", "羊刀", "金身", "中亚",
    
    # 视野和战术
    "ward", "vision", "pink", "control ward", "眼", "视野", "真眼", "控制守卫", 
    "蹲", "埋伏", "反蹲", "排眼",
    
    # 技能和CD
    "ult", "ultimate", "cd", "cooldown", "skill", "大招", "技能", "CD", "冷却",
    "没大", "有大", "大招好了",
    
    # 团队协作
    "group", "split", "engage", "disengage", "peel", "focus", "开团", "集合", 
    "分推", "带线", "保护", "切", "秒", "打团",
    
    # 资源和目标
    "buff", "红", "蓝", "石头人", "三狼", "F6", "河蟹", "先锋", "远古龙",
    
    # 游戏状态
    "level", "经验", "等级", "复活", "泉水", "兵线", "炮车", "超级兵",
)
```

**低价值对话识别规则**：

为每个字幕cue打标签：`key_event` / `tactical` / `narration` / `low_value`

判定为`low_value`的条件（满足任一即可）：
1. **无关键内容**：不含任何关键词或战术术语
2. **长度过短**：去除标点后字符数 < 3
3. **重复率高**：与前后10秒内的字幕相似度 > 80%（Levenshtein距离）
4. **纯语气词**：90%以上为语气词（"啊"、"哦"、"嗯"、"卧槽"等）
5. **无意义重复**：同一短语在30秒内出现3次以上（如"又死了"连续出现）
6. **闲聊模式**：包含明显的日常话题词（"吃饭"、"睡觉"、"明天"、"昨天"等）

**配置支持**：
```python
class HighlightSettings:
    # ...
    custom_tactical_keywords: list[str] = []  # 用户自定义战术术语
    low_value_min_length: int = 3
    low_value_similarity_threshold: float = 0.8
    low_value_repeat_window_seconds: float = 30.0
```

**中英混合处理**：
- 正常识别，关键词匹配时忽略大小写
- 对中英混合文本做归一化：统一转小写，保留中文原样
- 示例："我有Flash可以开团" → 匹配到"flash"和"开团"，标记为`tactical`

### 架构设计：扩展现有highlight系统
**方案：扩展`HighlightPlannerService`支持多模式**

1. **在`HighlightPlannerService`中增加模式支持**：
   - 当前逻辑重命名为`highlight`模式（保守剪辑，保留55%+）
   - 新增`condensed`模式的planning逻辑（激进剪辑，6-25分钟）
   - 共享底层工具函数：cue解析、window合并、duration计算等

2. **配置层面**（扩展`HighlightSettings`）：
   ```python
   class HighlightSettings:
       enabled: bool = True
       mode: str = "highlight"  # "highlight" | "condensed" | "disabled"
       
       # highlight模式参数（现有）
       cue_padding_seconds: float = 6.0
       highlight_padding_seconds: float = 22.0
       merge_gap_seconds: float = 10.0
       keep_edge_seconds: float = 30.0
       min_retained_fraction: float = 0.55
       # ...
       
       # condensed模式参数（新增）
       condensed_target_duration_range: tuple[int, int] = (6, 25)
       condensed_silent_gap_threshold_seconds: float = 60.0
       condensed_boring_gap_threshold_seconds: float = 120.0
       condensed_context_padding_seconds: float = 5.0
       condensed_min_window_duration_seconds: float = 3.0
       condensed_merge_gap_seconds: float = 8.0
   ```

3. **导出配置**（重命名以适配通用性）：
   ```python
   class ExportSettings:
       use_highlight_plans: bool = False  # 保持兼容性，控制是否使用plan裁切
   ```

4. **Plan数据结构保持不变**：
   - 继续使用`HighlightPlanAsset`
   - `windows`字段保存不同模式生成的窗口列表
   - `reason`字段区分窗口类型：
     - highlight模式："highlight_keyword" / "narration" / "match_start_context"
     - condensed模式："condensed_key_event" / "condensed_context" / "condensed_tactical"

**优点：**
- 复用现有基础设施（状态管理、JSONL存储、导出流程）
- 统一的plan格式，便于后续扩展（如ultra-short短视频模式）
- 减少代码重复，降低维护成本

### Condensed模式算法流程

```
Phase 1: 初始窗口生成
├─ 扫描所有字幕cue
├─ 识别关键事件（含关键词的cue）
├─ 识别战术沟通（含战术术语的cue）
└─ 为每个重要cue生成初始window（cue时间 ± context_padding）

Phase 2: 内容密度评估
├─ 计算highlight_event_density（关键词频率）
├─ 计算narration_density（有效字幕覆盖率）
├─ （可选）计算visual_activity（场景变化率）
├─ 加权计算content_density_score
│   score = 0.6 * highlight_event_density + 0.3 * narration_density + 0.1 * baseline
└─ 根据score确定target_duration：
    ├─ score > 0.8 → 20-25分钟
    ├─ 0.5 < score ≤ 0.8 → 12-18分钟
    └─ score ≤ 0.5 → 6-10分钟

Phase 3: 窗口优化和剪裁
├─ 合并相邻窗口（gap < condensed_merge_gap_seconds = 8秒）
├─ 剔除低价值窗口（duration < min_window_duration = 3秒）
├─ 添加上下文padding（关键事件前后各5秒）
├─ 剔除boring gaps（相邻窗口gap > boring_gap_threshold = 120秒）
└─ 按优先级排序窗口：
    ├─ 关键事件（优先级权重1.0）
    ├─ 战术沟通（优先级权重0.7）
    └─ 普通narration（优先级权重0.4）
    注：权重用于排序优先级（数值越大越重要），非概率分布

Phase 4: 时长控制
├─ 计算当前retained_duration
├─ 如果超出target_duration上限：
│   └─ 从低优先级窗口开始削减（narration → tactical → key_event保护）
├─ 如果低于target_duration下限：
│   └─ 适当放宽boring_gap_threshold，恢复部分gap内容
└─ 确保最终时长在[6, 25]分钟范围内

Phase 5: 质量检查
├─ 确保所有关键事件都被保留
├─ 确保窗口之间转场流畅（避免突兀跳跃）
└─ 生成HighlightPlanAsset（mode标记为condensed）
```

**关键参数总结：**
- `condensed_context_padding_seconds`: 5.0
- `condensed_merge_gap_seconds`: 8.0
- `condensed_min_window_duration_seconds`: 3.0
- `condensed_priority_weights`: {"key_event": 1.0, "tactical": 0.7, "narration": 0.4}

## Open Questions

暂无。所有核心决策已明确。

## Notes

- 优先级权重（1.0 / 0.7 / 0.4）、视觉分析权重（50% / 25% / 15% / 10%）在实现后需根据实际效果调优
- 各类阈值（60秒静默、120秒boring gap、10秒采样间隔）为初始值，后续可基于真实数据调整
- 战术术语列表需要持续补充和完善
- 低价值对话识别规则可能需要迭代优化

## Out of Scope

- 短视频平台增强（音效、特效、表情包）留待后续迭代
- 多对局混剪
- 自动化封面生成
