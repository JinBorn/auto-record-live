from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from arl.config import SubtitleSettings


class SubtitleTextNormalizer:
    def __init__(
        self,
        settings: SubtitleSettings,
        *,
        warn: Callable[[str], None],
    ) -> None:
        self.settings = settings
        self._warn = warn
        self._opencc: Any | None = None
        self._opencc_loaded = False
        self._opencc_warning_emitted = False
        self._term_fixes: dict[str, str] | None = None
        self._term_warning_emitted = False

    def normalize(self, text: str) -> str:
        normalized = self._convert_opencc(text)
        for source, replacement in self._load_term_fixes().items():
            if source:
                normalized = normalized.replace(source, replacement)
        return normalized

    def _convert_opencc(self, text: str) -> str:
        if not self.settings.opencc_enabled:
            return text
        converter = self._load_opencc()
        if converter is None:
            return text
        try:
            return str(converter.convert(text))
        except Exception as exc:
            self._warn_opencc(f"opencc conversion skipped reason={exc}")
            return text

    def _load_opencc(self) -> Any | None:
        if self._opencc_loaded:
            return self._opencc
        self._opencc_loaded = True
        try:
            from opencc import OpenCC  # type: ignore[import-not-found]

            self._opencc = OpenCC("t2s")
        except Exception as exc:
            self._opencc = None
            self._warn_opencc(f"opencc unavailable reason={exc}")
        return self._opencc

    def _warn_opencc(self, message: str) -> None:
        if self._opencc_warning_emitted:
            return
        self._opencc_warning_emitted = True
        self._warn(message)

    def _load_term_fixes(self) -> dict[str, str]:
        if self._term_fixes is not None:
            return self._term_fixes
        path = self.settings.term_fixes_path
        if path is None or not path.exists():
            self._term_fixes = {}
            return self._term_fixes
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._warn_term_fixes(path, f"parse_error:{exc}")
            self._term_fixes = {}
            return self._term_fixes
        if not isinstance(payload, dict):
            self._warn_term_fixes(path, "schema_error:not_object")
            self._term_fixes = {}
            return self._term_fixes

        fixes: dict[str, str] = {}
        for source, replacement in payload.items():
            if isinstance(source, str) and isinstance(replacement, str):
                fixes[source] = replacement
            else:
                self._warn_term_fixes(path, "schema_error:non_string_entry")
                fixes = {}
                break
        self._term_fixes = fixes
        return self._term_fixes

    def _warn_term_fixes(self, path: Path, reason: str) -> None:
        if self._term_warning_emitted:
            return
        self._term_warning_emitted = True
        self._warn(f"term fixes skipped path={path} reason={reason}")
