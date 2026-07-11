# Implement — 成片人工复核修复（8 项）

## 1. 配置与预设（config.py）

- [ ] `display_max_gap_fill_seconds` 8.0→1.5、`display_min_duration_seconds`
      3.5→2.5（默认值 + env 默认参数同步）。
- [ ] `bgm_source_music_majority_threshold` 0.60→0.35（同上）。
- [ ] `zoom_fallback_enabled: bool = False` + env `ARL_EDIT_ZOOM_FALLBACK_ENABLED`。
- [ ] `CopywriterSettings.cover_max_candidates=1` + env
      `ARL_COPY_COVER_MAX_CANDIDATES` + `Settings.copywriter`。
- [ ] `apply_publish_preset`：env 空时 `sfx_timing_offset_seconds=-1.5`、
      `sfx_gain_db=-7.0`。
- [ ] 单测：预设覆盖与 env 优先级。

## 2. 字幕平滑（subtitles/service.py）

- [ ] `_smooth_display_entries` 收口 ≤2 并发行；丢弃收口后空行。
- [ ] 单测：3 行重叠收口、gap-fill ≤1.5s、whisper 原生重叠。

## 3. 编辑器（editing/service.py）

- [ ] `_zoom_candidates` 兜底门控。
- [ ] `_semantic_teaser_windows` 质量门（kda 重叠 / 信号分 / highlight 窗口）。
- [ ] main-only 时写 `teaser_omitted_reason`（contracts additive）。
- [ ] 单测：兜底关闭无 p2 候选；杂乱推荐被拒→main-only+原因；合格推荐通过。

## 4. 报告（quality_report/service.py）

- [ ] teaser 下限告警：`teaser_omitted_reason` 非空 → 明细行。
- [ ] zoom 下限告警：仅当存在 KDA 事件且 zoom=0。
- [ ] 单测两条。

## 5. 文案（copywriter/service.py）

- [ ] prompt 标题上限 30→45；同步任何硬截断。
- [ ] 单测（若有现成 title 长度断言则更新）。

## 6. Gate A：`python -m pytest -q` 全量通过。

## 7. 重生成与验收（后台长任务，60s 心跳）

- [ ] subtitles 7 场 --force-reprocess（GPU 40–60 分钟）。
- [ ] highlight-planner → copywriter(hints) → edit-planner（7 场）。
- [ ] Gate B plan 层：cf m03 无 teaser；zoom 候选合理；预算维持。
- [ ] exporter 7 场（~40 分钟）→ copywriter(plain) → quality-report 0 告警。
- [ ] 抽验：SFX delta≈-1.5s、封面单张、标题长度、字幕活跃率新水位
      （必要时调 `subtitle_active_ratio_min` 并记录）。

## 8. 收尾

- [ ] validation-report.md（逐项对照 8 条反馈）。
- [ ] trellis-check（skill 内联）。
- [ ] spec 更新 + 提交。
- [ ] 提请 Jinson 人工复核主观项。
