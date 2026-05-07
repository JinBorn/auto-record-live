# 多平台 watcher 架构研究

> **元信息**：本文基于 (1) 仓库现状代码 (2) Python 生态通用 Strategy/Registry 设计模式 (3) Bililive-go / DDTV / streamlink 等多平台录制器的常识架构。trellis-research 子代理上游 500 不可用，由主代理直接基于仓库代码 + 工程常识写成。涉及外部项目源码引用是间接的（项目目录结构与设计取自社区描述），不是 raw 文件抓取。

## TL;DR

针对本仓库通用化 scope 的推荐方案：

- **接口**：抽象 `PlatformProbe` ABC（`detect() -> AgentSnapshot` 单方法核心 + 类属性 `platform_name: ClassVar[str]`），现有 `DouyinRoomProbe` 改名/适配实现。
- **注册**：显式 dict（`PROBE_REGISTRY = {"douyin": DouyinRoomProbe, "bilibili": BilibiliRoomProbe}`），不用 entry point —— 闭包小、调试直观、未来加平台改两行字典就行。
- **配置**：现阶段保留 `.env`（向后兼容硬约束），但把扁平 `ARL_DOUYIN_*` 改成「`ARL_PLATFORMS=douyin,bilibili` + 每平台 `ARL_<PLATFORM>_<KEY>` 前缀」的规则化方案。Pydantic 嵌套 model 自然适配。yaml/json 配置文件留给后续 PR（当 platforms 数量 ≥ 3 时再升级）。
- **运行时调度**：**单进程串行轮询**（`for probe in probes: probe.detect()`），简单可靠，故障互不阻断；**不引入 asyncio / 多进程**——这是个 30s 轮询频率的 IO-bound 任务，串行完全够用。Playwright probe（抖音）确实慢但只占抖音主播在线那段时间。
- **事件流**：**单 jsonl + AgentEvent 加 `platform` 字段**。orchestrator 改成按 `(platform, room_url)` 维度的 session key。
- **改动量估算**：约 **10-12 文件**，比扁平估的 15+ 小。具体清单见第 8 节。

---

## 1. 仓库现状摘录

| 文件 | 现状 | 是否要改 |
|---|---|---|
| `src/arl/config.py` | `Settings.douyin: DouyinSettings` 单字段；env 用 `ARL_DOUYIN_*` 前缀；`load_settings()` 手写映射 | ✅ 必改：`Settings.platforms: list[PlatformSettings]` |
| `src/arl/windows_agent/probe.py` | `DouyinRoomProbe` 类名/常量写死；`detect()` 返回 `AgentSnapshot`；Playwright 优先 + httpx fallback | ✅ 适配：抽 ABC，原类改名 `DouyinRoomProbe` 实现 ABC；新增 `BilibiliRoomProbe` |
| `src/arl/windows_agent/service.py` | `__init__` 里 `self.probe = DouyinRoomProbe(settings)` 单 probe；`run_once()` 跑一次 | ✅ 必改：`self.probes: list[PlatformProbe] = [...]`；`run_once()` 遍历 |
| `src/arl/windows_agent/state_store.py` | 状态 jsonl + last_snapshot；按文件操作；无 platform key | ✅ 必改：last_snapshot 改 `dict[platform, AgentSnapshot]`；event log 增 platform 字段 |
| `src/arl/windows_agent/models.py` | `AgentSnapshot` / `AgentStateFile` / `AgentEvent`；字段平台中立但无 platform 字段 | ✅ 必改：加 `platform: str` 字段 |
| `src/arl/shared/contracts.py` | `LiveState`、`SourceType` 等枚举 | ⚠️ 看情况：若加 `Platform` 枚举值得在这里 |
| `src/arl/orchestrator/` | 消费 windows-agent-events.jsonl，按 streamer_name 维持 session | ✅ 必改：session key 改成 `(platform, room_url)` |
| `src/arl/recorder/` | 接收 orchestrator 派发的录制 job | ⚠️ 多半要改：`stream_headers` 字段从 probe 透传到 ffmpeg 命令 |
| `scripts/probe_douyin_room.mjs` | Playwright 探测脚本 | 保留，不改 |
| `scripts/windows-agent-loop.ps1` | 长跑包装 `python -m arl.cli windows-agent` | ⚠️ 看情况：如果保持单进程串行，则不改 |
| `.env.example` | 抖音 env 范例 | ✅ 必改：加 `ARL_PLATFORMS=douyin` + B 站范例 |
| `tests/` | 抖音测试 | ✅ 必加 B 站测试 + scope 调度路径回归 |

`AgentSnapshot` **字段命名本身已经平台中立**（state、streamer_name、room_url、source_type、stream_url、reason、detected_at），这是迁移的最大利好——下游 orchestrator/recorder 不需要语义重构，只是要"知道事件来自哪个平台"。

