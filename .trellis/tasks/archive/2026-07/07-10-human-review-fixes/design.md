# Design — 成片人工复核修复（8 项）

修复点分散在四个 stage，均为小改动；关键是兼容边界与验收口径。

## 1. SFX 时机与音量（editing + config）

- 根因：kill SFX 对齐 `_kda_event_timestamp` = OCR `current_at`（HUD 更新 +
  OCR 采样滞后 ≈1–2s）。
- 改法：`apply_publish_preset` 中，若 `ARL_EDIT_SFX_TIMING_OFFSET_SECONDS`
  未设置则 `sfx_timing_offset_seconds=-1.5`；若 `ARL_EDIT_SFX_GAIN_DB` 未设置
  则 `sfx_gain_db=-7.0`（沿用 transition_mode/zoom_max_segments 的"env 空才
  覆盖"模式）。全局默认值不动 → 非 publish 与既有单测不受影响。
- `_clamped_sfx_time` 已保证提前量不会越出片段起点。

## 2+3. 字幕显示平滑（subtitles/service.py `_smooth_display_entries`）

- 滞留根因：`display_max_gap_fill_seconds=8`（说完话最多再挂 8s）；
  叠行根因：仅对"正间隙"截断 `target_end`，whisper 原生重叠 + 最短显示扩展
  可 3+ 行同屏。
- 改法（函数内，两步）：
  1. 默认值调整：`display_max_gap_fill_seconds` 8.0→1.5、
     `display_min_duration_seconds` 3.5→2.5（env 原样可覆盖）。
  2. 平滑后统一收口：`smoothed[i].end = min(end, smoothed[i+2].start)`
     （i+2 存在时），保证任意时刻 ≤2 行；收口后 end<=start 的行丢弃。
- SRT 在转写时生成 → 参数变更需全部 7 场重跑 subtitles（GPU whisper medium，
  预计 40–60 分钟，后台跑）。
- 指标影响：字幕活跃率回落（预计 60–70%）；若个别样本 <0.55，将
  `subtitle_active_ratio_min` 降至新水位（如 0.50）并在 spec 记录"体验优先"
  的理由。

## 4. BGM 全局跳过阈值（config）

- `bgm_source_music_majority_threshold` 0.60→0.35：源音乐覆盖 >35% 即整场跳过
  BGM；span 回避逻辑不动。env 可调回。

## 5. 封面单张（copywriter + config）

- 新增 `CopywriterSettings(BaseModel): cover_max_candidates: int = 1`，
  env `ARL_COPY_COVER_MAX_CANDIDATES`；`Settings` 挂 `copywriter` 字段。
- `_cover_frame_candidates` 调 `select_cover_frame_candidates(...,
  max_candidates=settings.copywriter.cover_max_candidates)`；下游按候选数出图,
  自然只渲染 cover-01.jpg。旧多余的 cover-0N.jpg 由重生成覆盖策略处理
  （不主动删除历史文件，报告只引用新候选）。

## 6. zoom 兜底移除（editing + config）

- 新增 `zoom_fallback_enabled: bool = False`（env
  `ARL_EDIT_ZOOM_FALLBACK_ENABLED`）；`_zoom_candidates` 的 priority=2 兜底
  候选生成用它门控。KDA（p0）与聊天刷屏（p1）保留。
- 风险：无 KDA-in-zoomable 且无 chat burst 的场次 zoom=0 →
  `zoom_min_segments` 告警。处理：quality-report 的 zoom 下限告警改为仅当
  "存在 KDA 事件且 zoom=0" 时触发（触发器缺失 ≠ 管线故障），spec 同步。

## 7. teaser 质量门（editing + contracts + quality_report）

- `_semantic_teaser_windows`：snap 后过门槛——满足其一：
  (a) 与任一 `HighlightPlanAsset.kda_events` 跨度重叠；
  (b) `_teaser_signal_score(window, subtitle_cues) > 0`；
  (c) snap 到的窗口 reason == "highlight_keyword"。
  全部被拒 → 返回空，走既有 fallback 链；fallback 也无高置信时已有 main-only
  路径。
- `EditPlanAsset.teaser_omitted_reason: str | None = None`（additive）：
  main-only 时记录 "no_high_confidence_teaser"。
- quality-report：`teaser_segment_count < min` 且 `teaser_omitted_reason` 非空
  → 明细行代替告警（同 budget_exception_reason 模式）。
- cf11bf9e m03 为验收样本：其 llm_teaser 内容杂乱应被 (a)(b)(c) 拒绝。
  若实测未被拒（如信号分意外 >0），再收紧门槛（要求 (a) 必须满足）。

## 8. 标题长度（copywriter）

- LLM prompt：`title_candidates (exactly 3 strings, each <=30 compact chars)`
  → `<=45`；同函数内如有对 title 的硬截断一并放宽到 45。
- 启发式 `_is_weak_title` 的 22/10 阈值不动（它们是"最短可用"判断），只放宽
  上限侧。
- 语义缓存注意：prompt 变更 → 输入指纹变化 → 7 场语义资产全部重新调 LLM
  （成本可接受，DeepSeek flash）。

## 兼容与回滚

- 全部新配置有 env 覆盖；publish 预设覆盖仅在对应 env 为空时生效。
- 契约新增字段 additive 默认 None。
- 回滚点 = 单次 feat commit；行为回滚可用 env（SFX offset/gain、zoom fallback、
  cover 数、BGM 阈值、gap-fill）。

## 验证

全链路重生成顺序（subtitles 参数变了，必须从头）：
subtitles → highlight-planner → copywriter(hints) → edit-planner → exporter
→ copywriter(plain) → quality-report，7 场；quality-report 0 告警 + PRD 逐项
验收 + Jinson 人工复核。
