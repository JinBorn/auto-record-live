# Design — 07-06 批次综合复核与成片时长调优

> **实施修订（2026-07-10）**：与 §2.2 原案的差异——
> 1. 放弃"预算压力下收紧 KDA preroll 到 floor"：quality-report 的 KDA 覆盖
>    语义要求完整 cue 跨度（含 preroll/读数间隔/postroll）被保留窗口覆盖，
>    收紧 preroll 会直接制造 uncovered 告警。KDA cue 全跨度为硬保护。
> 2. continuity 窗口并非只有 3s 桥（早期管线的无上限语音保护会把桥撑到
>    80–158s），改为**仅裁尾**（头部对齐保住 death-entry 保护），地板=最小窗。
> 3. 新增边界锚点锁定：edit-planner 要求窗口触达边界首尾
>    （`no_valid_main_windows`），收缩不得裁掉锚点端。
> 4. 收缩后语音保护为纯限幅延伸（≤3s、只延不缩），不做边界回退——回退可能
>    切进 KDA 保护区。
> 5. 预算告警按 **main 段时长**判定（teaser 冷开场有独立预算，不占本预算）；
>    teaser 计数按源时间相邻合并（zoom 特写切分）。


## 1. 时长膨胀根因（代码 + 数据定位）

`HighlightService` condensed 管线（`src/arl/highlights/service.py` ~L2060-2195）
顺序为：

```
optimize_windows(target)                     # 按目标时长选窗
→ _trim_silent_kda_death_waits / _extend_action_resolution_windows
→ _protect_speech_boundaries                 # 语音边界外扩
→ bridge_highlight_windows                   # ≤45s gap 填 continuity
→ _enforce_condensed_duration_budget         # ★预算裁剪（超预算→单最优窗+首尾）
→ _restore_missing_kda_event_windows         # ★每个未覆盖 KDA 事件回补窗口
→ （每次变更后重复 speech-protect / bridge / death-continuity）
→ _trim_low_value_internal_gaps
→ 产出 HighlightPlanAsset                    # ★无最终预算复检
```

三个星号构成根因链：预算裁剪在中段执行；其后 KDA 回补（kill preroll 15s /
death preroll 30s / postroll 5s，9–11 个事件）+ 语音边界外扩（字幕活跃率
84–87% 后几乎处处是语音，可扩空间大）+ 45s 桥接，把 12.9 分钟预算重新膨胀到
17.7–23.7 分钟，且没有任何步骤再回头检查预算。

数据佐证（highlight-plans.jsonl，07-10）：

| 样本 | plan | key_event | continuity | 备注 |
|---|---:|---:|---:|---|
| 4b5ec478 m02 | 17.1 min | 833s | 171s | 源边界 22.7 min，保留 75% |
| cf11bf9e m02 | 17.0 min | 826s | 167s | |
| cf11bf9e m03 | 22.1 min | 959s | 365s | continuity 27% >> 10% 预算 |
| cf11bf9e m04 | 22.9 min | 834s | 526s | continuity 38% |
| bc90812b m01 | 16.2 min | 885s | 67s | |
| bc90812b m02/03 | 7.8 / 9.5 min | — | — | 密度低时管线本身可达标 |

## 2. 方案

### 2.1 可观测性先行：quality-report 时长指标

- `HighlightPlanAsset` 增加 additive 字段 `target_duration_seconds: float | None = None`
  （`shared/contracts.py`），planner 写入密度分析结果。旧资产缺字段 → None →
  报告跳过预算判断（向后兼容）。
- `quality_report/service.py` 新增指标：`plan_duration_seconds`、
  `target_duration_seconds`、`duration_budget_seconds`（= max(target×1.25,
  target+60s)，与 planner 常量同源，常量提升到 shared 或由 planner 一并持久化
  `budget_seconds` 避免双写漂移——选后者：资产上直接存 budget）、
  `plan_over_budget` 布尔；超预算发 warning（`--strict` 时置 exit 1）。
- 阈值可关：`ARL_QUALITY_REPORT_DURATION_BUDGET_ENFORCED`（默认开）。

