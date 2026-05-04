# Fix orchestrator state UTF-8 decode error

## Goal

Recorder 启动时读取 `data/tmp/orchestrator-state.json` 报 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb7 in position 437`，导致 `windows-recorder-loop.ps1` 整轮失败。要让 orchestrator state 文件在中文 Windows 上也能稳定读写，并恢复 recorder pipeline。

## What I already know

- 报错点：`src/arl/recorder/service.py:187` `OrchestratorStateFile.model_validate_json(path.read_text(encoding="utf-8"))`
- 写入点（漏配 encoding 的元凶）：`src/arl/orchestrator/state_store.py`
  - L18：`self.state_path.read_text()`（裸读）
  - L22：`self.state_path.write_text(state.model_dump_json(indent=2) + "\n")`（裸写）
  - 这是 codebase 里**唯一**没显式 `encoding="utf-8"` 的 state store；recorder/exporter/recovery/subtitles/segmenter/windows_agent 全部都显式 UTF-8。
- 实测确认：`data/tmp/orchestrator-state.json`（1348 字节）当前是 GBK/CP936 编码，里面 `streamer_name` 是中文（解码后大概是「小风疯头」），`0xb7` 就是中文字 GBK 第二字节。
- 触发原因：Windows 中文系统下 Python 3.14 `Path.write_text` 没指定 encoding 默认 fallback 到 locale（CP936），写入直播间名等中文字段时就落成 GBK；recorder 强制 UTF-8 读取必炸。
- 当前坏文件中含活跃会话 + cursor_offset=5182 + active recording job（queued 状态），完全删除会丢失 cursor 并 replay 全部 agent events。
- 测试：仅有 `tests/orchestrator/test_service.py`，没有 `test_state_store.py`；`tests/windows_agent/test_state_store.py` 是同形参考。
- spec：`.trellis/spec/backend/orchestration-contracts.md` 列了所有 durable file path 但**没**显式约定 encoding；`quality-guidelines.md` 也没相关 forbidden pattern。

## Assumptions (temporary)

- 没有别的下游进程会以非 UTF-8 方式生产或消费 `orchestrator-state.json`。
- recovery 状态文件 `recovery-state.json` 不受影响（recovery service 已经显式 UTF-8）。
- 现行 GBK 内容里没有非 GBK 字节（即 fallback 解码不会再炸）。

## Open Questions

- **Q1**：现有这份坏掉的 `orchestrator-state.json` 怎么处理？（见下面 Approach 选项）

## Requirements (evolving)

- `OrchestratorStateStore.save` / `OrchestratorStateStore.load` 必须显式使用 UTF-8 写读。
- 修复后 `windows-recorder-loop.ps1` 能正常跑（读取不再抛 UnicodeDecodeError）。
- 不破坏既有 orchestrator 单测；新加 state_store 编码往返单测。
- 不引入跨平台行为差异（macOS / Linux / Windows 都强制 UTF-8）。

## Acceptance Criteria (evolving)

- [ ] `state_store.py` `read_text` / `write_text` 都显式 `encoding="utf-8"`。
- [ ] 新增 `tests/orchestrator/test_state_store.py`：往返一次包含中文 `streamer_name` 的状态，断言能 round-trip。
- [ ] 现有坏文件场景有明确处理路径（看 Q1 决议）。
- [ ] `pytest -q tests/orchestrator` 全绿。
- [ ] 本地手动跑 `python -m arl.cli recorder` 不再报 UnicodeDecodeError。

## Definition of Done

- 单测 + lint 通过。
- spec 更新：`orchestration-contracts.md` 加一条「所有 orchestrator/recorder/recovery/subtitles/segmenter state 文件必须以 UTF-8 读写」。
- `quality-guidelines.md` Forbidden Patterns 加一条「裸用 `read_text()/write_text()` 处理状态/事件 JSON 文件」。
- 把这次「中文 locale 下默认 encoding 不是 UTF-8」的教训沉淀到 spec 里，避免别的 stage 后面踩同样的坑。

## Out of Scope (explicit)

- 重新设计 state file schema（不动 model）。
- 全仓审计其他可能的非 UTF-8 文件 IO（dotenv、SRT 等）。
- recovery / orchestrator 之间状态迁移工具。
- Python 3.14 `PYTHONUTF8=1` 全局开关（环境层面策略，不在本 PR 范围）。

## Technical Notes

- 涉及文件：`src/arl/orchestrator/state_store.py`
- 新建：`tests/orchestrator/test_state_store.py`
- spec：`.trellis/spec/backend/orchestration-contracts.md`、`.trellis/spec/backend/quality-guidelines.md`
- 现有坏文件：`data/tmp/orchestrator-state.json`（GBK，1348B，含 active session + cursor_offset=5182）

## Research References

- 已经在主 agent 内完成 grep 全仓 `read_text|write_text` 用法对比，无需 sub-agent 研究。
- 现有 codebase 共识非常清晰：所有 state store 都显式 UTF-8，本案纯 patch oversight。

## Feasible approaches for the existing broken file (Q1)

**Approach A: Auto-heal on load (Recommended)**

- 做法：`load()` 先 `read_bytes()`，尝试 UTF-8 解码；失败则 fallback 到 `gbk` / `cp936`；解析成功后下次 `save()` 自然以 UTF-8 重写。
- 优点：用户无感迁移；保留 cursor_offset / active session / recording job；幂等。
- 缺点：多一段 fallback 分支；万一坏文件不是 GBK 仍然会炸（但本场景明确是 GBK）。

**Approach B: Just fix encoding, advise manual delete**

- 做法：只加 `encoding="utf-8"`；让用户手动删除 `data/tmp/orchestrator-state.json` 并 rebuild。
- 优点：实现最干净；行为简单可推断。
- 缺点：cursor 重置 → replay 全部 5182 字节 agent 事件；当前 active session 状态会从 event log 重建（在 MVP 设计下事件日志是 source-of-truth，所以理论上可行，但 active recording job 状态会回到 queued 重新被 recorder 拉取）。

**Approach C: Hard fail with actionable error**

- 做法：`load()` 捕获 `UnicodeDecodeError` 后抛带文件路径和恢复指引的自定义异常。
- 优点：行为显式，强制人为决策。
- 缺点：用户体验最差，同一坑日后再踩还得手动；对自动循环不友好。

## Decision (ADR-lite)

**Context**：现有 `data/tmp/orchestrator-state.json` 已被 GBK 写入，里面含 cursor_offset=5182 + active session + active recording job；如果直接强制 UTF-8 读，下次启动必然崩；如果让用户手动删，event log 会从 0 重放且当前 active session 状态需要从事件流重建。

**Decision**：选 Approach A（Auto-heal on load）。`OrchestratorStateStore.load()` 改为先 `read_bytes()`，依次尝试 UTF-8 → GBK/CP936 解码；任一成功后正常 `model_validate_json`，下次 `save()` 自动以 UTF-8 重写；两种 codec 都失败则抛带文件路径的明确异常（行为退化为 Approach C，作为兜底）。

**Consequences**：
- 用户既存状态（cursor、active session、recording job）无感保留。
- 下一轮 save 后文件就是干净 UTF-8，自愈分支后续不会再被触发。
- 仅写入侧需要新增 `encoding="utf-8"`（save）；读取侧多一段 fallback 分支但行为收敛。
- 风险：万一文件不是 GBK 而是其他 codec（极少见），仍会报错——可接受，因为兜底异常会指向具体文件路径方便人工处理。
- 后续维护：自愈逻辑只服务于一次性历史迁移，等所有用户都跑过一次后可以考虑清理（但保留无害）。
