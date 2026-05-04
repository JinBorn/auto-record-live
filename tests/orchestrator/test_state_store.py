from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arl.orchestrator.models import (
    OrchestratorStateFile,
    SessionRecord,
    SessionStatus,
)
from arl.orchestrator.state_store import OrchestratorStateStore
from arl.shared.contracts import SourceType


def _build_state_with_chinese_streamer() -> OrchestratorStateFile:
    state = OrchestratorStateFile(cursor_offset=5182)
    state.sessions.append(
        SessionRecord(
            session_id="session-20260504080021-36adcb4b",
            streamer_name="小风疯头",
            room_url="https://live.douyin.com/856014093738",
            source_type=SourceType.DIRECT_STREAM,
            stream_url="https://example.com/stream.m3u8",
            status=SessionStatus.LIVE,
            started_at=datetime(2026, 5, 4, 8, 0, 21, tzinfo=timezone.utc),
        )
    )
    state.active_session_id = state.sessions[0].session_id
    return state


class OrchestratorStateStoreTests(unittest.TestCase):
    def test_load_returns_default_when_state_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = OrchestratorStateStore(
                state_path=root / "missing.json",
                audit_log_path=root / "audit.jsonl",
            )

            state = store.load()

            self.assertEqual(state.cursor_offset, 0)
            self.assertEqual(state.sessions, [])

    def test_load_returns_default_when_state_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "orchestrator-state.json"
            state_path.write_text("", encoding="utf-8")
            store = OrchestratorStateStore(
                state_path=state_path,
                audit_log_path=root / "audit.jsonl",
            )

            state = store.load()

            self.assertEqual(state.cursor_offset, 0)
            self.assertEqual(state.sessions, [])

    def test_save_writes_utf8_and_load_round_trips_chinese_streamer_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "orchestrator-state.json"
            store = OrchestratorStateStore(
                state_path=state_path,
                audit_log_path=root / "audit.jsonl",
            )

            store.save(_build_state_with_chinese_streamer())

            # File must be valid UTF-8 regardless of platform locale.
            decoded = state_path.read_bytes().decode("utf-8")
            self.assertIn("小风疯头", decoded)

            loaded = store.load()
            self.assertEqual(loaded.cursor_offset, 5182)
            self.assertEqual(len(loaded.sessions), 1)
            self.assertEqual(loaded.sessions[0].streamer_name, "小风疯头")

    def test_load_auto_heals_legacy_gbk_encoded_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "orchestrator-state.json"

            # Simulate a state file written by the previous bare ``Path.write_text``
            # call on Windows zh-CN, where the platform locale is CP936/GBK.
            legacy_state = _build_state_with_chinese_streamer()
            legacy_payload = legacy_state.model_dump_json(indent=2) + "\n"
            state_path.write_bytes(legacy_payload.encode("gbk"))

            store = OrchestratorStateStore(
                state_path=state_path,
                audit_log_path=root / "audit.jsonl",
            )

            loaded = store.load()

            self.assertEqual(loaded.cursor_offset, 5182)
            self.assertEqual(loaded.sessions[0].streamer_name, "小风疯头")

            # Next save must rewrite the file as UTF-8 so the GBK fallback is
            # exercised at most once per legacy file.
            store.save(loaded)
            self.assertEqual(
                state_path.read_bytes().decode("utf-8").count("小风疯头"),
                1,
            )

    def test_load_raises_when_payload_is_neither_utf8_nor_gbk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "orchestrator-state.json"
            # 0xff/0xfe bytes are invalid in both UTF-8 and GBK first-byte ranges.
            state_path.write_bytes(b"\xff\xfe\xff\xfe corrupted payload")

            store = OrchestratorStateStore(
                state_path=state_path,
                audit_log_path=root / "audit.jsonl",
            )

            with self.assertRaises(RuntimeError) as ctx:
                store.load()
            self.assertIn(str(state_path), str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
