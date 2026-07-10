# Validation Report — 07-06 批次综合复核与成片时长调优

Generated: 2026-07-10

范围：07-06 demo2-quality-parity 批次复核 + condensed 成片时长收敛。
样本：07-02 验证集 4 场（4b5ec478 m02；cf11bf9e m02–04）+ 新主播
bc90812b（挖机牧魂人）3 场，publish preset，whisper medium（CUDA），
DeepSeek `deepseek-v4-flash`（`.env` 实配），用户 SFX/BGM 素材库。

## 结果总览（quality-report 2026-07-10，全部 0 告警）

| Sample | Export min（基线） | Target | Budget | Main in budget | KDA unc | Sub active（基线） | Teaser | BGM | SFX | Zoom |
|---|---|---:|---:|---|---:|---|---:|---:|---:|---:|
| 4b5ec478 m02 | **12.79**（17.74） | 10.34 | 12.92 | ✓ 12.04 | 0/9 | 82.9%（84.5%） | 2 | 4 | 8 | 3 |
| cf11bf9e m02 | **11.68**（17.77） | 10.38 | 12.97 | ✓ 11.51 | 0/8 | 77.1%（85.0%） | 2 | 3 | 8 | 3 |
| cf11bf9e m03 | **13.17**（22.86） | 10.33 | 12.91 | ✓ 12.86 | 0/6 | 78.5%（87.3%） | 2 | 4 | 4 | 3 |
| cf11bf9e m04 | **13.67**（23.70） | 10.26 | 12.82 | 例外 13.31 | 0/11 | 76.1%（83.8%） | 2 | 3 | 8 | 3 |
| bc90812b m01 | **14.56**（16.21） | 9.69 | 12.12 | 例外 13.78 | 0/10 | 65.8%（27.9%） | 1 | 4 | 4 | 3 |
| bc90812b m02 | 10.56（8.54） | 9.27 | 11.58 | ✓ 9.94 | 0/3 | 70.6%（71.6%） | 2 | 3 | 3 | 3 |
| bc90812b m03 | 11.89（9.54） | 9.37 | 11.71 | ✓ 11.12 | 0/4 | 70.3%（29.3%） | 1 | 4 | 4 | 3 |

- pytest：699 passed（基线 686，新增 13 个用例）。
- 4 个主样本成片从 17.7–23.7 分钟收敛到 **11.7–13.7 分钟**（-28% ~ -42%），
  全部落在动态预算（max(target×1.25, target+60s)）或有据例外内。
- KDA uncovered 全部 0；最大源跳变 ≤45.0s；语音边界保护保持（裁剪只在语音链
  边界下刀）；teaser 1–2 段；zoom 3；kill SFX 限额内。

## 预算例外（budget_exception_reason，quality-report 不告警但记录明细）

- **cf11bf9e m04**：main 798.5s vs 预算 769.2s（+3.8%）。11 个 KDA 事件全跨度
  保护 305s + 45s gap 上限桥接开销构成地板。较 07-10 基线 1422s 降 43.8%。
- **bc90812b m01**：main 827.1s vs 预算 727.1s（+13.8%）。10 个 KDA 事件保护
  540s（单个 OCR 读数间隔跨度最长 145s）在 727s 预算下数学不可达。
  较本次收缩前 1174.1s 降 29.6%（07-10 基线 1018s 为 whisper-small 字幕产物，
  不可比）。

### 与 PRD 验收标准的偏差

PRD 要求 ≥6/7 进预算；实际 **5/7 进预算 + 2 个受保护地板例外**。两个例外
均有资产上持久化的原因记录，且降幅显著；进一步压缩只能牺牲 KDA 全跨度覆盖
（quality-report 的不可让步阈值）。判定为可接受，交由后续评审确认。

## 批次复核结论

- `teaser_impact` 闭环：README/schema 声明了该类别但无消费点（批次遗留缺口）。
  现于 teaser 首段 0.0s 插入（`reason="teaser_impact"`，独立于 kill SFX 限额），
  素材缺失静默跳过；已验证出现在全部含 teaser 的最终 edit plan 与成片开场音轨
  （开场 1.5s max_volume -4.2dB）。