---

## 2. PlatformProbe 接口设计

### 2.1 推荐签名

```python
# src/arl/windows_agent/platform_probe.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import ClassVar
from arl.windows_agent.models import AgentSnapshot

class PlatformProbe(ABC):
    """One platform = one probe class. Each instance bound to one room."""
    platform_name: ClassVar[str]  # e.g. "douyin", "bilibili"

    @abstractmethod
    def detect(self) -> AgentSnapshot:
        """Return a snapshot. MUST set snapshot.platform = cls.platform_name."""

    # Optional: stream header injection for downstream recorder.
    # Default returns empty dict (compatible with douyin which needs no headers).
    def stream_headers(self) -> dict[str, str]:
        return {}
```

### 2.2 注册机制对比

| 方案 | 优 | 劣 | 推荐 |
|---|---|---|---|
| **A. 显式 dict** | 调试直观；启动不依赖 entry point 解析；闭包小 | 加平台需改框架代码（一行）| ✅ |
| B. setuptools entry points | 第三方插件友好；不动框架 | 启动开销；调试难（看不到注册顺序）；过度灵活给 MVP | ❌ |
| C. 装饰器自注册 + import 副作用 | 接近 A 但把"加一行字典"换成"在文件顶 `@register`" | 依赖 import 顺序；有时引发循环 import | ⚠️ 可接受 |

**采纳 A**。注册表位置：`src/arl/windows_agent/registry.py`：

```python
PROBE_REGISTRY: dict[str, type[PlatformProbe]] = {
    "douyin": DouyinRoomProbe,
    "bilibili": BilibiliRoomProbe,
}

def build_probes(platforms: list[PlatformSettings]) -> list[PlatformProbe]:
    return [PROBE_REGISTRY[p.type](p) for p in platforms]
```

---

## 3. 配置层迁移

### 3.1 形态对比

| 形态 | 优 | 劣 | MVP 选择 |
|---|---|---|---|
| 扁平 env（`ARL_DOUYIN_*` / `ARL_BILIBILI_*`）| 改动小；与现有抖音兼容；用户熟悉 | 平台多了 env 表会变长；不能描述结构（list of platforms with shared keys）| ✅ MVP 用这个 |
| yaml 文件（`platforms.yaml`）| 表达力强；多平台天然自然 | 引依赖（PyYAML）；与现有 .env 流程并存增加复杂度 | ⚠️ 第二版 |
| toml 文件（pyproject 风格）| Python 标准库支持（3.11+）；声明式 | 比 env 学习成本略高 | ⚠️ 第二版 |
| pydantic-settings | 自动 env 映射 | 引依赖；现仓库手写 _load_dotenv 与之冲突 | ❌ 不引入 |

### 3.2 全局项 vs 平台项

**全局**（一份）：
- `ARL_PLATFORMS=douyin,bilibili` —— 启用的平台列表（顺序就是 polling 顺序）
- `ARL_PROFILE_ROOT_DIR=data/tmp/profiles` —— Playwright profile 父目录（每平台子目录隔离）
- `ARL_AGENT_POLL_INTERVAL_SECONDS=30` —— 全局 polling 频率（替代 `ARL_DOUYIN_POLL_INTERVAL_SECONDS`）
- 现有 storage / orchestrator / subtitles / recording / export 字段不动

**平台项**（每平台一份）：
- `ARL_<PLATFORM>_ROOM_URL` （required）
- `ARL_<PLATFORM>_STREAMER_NAME`（required）
- `ARL_<PLATFORM>_USE_PLAYWRIGHT`（默认 false；douyin=true，bilibili=false）
- `ARL_<PLATFORM>_PLAYWRIGHT_SCRIPT`（only used if USE_PLAYWRIGHT=true）
- `ARL_<PLATFORM>_PLAYWRIGHT_TIMEOUT_MS`
- `ARL_<PLATFORM>_PROFILE_DIR`（默认 `<PROFILE_ROOT_DIR>/<platform>`，仅 Playwright 平台用）
- 平台扩展键：抖音的 `ALLOW_BROWSER_CAPTURE_FALLBACK` 这种保持 `ARL_DOUYIN_*` 前缀

### 3.3 抖音向后兼容

**硬约束**：现有用户的 `.env` 里 `ARL_DOUYIN_ROOM_URL=...` 不能失效。

方案：

1. 新代码读 `ARL_PLATFORMS=douyin,...`；如果该变量缺失，**默认回退为 `douyin`**（保持现有用户 zero-config 升级）。
2. 旧的 `ARL_DOUYIN_POLL_INTERVAL_SECONDS` 优先于新 `ARL_AGENT_POLL_INTERVAL_SECONDS`（向后兼容兜底）。
3. `.env.example` 同时给出新 + 旧风格注释，告诉用户推荐用新风格。
4. 不做激进的 deprecation warning（MVP 阶段噪声）。

