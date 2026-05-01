from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from arl.windows_agent.state_store import WindowsAgentStateStore


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


if __name__ == "__main__":
    unittest.main()
