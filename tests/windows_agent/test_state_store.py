from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.shared.contracts import LiveState, SourceType
from arl.windows_agent.models import AgentSnapshot, AgentStateFile
from arl.windows_agent.state_store import WindowsAgentStateStore


def _make_snapshot(
    *,
    platform: str = "douyin",
    room_url: str = "https://live.douyin.com/123",
    streamer_name: str = "streamer-a",
    state: LiveState = LiveState.LIVE,
) -> AgentSnapshot:
    return AgentSnapshot(
        state=state,
        streamer_name=streamer_name,
        room_url=room_url,
        source_type=SourceType.DIRECT_STREAM if state == LiveState.LIVE else None,
        stream_url=("https://cdn.example/live.m3u8" if state == LiveState.LIVE else None),
        reason="page_marker_detected",
        detected_at=datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc),
        platform=platform,
    )


class WindowsAgentStateStoreTests(unittest.TestCase):
    def test_load_returns_default_state_when_state_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "agent-state.json"
            event_log_path = root / "windows-agent-events.jsonl"
            state_path.write_text("", encoding="utf-8")

            store = WindowsAgentStateStore(state_path=state_path, event_log_path=event_log_path)
            state = store.load()

            self.assertIsNone(state.last_snapshot)
            self.assertEqual(state.last_snapshots, {})

    def test_round_trip_preserves_multi_platform_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "agent-state.json"
            event_log_path = root / "windows-agent-events.jsonl"
            store = WindowsAgentStateStore(state_path=state_path, event_log_path=event_log_path)

            douyin_snapshot = _make_snapshot(platform="douyin")
            bilibili_snapshot = _make_snapshot(
                platform="bilibili",
                room_url="https://live.bilibili.com/456",
                streamer_name="streamer-b",
            )

            state = AgentStateFile()
            state.set(douyin_snapshot)
            state.set(bilibili_snapshot)
            store.save(state)

            reloaded = store.load()
            self.assertEqual(
                set(reloaded.last_snapshots.keys()),
                {
                    "douyin:https://live.douyin.com/123",
                    "bilibili:https://live.bilibili.com/456",
                },
            )
            self.assertEqual(
                reloaded.get("douyin", "https://live.douyin.com/123"),
                douyin_snapshot,
            )
            self.assertEqual(
                reloaded.get("bilibili", "https://live.bilibili.com/456"),
                bilibili_snapshot,
            )

    def test_legacy_single_snapshot_state_file_migrates_to_dict_on_load(self) -> None:
        """A state file written by the pre-PR1 single-platform code (with the
        legacy ``last_snapshot`` singular key) must load successfully, get
        migrated into the new ``last_snapshots`` dict, and never re-emit the
        legacy key on subsequent saves.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "agent-state.json"
            event_log_path = root / "windows-agent-events.jsonl"

            legacy_snapshot = _make_snapshot()
            legacy_payload = {
                "last_snapshot": json.loads(legacy_snapshot.model_dump_json()),
            }
            state_path.write_text(
                json.dumps(legacy_payload, ensure_ascii=False),
                encoding="utf-8",
            )

            store = WindowsAgentStateStore(state_path=state_path, event_log_path=event_log_path)
            state = store.load()

            # Migration: legacy snapshot is hoisted into the dict and the
            # singular field is cleared.
            self.assertIsNone(state.last_snapshot)
            self.assertEqual(
                set(state.last_snapshots.keys()),
                {"douyin:https://live.douyin.com/123"},
            )
            self.assertEqual(
                state.get("douyin", "https://live.douyin.com/123"),
                legacy_snapshot,
            )

            # Saving the migrated state must not re-emit the legacy key.
            store.save(state)
            on_disk = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertNotIn("last_snapshot", on_disk)
            self.assertIn("last_snapshots", on_disk)
            self.assertIn(
                "douyin:https://live.douyin.com/123",
                on_disk["last_snapshots"],
            )

    def test_set_overwrites_existing_snapshot_for_same_platform_and_room(self) -> None:
        state = AgentStateFile()
        first = _make_snapshot(state=LiveState.OFFLINE)
        second = _make_snapshot(state=LiveState.LIVE)
        state.set(first)
        state.set(second)

        self.assertEqual(len(state.last_snapshots), 1)
        self.assertEqual(
            state.get("douyin", "https://live.douyin.com/123"),
            second,
        )


if __name__ == "__main__":
    unittest.main()