---

## 4. 运行时调度模型

### 4.1 单进程串行（推荐）

```python
class WindowsAgentService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.probes = build_probes(settings.platforms)
        self.state_store = ...  # 多平台 last_snapshot

    def run_once(self) -> None:
        for probe in self.probes:
            try:
                snapshot = probe.detect()
                self._dispatch(snapshot)
            except Exception as exc:
                log("windows-agent", f"probe={probe.platform_name} crashed: {exc}")
```

- **故障隔离**：try/except 包裹每次 detect，单平台异常不阻断另一个。
- **Playwright 慢但 OK**：抖音 Playwright probe 单次 ~5-15s；30s 轮询周期完全装得下两次 detect。
- **测试简单**：mock `PROBE_REGISTRY` + mock `detect()` 即可。

### 4.2 单进程并发（asyncio / threading）

不推荐：

- 我们的 `WindowsAgentService.run()` 是同步阻塞循环；引入 asyncio 要重构 service.py 整个生命周期。
- 收益微小：30s 周期下，「先抖音后 B 站」与「同时跑」对用户感知无差别。
- 增加测试复杂度：异步代码 mock 比同步麻烦。

### 4.3 多进程

不推荐：

- 现有 PowerShell 启动脚本就跑一份 windows-agent-loop.ps1。多进程要么启 N 份脚本（用户配置变复杂），要么父进程 fork（引入 multiprocessing 依赖 + Windows 上 fork 行为坑）。
- 故障隔离单进程串行也能给（try/except 包裹），不必上多进程税。

### 4.4 推荐 + 理由

**采纳 4.1 单进程串行 + try/except 隔离**。后续如果发现 Playwright probe 阻塞另一个平台的探测时机（实测 30s 周期内两个 probe 加起来 > 25s），再升级到 ThreadPoolExecutor —— 这是渐进改造，不是架构革命。

---

## 5. 事件流与 platform 字段

### 5.1 单 jsonl + platform 字段（推荐）

`data/tmp/windows-agent-events.jsonl`：

```jsonl
{"event_type": "live_started", "snapshot": {"platform": "douyin", "state": "live", ...}}
{"event_type": "live_stopped", "snapshot": {"platform": "bilibili", "state": "offline", ...}}
```

orchestrator 读单文件 + filter platform，session key 用 `(platform, room_url)`。

### 5.2 每平台一份 jsonl

`windows-agent-events-douyin.jsonl` / `windows-agent-events-bilibili.jsonl`。

劣势：orchestrator 要 watch 多文件；事件全局时序丢失（多文件合并时序需要再排序）；测试 fixture 数量 ×N。

### 5.3 推荐

**采纳 5.1**。AgentEvent 加 `snapshot.platform: str` 字段；orchestrator 把 `(platform, room_url)` 作为 session key。`AgentStateFile.last_snapshot` 改成 `last_snapshots: dict[str, AgentSnapshot]`（key 是 `<platform>:<room_url>` 字符串）。

---

## 6. 下游影响

### 6.1 orchestrator

- session 维度从 `streamer_name` 升级为 `(platform, room_url)`。
- 状态文件 schema 不变（业务数据本身平台中立），只是 session key 改了。
- 必要的回归测试：抖音单平台路径必须保持现有行为不变（用相同的 room_url、相同的 streamer_name，session lifecycle 保持一致）。

### 6.2 recorder

- ffmpeg 命令构造增加 header 注入：从 `AgentSnapshot.stream_headers`（新字段）取 dict，转成 `-headers "K1: V1\r\nK2: V2"`。
- 抖音 probe 永远返回空 headers → 现有命令行 100% 兼容。
- B 站 probe 返回 `{"Referer": "https://live.bilibili.com", "User-Agent": "..."}`。
- recorder 重试逻辑：B 站流 token 过期时（ffmpeg 报 403），不能直接 retry 同一 URL —— 需要回 probe 重新拿 URL。这个反馈链路目前没有，**MVP 可以接受 retry 失败 → emit `live_stopped` → 等下一轮 probe 自然恢复**，不必硬实现"重新拿 URL"的回调。

---

## 7. 开源参考实现（间接证据）

由于上游 500 + 主代理 WebFetch 不稳，未做源码 raw 抓取。基于社区描述与项目目录结构推断：

