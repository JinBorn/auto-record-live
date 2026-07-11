# 07-06 批次综合复核与成片时长调优

## Goal

1. 对 07-06 demo2-quality-parity 批次（8 个子任务，已归档）做一轮完整复核：全量测试、
   以最终用户配置（`.env` 的 DeepSeek LLM、`data/sfx` 用户素材库）验证真实链路，
   修复复核中发现的遗留缺口。
2. 把 publish 成片时长收敛到动态目标预算内。07-10 集成复核记录的遗留项：
   plan 时长 17.7–23.7 分钟 vs 密度分析目标 ~10.3 分钟（预算上限应为
   max(target×1.25, target+60s) ≈ 13 分钟）。

## Background

- 07-10 集成复核（`archive/2026-07/07-06-demo2-quality-parity/validation-report.md`）
  已确认 686 tests 通过、0 质量告警，时长超标被明确记录为"future tuning headroom"。
- 诊断数据（data/tmp/highlight-plans.jsonl，2026-07-10）：
  - `condensed_key_event` 每场累计 826–959s，是主要膨胀源；
  - `condensed_continuity` 最高 526s（m04，约占 plan 23%），远超 07-02 定下的
    10% 预算；
  - 预算裁剪发生在管线中段，其后的 KDA 恢复→语音边界保护→桥接步骤会重新膨胀，
    且没有最终预算复检。
- ASR 升级（字幕活跃率 39–47% → 84–87%）是诱因：语音边界保护的可延伸空间大增。

## Requirements

### A. 批次复核

- pytest 全量通过（当前基线 686）。
- SFX 素材库真实集成验证：
  - `teaser_impact` 已在 README 与 `library.json` schema 中声明，但代码无任何消费
    点——需闭环（实现消费或修正文档，倾向实现：teaser 开场卡点播放，素材缺失时静默跳过）。
  - `multi_kill` 未登记音轨时回退 `kill_coin` 属预期行为，验证并保留。
  - 用户自加类别 `mistake` / `boom` / `pew` / `transition_bruh` 当前无触发语义，
    本任务不实现其触发（记录到报告，留待后续任务），不得因未知类别报错。
- LLM 链路以 `.env` 实配（deepseek-v4-flash）验证 copywriter 正常出稿。

### B. 成片时长调优

- 每场 plan 总时长收敛到预算内：plan ≤ max(target×1.25, target+60s)。
  允许个别样本有记录在案的例外（受保护内容密度在数学上不允许再裁），但 7 个样本
  中至少 6 个达标，且超标样本相对 07-10 基线时长下降 ≥25%。
- 收敛机制必须是"低价值优先裁剪"式的多窗口收缩，不允许退化为现有预算兜底的
  单窗口替换结果。
- 不可破坏既有防回归保证：
  - KDA uncovered 保持 0；
  - 不切断活跃语音（speech boundary 保护仍然有效）；
  - 相邻源时间跳变 ≤45s；
  - `condensed_continuity` 合计 ≤ 渲染时长 10%（除非 45s gap 约束确需例外，须记录）；
  - editing-quality.md 全部既有阈值（字幕活跃 ≥55%、teaser 1–3、zoom 1–4、
    kill SFX ≤6、标题非原始字幕）继续满足。
- quality-report 增加时长可观测性：输出 target / plan / export 时长与预算比，
  超预算发 warning，使时长回归从此可度量。
- 默认（非 publish/condensed）预设行为保持不变；新契约字段全部 additive 且有默认值。
- `data/` 运行时产物不进 git。

## Acceptance Criteria

- [ ] pytest 全量通过，无回归。
- [ ] 重新生成 4 个 07-02 验证样本（4b5ec478 m02；cf11bf9e m02–04）+ 新主播样本
      （bc90812b m01–03）：≥6/7 plan 时长在预算内，例外样本记录原因且较基线降 ≥25%。
- [ ] 全部 7 个样本 quality-report 0 warnings（含新增时长告警）。KDA uncovered=0。
- [ ] `teaser_impact` 闭环：有素材时 teaser 开场可听到；无素材静默跳过；有测试。
- [ ] quality-report 输出时长指标并有测试覆盖。
- [ ] `.trellis/spec/backend/editing-quality.md` 更新：时长预算阈值、收敛机制、
      调优经验。
- [ ] 任务目录产出 validation-report.md，含 7 样本前后对比
      （时长、字幕活跃、teaser/BGM/SFX/zoom、KDA、告警）。

## Out Of Scope

- `mistake` / `boom` / `pew` / `transition_bruh` 等新 SFX 类别的触发语义。
- ASR 模型、匹配边界检测、BGM/封面设计的改动。
- 非 condensed 模式的时长控制。
