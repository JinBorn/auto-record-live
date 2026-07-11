# 成片人工复核修复（8 项）

## Goal

修复 Jinson 人工观看 07-10 成片批次后提出的 8 项体验问题，全部收敛后重生成
7 个验证样本并交回人工复核。

## Requirements（按用户反馈逐项）

1. **击杀金币音效**：现在比击杀瞬间晚 1–2 秒（SFX 对齐到 KDA OCR 首见时间
   `current_at`，含 HUD/OCR 延迟），且音量偏小。要求：publish 预设下提前
   ~1.5s（`sfx_timing_offset_seconds`，可 env 覆盖）、默认增益 -12dB → -7dB。
   非 publish 预设行为不变。
2. **字幕并发行数 ≤2**：显示平滑不得让 3 条及以上字幕同屏（第 i 条的结束
   时间不得超过第 i+2 条的开始时间；对 whisper 原生重叠同样收口）。
3. **字幕滞留/提前**：`display_max_gap_fill_seconds=8` 的空隙填充导致说完话
   字幕仍挂着；要求填充上限降到 ~1.5s、`display_min_duration` 3.5→2.5s。
   代价：字幕活跃率下降（76–83% 会回落）；若跌破 0.55 阈值，按新的真实水位
   调整阈值并记录理由（用户体验优先于旧指标）。
4. **源视频有 BGM 时不加 BGM**：全局跳过的多数阈值
   `bgm_source_music_majority_threshold` 0.60→0.35（源音乐覆盖超过 1/3 即整场
   不加），跨度回避逻辑保留。
5. **封面只要一张**：封面候选数默认为 1（新增 `CopywriterSettings.
   cover_max_candidates`，env 可调回多张）。
6. **zoom 时机**：去掉 priority=2 的"任意段落中点兜底放大"（新增
   `zoom_fallback_enabled`，默认 False）；保留 KDA 击杀与聊天刷屏触发。
   若个别样本因此 zoom=0，quality-report 不应误报（见验收）。
7. **无精华可不加片头**：LLM teaser 推荐必须过质量门（与 KDA 事件跨度重叠，
   或 teaser 信号分 >0，或落在 highlight_keyword 窗口）；全部不合格时输出
   main-only，并在 `EditPlanAsset.teaser_omitted_reason`（additive）记录原因；
   quality-report 对有原因记录的 0-teaser 不告警。cf11bf9e m03 是验收样本。
8. **文案可以更长**：标题上限 30→45 紧凑字符（LLM prompt 与启发式阈值同步），
   cover_lines/summary 限制不变。

## Acceptance Criteria

- [ ] pytest 全量通过；每项修复有单测（含回归：≤2 并发行、gap-fill 上限、
      teaser 质量门拒绝路径、zoom 无兜底、封面单张）。
- [ ] 7 个样本全链路重生成（字幕平滑参数变更 → subtitles 起全部重跑）后
      quality-report 0 告警（阈值若调整，spec 同步记录理由）。
- [ ] cf11bf9e m03 重生成后无 teaser（main-only）且报告不告警。
- [ ] 击杀 SFX 对齐时间 = KDA current_at − 1.5s（quality-report delta 明细
      体现），增益 -7dB。
- [ ] 封面每场只产出 1 张 cover-01.jpg。
- [ ] 标题可超过 30 字、≤45 字（至少 1 个样本实际产出验证）。
- [ ] spec（editing-quality.md / export-configuration.md）与
      validation-report.md 更新；提交。
- [ ] 最终交 Jinson 人工复核主观项（音效时机/字幕观感/zoom 时机/片头/文案）。

## Out Of Scope

- KDA OCR 采样与时间戳精度改造（本轮用固定提前量补偿）。
- `mistake`/`boom`/`pew`/`transition_bruh` 新 SFX 类别触发语义。
- 封面排版/构图设计变更（只改数量）。
