# Implement — 07-06 批次综合复核与成片时长调优

执行顺序即依赖顺序；每步含验证命令。回滚点 = 每个 feat/fix commit。

## 0. 基线复核（不改代码）

- [ ] `python -m pytest -q` 全量通过，记录用例数基线。
- [ ] 对现有 7 个导出跑 quality-report，存基线表（时长、字幕活跃、teaser/BGM/
      SFX/zoom、KDA、告警）到任务 `research/baseline.md`：
      ```powershell
      python -m arl.cli quality-report --session-id session-20260617073649-4b5ec478 --match-index 2
      python -m arl.cli quality-report --session-id session-20260617073651-cf11bf9e --match-indices 2,3,4
      python -m arl.cli quality-report --session-id session-20260702092321-bc90812b --match-indices 1,2,3
      ```
- [ ] copywriter 以 `.env` 实配冒烟一次（确认 DeepSeek 可用，语义缓存命中即可）。

## 1. 批次复核修复

- [ ] `teaser_impact`：teaser 首段起点插入 SFX，缺素材静默跳过；单测
      （有素材→出现在音频指令；无素材→无指令无告警）。
- [ ] 验证 `multi_kill`→`kill_coin` 回退日志与行为（现有测试若未覆盖回退分支则补）。
- [ ] 确认 library 未消费类别零影响（现有 loader 行为，必要时补一个容忍性单测）。
- [ ] `python -m pytest tests/pipeline/test_editing_service.py -q`

## 2. 契约与可观测性

- [ ] `shared/contracts.py`：`HighlightPlanAsset` 增加
      `target_duration_seconds` / `budget_seconds` / `budget_exception_reason`
      （additive，默认 None）；planner 写入。
- [ ] `quality_report/service.py`：时长指标 + `plan_over_budget` warning +
      `ARL_QUALITY_REPORT_DURATION_BUDGET_ENFORCED` 开关；旧资产（None）跳过。
- [ ] 单测：超预算告警、例外降级 info、旧资产兼容。
- [ ] `python -m pytest tests/ -q -k "quality_report or contracts"`

## 3. 预算收缩（核心）

- [ ] `highlights/service.py`：`_shrink_windows_to_budget` 终段步骤 + 收缩顺序
      （continuity 超额→首尾 padding→内部低价值→KDA preroll floor→整窗淘汰），
      硬约束校验（KDA 覆盖、≤45s gap、最小窗时长）。
- [ ] `_protect_speech_boundaries` 增加 `max_extension_seconds` 可选参数
      （仅收缩后固定点使用，默认 None=旧行为）。
- [ ] 配置：`condensed_budget_kda_*_preroll_floor_seconds`、
      `ARL_HIGHLIGHTS_CONDENSED_BUDGET_SHRINK_ENABLED`。
- [ ] 单测：超预算多窗+KDA 事件→收敛达标且 KDA 全覆盖；语音跨切点吸附；
      预算内 plan 完全不变（幂等）；开关关闭→字节级旧行为；触底→例外原因记录。
- [ ] `python -m pytest tests/ -q`（全量，防回归）

## 4. 样本重生成与验收

字幕不重跑（ASR 配置未变，偏离 spec 标准流程，原因记录进报告）。顺序遵守
editing-quality.md：copywriter（语义 hints）先于 edit-planner。

- [ ] ```powershell
      $env:ARL_POSTPROCESS_PRESET='publish'
      python -m arl.cli highlight-planner --session-id session-20260617073649-4b5ec478 --match-index 2 --force-reprocess
      python -m arl.cli highlight-planner --session-id session-20260617073651-cf11bf9e --match-indices 2,3,4 --force-reprocess
      python -m arl.cli highlight-planner --session-id session-20260702092321-bc90812b --match-indices 1,2,3 --force-reprocess
      python -m arl.cli copywriter ... --force-reprocess   # 同上三组
      python -m arl.cli edit-planner ... --force-reprocess # 同上三组
      ```
- [ ] plan 层先验收（不必等导出）：7 样本 plan 时长 vs budget、KDA 覆盖、
      continuity 占比、最大源 gap——不达标回到步骤 3 调参，不进导出。
- [ ] 导出（长任务，检查点：后台/计划任务方式跑，~4 min/样本；每 60s 汇报进度）：
      `python -m arl.cli exporter ... --force-reprocess`（三组）
- [ ] `python -m arl.cli copywriter ...`（plain，修复 package）
- [ ] `python -m arl.cli quality-report ...` 7 样本 0 warnings。
- [ ] 抽查 1–2 个成片：teaser_impact 可听、无语音截断、无 KDA 跳变。

## 5. 收尾

- [ ] 任务 `validation-report.md`：7 样本前后对比表 + 例外记录 + 偏离说明。
- [ ] trellis-check（skill 内联，不派子代理）通过。
- [ ] spec 更新：`editing-quality.md`（时长预算阈值、收缩机制、"预算裁剪后回膨胀"
      教训）；`export-configuration.md` 若新增 env。
- [ ] 提交（feat + spec + task 工件），`data/` 不入库。

## Review Gates

- Gate A（步骤 3 后）：全量 pytest 通过才进入重生成。
- Gate B（步骤 4 plan 层验收）：预算达标 ≥6/7 才启动导出，防止浪费 30+ 分钟
  ffmpeg 批次。