### 2.2 收敛机制：终段预算收缩 `_shrink_windows_to_budget`

在管线终点（`_trim_low_value_internal_gaps` 固定点之后、构造资产之前）新增一个
KDA 感知的预算收缩步骤，替代"中段裁剪后放任回膨胀"的结构：

- 触发条件：窗口总时长 > budget（预算常量不变：×1.25 / +60s）。
- 保护集：每个 KDA 事件的 [start−preroll_floor, end+postroll] 区间；活跃语音 cue
  跨越的切点禁止（裁剪边界吸附到语音间隙）；death-like continuity 入口。
  preroll_floor 为新配置（`condensed_budget_kda_kill_preroll_floor_seconds=8`、
  `condensed_budget_kda_death_preroll_floor_seconds=15`）——仅在预算压力下把
  15s/30s 的 preroll 收紧到 floor，无压力时不变。
- 裁剪顺序（低价值优先，迭代直到达标或无可裁）：
  1. continuity 桥接超出"维持 ≤45s gap 所必需"的部分，并把 continuity 合计
     压回 ≤10%；
  2. 窗口首尾的无 cue / baseline 价值 padding（吸附语音间隙）；
  3. 窗口内部长低价值跨度二次切分（复用 `_trim_low_value_internal_gaps` 的
     价值判定，收紧其 keep 参数）；
  4. KDA preroll/postroll 收紧到 floor；
  5. 整窗淘汰：按窗口价值密度（key_event > tactical > narration > baseline，
     复用 `condensed_priority_*` 权重）从低到高丢弃**不含 KDA 事件**的窗口。
- 硬约束（每轮迭代后校验，违反则回退该轮）：KDA 覆盖不减、相邻源 gap ≤45s
  （必要时保留桥接）、不产生 < `condensed_min_window_duration_seconds` 的碎窗。
- 收缩后跑一次轻量固定点：speech-protect（只允许**吸附收缩**方向，新参数禁止
  再外扩超预算——`_protect_speech_boundaries` 增加可选 `max_extension_seconds`）
  → clamp → 校验；若重新超预算且已到裁剪下限，记录例外原因到 plan asset
  （`budget_exception_reason`，additive），quality-report 对有例外说明的样本降级为
  info 而非 warning。
- 开关：`ARL_HIGHLIGHTS_CONDENSED_BUDGET_SHRINK_ENABLED`（默认开；关闭即回到
  07-10 行为，作为回滚开关）。

### 2.3 批次复核修复项

- `teaser_impact` 消费：`editing/service.py` teaser 首段起点插入一次
  `teaser_impact` SFX（增益走 library `gain_db`），素材/清单缺失静默跳过；
  README 措辞已正确无需改。不影响非 teaser 计划。
- `multi_kill` 回退 `kill_coin`：现状符合 sfx 子任务 PRD，仅补一条日志断言验证。
- 未知 library 类别（mistake/boom/pew/transition_bruh）：确认 loader 容忍未消费
  类别（只按需查询，天然容忍），不改代码，记录到报告。

## 3. 兼容与回滚

- 契约字段全部 additive 且默认 None/[]，旧 plan JSON 反序列化不受影响。
- 非 condensed 模式：新步骤只挂在 condensed 分支，highlight/disabled 模式零改动。
- 回滚：关 `ARL_HIGHLIGHTS_CONDENSED_BUDGET_SHRINK_ENABLED` +
  `ARL_QUALITY_REPORT_DURATION_BUDGET_ENFORCED` 即回到 07-10 行为；代码回滚点
  为单次 feat commit。

## 4. 风险

- 收缩后 BGM/SFX/zoom 指令基于新时间线重算，需整链重生成（highlight-planner
  起，subtitles 不动）——已列入 implement 验证流程。
- 语音密集样本可能触底例外路径：验收允许 ≤1 个例外且需 ≥25% 降幅。
- 导出阶段 ffmpeg 长任务照 07-09 经验以计划任务/后台方式跑，避免会话中断。
