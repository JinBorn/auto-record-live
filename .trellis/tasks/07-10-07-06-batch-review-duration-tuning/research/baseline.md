# Baseline — 2026-07-10（改动前）

pytest: 686 passed (89.7s)。
copywriter LLM 冒烟: deepseek-v4-flash, cached=1 failed=0 — `.env` 链路可用。

## quality-report 基线（现有导出，未重生成）

| Sample | Export min | Bitrate | Target min | Plan min | Max gap | KDA unc | Sub active | No-sub gaps | Teaser | BGM | SFX | Zoom | Warn |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|
| 4b5ec478 m02 | 17.74 | 16.16 | 10.34 | 17.74 | 45.0s | 0/9 | 84.5% | 9, max 16.7s | 2 | 5 | 7 | 3 | 0 |
| cf11bf9e m02 | 17.77 | 16.16 | 10.38 | 17.76 | 45.0s | 0/8 | 85.0% | 6, max 23.5s | 2 | 3 | 7 | 3 | 0 |
| cf11bf9e m03 | 22.86 | 16.16 | 10.33 | 22.86 | 45.0s | 0/6 | 87.3% | 5, max 40.6s | 3 | 4 | 3 | 3 | 0 |
| cf11bf9e m04 | 23.70 | 16.16 | 10.26 | 23.70 | 44.4s | 0/11 | 83.8% | 7, max 29.6s | 1 | 3 | 7 | 3 | 0 |
| bc90812b m01 | 16.21 | 8.15 | 9.01 | 16.97 | 45.0s | 0/10 | 27.9% | 25, max 36.4s | 1 | 0 | 4 | 1 | 1 |
| bc90812b m02 | 8.54 | 16.14 | 9.27 | 8.54 | 45.0s | 0/3 | 71.6% | 4, max 15.3s | 4 | 0 | 6 | 3 | 1 |
| bc90812b m03 | 9.54 | 8.15 | 8.64 | 9.54→10.31(plan) | 45.0s | 0/4 | 29.3% | 13, max 25.4s | 1 | 0 | 4 | 1 | 1 |

观察：

- 4 个主样本 plan 超预算 1.7–2.3×（budget = max(target×1.25, target+60s) ≈ 13 min）。
- bc90812b 导出为旧产物（m01/m03 字幕活跃 28–29%、8.15 Mbps 码率、BGM=0，各 1 条
  告警）——早于 ASR/BGM 升级；步骤 4 重生成前需核对其 subtitle 资产是否 whisper
  medium 产物，不是则该 session 需补跑 subtitles（与"字幕不重跑"计划的偏离点）。
- highlight-plans.jsonl 窗口构成：key_event 826–959s 是主膨胀源；continuity 最高
  526s（m04，23%），远超 10% 预算。
