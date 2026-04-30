# 继续开发自动录制直播功能（直链优先）

## Goal

在现有 MVP 骨架上推进“自动录制直播”能力，从当前可跑通的状态检测与占位产物，向可用的端到端录制链路持续迭代，并保持本地优先（Windows 探测 + WSL2 编排）的架构约束。

## What I already know

* 仓库已有 `windows_agent -> orchestrator -> recorder -> segmenter -> subtitles -> exporter` 主流程骨架。
* `windows_agent` 已支持 HTTP/Playwright 探测开播状态，并可在事件中携带 `stream_url`。
* `orchestrator` 会消费开播/停播事件并创建录制任务，状态持久化已具备。
* `recorder` 已支持在 `stream_url` 存在且开启开关时调用 `ffmpeg` 直录，否则回退为占位录制文件。
* README 明确标注“稳健的直链获取与浏览器录制集成”仍未完成，当前真实浏览器成功路径仍多为 `browser_capture` 且未接入真实录制。

## Assumptions (temporary)

* 本轮优先做“可稳定前进的一步”，而不是一次性完成全部直播处理能力。
* 保持现有模块边界，不做大规模架构重写。
* 继续以可测试、可回退的小步迭代为主。

## Open Questions

* 当前无阻塞问题；若实现阶段发现抖音页面结构限制，再补充技术调研并回写 PRD。

## Requirements (evolving)

* 本轮优先实现“直链获取”：Windows Playwright 探测阶段尽可能提取真实 `stream_url`。
* 当提取到 `stream_url` 时，事件 `source_type` 应标记为 `direct_stream`，并沿 `orchestrator -> recorder` 链路触发 ffmpeg 直录。
* 当未提取到 `stream_url` 时，保持当前 `browser_capture`/占位回退行为不破坏。
* 新增或修改实现需与现有配置项、状态模型和流水线约定兼容。
* 关键路径需有自动化测试覆盖（至少单元/集成其一）。

## Acceptance Criteria (evolving)

* [ ] Playwright 探测在识别开播时可输出 `stream_url`（在可提取场景）。
* [ ] 录制流程在开启 ffmpeg 开关后，优先消费上述 `stream_url` 进行直录。
* [ ] 无 `stream_url` 场景保持现有回退行为（不引入回归）。
* [ ] 相关测试新增或更新并通过。
* [ ] `lint` 和 `type-check` 通过。
* [ ] PRD 中本轮范围与非范围清晰。

## Technical Approach

* 在 `scripts/probe_douyin_room.mjs` 增加直链提取逻辑（优先解析页面/请求中可识别的 `m3u8`/`flv` 候选链接）。
* `src/arl/windows_agent/probe.py` 继续消费脚本返回的 `streamUrl`，并保证 `sourceType` 与 `streamUrl` 一致。
* 保持 `orchestrator` 与 `recorder` 现有契约，重点补测试验证直链透传和回退行为。

## Decision (ADR-lite)

**Context**: 自动录制主路径尚未打通，当前真实浏览器路径多数停在 `browser_capture`。  
**Decision**: 本轮选择“直链优先”，先补齐 `stream_url` 提取与直录主路径。  
**Consequences**: 可尽快获得可用录制主链路；浏览器录制回退与语义分段延后到后续迭代。

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky

## Out of Scope (explicit)

* 一次性完成全链路生产级能力（包括所有异常恢复、完整 LoL 语义识别、完整离线 ASR 集成）。
* 偏离当前 MVP 架构的大规模重构。
* 本轮不实现完整浏览器录屏回退能力。
* 本轮不实现 LoL 语义分段与 ASR 集成。

## Technical Notes

* 已查看：
* `README.md`
* `src/arl/windows_agent/probe.py`
* `src/arl/windows_agent/service.py`
* `src/arl/orchestrator/service.py`
* `src/arl/recorder/service.py`
* 当前任务目录：`.trellis/tasks/04-25-continue-auto-live-recording/`