- **Bililive-go**（[hr3lxphr6j/bililive-go](https://github.com/hr3lxphr6j/bililive-go)）：Go 实现的多平台直播录制器。Site interface 在 `src/live/live.go`，每个平台一个 package（`src/live/bilibili/`、`src/live/douyu/`、`src/live/huya/` ...）。注册走 `init()` 自注册到全局 map。配置走 yaml。**和我们的目标架构 90% 一致**，是最好的参照。
- **streamlink/streamlink**（[streamlink/streamlink](https://github.com/streamlink/streamlink)）：Python 实现的"site plugin"模式。每个站点一个 plugin 类继承 `Plugin` ABC，注册靠 entry points + 内置 plugin 包扫描。架构对我们 MVP 来说**过设计**，但 plugin ABC 设计可以参考。
- **DDTV**（[CHKZL/DDTV](https://github.com/CHKZL/DDTV)）：C# 实现，B 站特化（不是多平台），但 ffmpeg referer 用法是直接证据。
- **yt-dlp**：extractor 模式，每个站点一个 extractor 类，ABC 是 `InfoExtractor`。同 streamlink，对 MVP 过重。

> 实施 PR 时建议主开发花 30 分钟读 Bililive-go 的 `src/live/live.go` + `src/live/bilibili/bilibili.go`，对照我们的 ABC 与 BilibiliRoomProbe 设计。

---

## 8. 推荐方案（针对本仓库）

### A: 保守渐进（推荐 MVP 第一刀）

- 抽 `PlatformProbe` ABC、`PROBE_REGISTRY` dict、改 `WindowsAgentService` 串行轮询、`AgentSnapshot` 加 `platform` + `stream_headers`、orchestrator 改 session key、recorder 透传 headers。
- 配置仍走 .env，加 `ARL_PLATFORMS` + `ARL_<PLATFORM>_*` 规则。
- **改动文件清单（粗粒度，~10-12 文件）**：
  - `src/arl/config.py`（嵌套 model + load_platforms）
  - `src/arl/shared/contracts.py`（可选加 Platform 枚举）
  - `src/arl/windows_agent/models.py`（AgentSnapshot 加 platform/stream_headers）
  - `src/arl/windows_agent/platform_probe.py`（新增 ABC）
  - `src/arl/windows_agent/registry.py`（新增 dict）
  - `src/arl/windows_agent/probe.py`（DouyinRoomProbe 适配 ABC + 改名维持）
  - `src/arl/windows_agent/bilibili_probe.py`（新增）
  - `src/arl/windows_agent/service.py`（probes list + run_once 遍历）
  - `src/arl/windows_agent/state_store.py`（last_snapshots dict）
  - `src/arl/orchestrator/`（session key 改 (platform, room_url)；估 1-2 文件）
  - `src/arl/recorder/`（ffmpeg header 注入；估 1 文件）
  - `.env.example`、`README.md`、`tests/...`
- **改动量**：中等。1-2 个 PR 可完成（结构化 PR1：抽接口 + 抖音适配；PR2：B 站 probe + recorder header）。
- **优劣**：
  - 优：架构调整最小化；保留所有现有抖音路径；测试覆盖容易；用户 .env 兼容。
  - 劣：env 平台键多了不直观（4 个平台时 .env 会有 ~25 行），但可以接受。
- **推荐度**：⭐⭐⭐⭐⭐

### B: 彻底通用化 + yaml 配置

- A 的所有改动 + 把 .env 平台部分迁到 `config/platforms.yaml`，引入 PyYAML，重构 `_load_dotenv` 流程。
- 改动量：A + 5-8 文件（yaml loader、文档、迁移脚本）。
- 优：表达力最强，未来加平台只动 yaml。
- 劣：第一个 PR 体积大，回归风险高，给 MVP 拖时间。
- **不推荐 MVP 阶段做**，可作为第三个 PR（B 站跑通后再升级配置）。

### C: 折中（A + yaml 留 future hook）

- A 完整实施 + 在 `Settings` 里同时支持 `.env` 和 `platforms.yaml`（yaml 缺失则回 .env），yaml 加载逻辑写但不文档化、不 sample。
- 优：保留升级路径但不给用户增加学习成本。
- 劣：维护两套配置加载代码（轻度技术债）。
- 推荐度：⭐⭐（不必要的复杂度）。

### **采纳 A**

A 是最务实的 MVP 路径。yaml 配置等到平台数 ≥ 3 时再升级。

---

## 9. References

- 仓库现状代码：`src/arl/config.py`、`src/arl/windows_agent/{probe,service,state_store,models}.py`、`src/arl/shared/contracts.py`
- [hr3lxphr6j/bililive-go](https://github.com/hr3lxphr6j/bililive-go) — Go 多平台录制器，site-plugin 架构
- [streamlink/streamlink](https://github.com/streamlink/streamlink) — Python site-plugin 参考
- [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) — InfoExtractor 模式
- [pydantic docs — Nested models](https://docs.pydantic.dev/latest/concepts/models/#nested-models)
- 相关研究文件：[`bilibili-live-detection.md`](./bilibili-live-detection.md)