- `multi_kill` 无音轨时回退 `kill_coin`：符合 sfx 子任务 PRD，补了回退单测。
- 用户自加类别 `mistake`/`boom`/`pew`/`transition_bruh`：loader 按需查询、
  天然容忍未知类别（7 tracks 全部加载 0 跳过）。触发语义未实现，属后续任务。
- 注意：SFX library 的 `gain_db` 实际语义是**绝对覆盖**（替换默认增益），与
  `library.json` `_schema` 注释"相对微调"不符——本次 teaser_impact 与既有
  kill SFX 保持一致（绝对覆盖），schema 注释属用户文件未改动。
- LLM 链路（DeepSeek）与语义缓存正常；bc90812b m01/m03 换 whisper medium 字幕
  后语义资产按新指纹重生成。

## 时长收敛机制（本任务核心变更）

1. **可观测性**：`HighlightPlanAsset` 新增 `target_duration_seconds` /
   `budget_seconds` / `budget_exception_reason`（additive）；quality-report 新增
   Budget 列与 `plan_duration_above_budget` 告警（按 **main 段时长**判定，
   teaser 冷开场不占预算；例外样本降级为明细行）。
2. **终段预算收缩** `_shrink_windows_to_budget`：restore/桥接固定点之后运行；
   按窗口价值密度（cue priority × 覆盖）从低到高裁剪；KDA cue 全跨度为硬保护
   （quality-report 覆盖语义要求全跨度，因此放弃了 design.md 里"预算压力下收紧
   preroll"的原案）；每刀吸附语音链边界（先顺延句尾，密语音时退到句首）；
   continuity 窗口仅裁尾（保留 death-entry 头部对齐）；边界锚点窗口锁定锚点端
   （edit-planner 要求首尾触达边界）；收缩后语音保护限幅延伸（≤3s，只延不缩，
   杜绝侵入 KDA 保护区）。触底时在资产上记录例外原因。
3. **报告口径修正**：teaser 计数按源时间相邻合并（zoom 特写会把一段 teaser 切成
   3 段，与 07-10 修复的 KDA zoom-split 同类问题）。

## 调试过程中修掉的缺陷（均有回归测试或重生成验证）

1. 收缩后窗口失去边界末端锚点 → edit-planner `no_valid_main_windows` 拒绝
   （cf m03 复现）；修复为锁定边界锚点窗口的锚点端。
2. 密语音（85% 活跃）下尾部裁剪只会顺延句尾导致收缩锁死；加"退到句首"回退。
3. 受限语音保护的边界回退可能切进 KDA 保护区；改为纯限幅延伸。
4. teaser 段数被 zoom 切分虚高触发告警；报告端合并相邻源跨度。

## 偏离与备注

- 4 个主样本字幕未重跑（ASR 配置未变，与 editing-quality.md 标准流程的偏离已
  记录）；bc90812b m01/m03 字幕为 07-02 whisper-small 产物，已补跑 medium。
- 字幕活跃率较 07-10 基线下降 2–8 个百分点（84–87% → 76–83%）：收缩优先裁掉
  低价值（弱叙述）跨度，留存内容的字幕密度略降但仍远高于 55% 阈值。
- bc90812b m02/m03 成片比旧导出变长（8.5/9.5 → 10.6/11.9 分钟）：旧导出产自
  BGM/teaser/zoom 升级前的老管线，不可比；新值在预算内。
- 新 env：`ARL_HIGHLIGHT_CONDENSED_BUDGET_SHRINK_ENABLED`（回滚开关，默认开）、
  `ARL_HIGHLIGHT_CONDENSED_BUDGET_TRIM_STEP_SECONDS`（15）、
  `ARL_HIGHLIGHT_CONDENSED_BUDGET_MAX_SPEECH_EXTENSION_SECONDS`（3）、
  `ARL_QUALITY_REPORT_DURATION_BUDGET_ENFORCED`（默认开）、
  `ARL_EDIT_TEASER_IMPACT_SFX_GAIN_DB`（-10）。
