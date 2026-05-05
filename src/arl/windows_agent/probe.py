from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar
from urllib.parse import unquote

import httpx

from arl.config import DouyinSettings
from arl.shared.contracts import LiveState, SourceType
from arl.windows_agent.models import AgentSnapshot
from arl.windows_agent.platform_probe import PlatformProbe


class DouyinRoomProbe(PlatformProbe):
    platform_name: ClassVar[str] = "douyin"

    _STREAM_KEY_PATTERN = re.compile(
        r'"(?:streamUrl|stream_url|hls_pull_url|flv_pull_url|main_hls|main_flv|origin_hls|origin_flv)"\s*:\s*"([^"]+)"',
        re.IGNORECASE,
    )
    _URL_PATTERN = re.compile(r"https?:\\?\/\\?\/[^\s\"'<>\\]+", re.IGNORECASE)
    _PERCENT_ENCODED_URL_PATTERN = re.compile(
        r"https?%3a%2f%2f[^\s\"'<>\\]+",
        re.IGNORECASE,
    )
    _UNICODE_ESCAPE_PATTERN = re.compile(r"\\u([0-9a-fA-F]{4})")
    _HEX_ESCAPE_PATTERN = re.compile(r"\\x([0-9a-fA-F]{2})")
    _BLOCKED_SUFFIXES = (
        ".js",
        ".css",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".webp",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
    )

    def __init__(self, settings: DouyinSettings) -> None:
        self.settings = settings

    def detect(self) -> AgentSnapshot:
        now = datetime.now(timezone.utc)
        room_url = self.settings.room_url
        streamer_name = self.settings.streamer_name or "unknown-streamer"

        if not room_url:
            return AgentSnapshot(
                state=LiveState.OFFLINE,
                streamer_name=streamer_name,
                room_url=room_url,
                reason="room_url_not_configured",
                detected_at=now,
            )

        forced_state = os.getenv("ARL_AGENT_FORCE_STATE", "").strip().lower()
        if forced_state:
            return self._forced_snapshot(forced_state, room_url, streamer_name, now)

        if self.settings.use_playwright_probe:
            snapshot = self._probe_with_playwright(room_url, streamer_name, now)
            if snapshot is not None and not self._should_fallback_to_http(snapshot):
                return snapshot

        try:
            response = httpx.get(
                room_url,
                timeout=15.0,
                follow_redirects=True,
                headers={
                    "user-agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                },
            )
        except httpx.HTTPError as exc:
            return AgentSnapshot(
                state=LiveState.OFFLINE,
                streamer_name=streamer_name,
                room_url=room_url,
                reason=f"http_error:{exc.__class__.__name__}",
                detected_at=now,
            )

        text = response.text
        stream_url = self._extract_stream_url(text)
        if response.status_code >= 400:
            return AgentSnapshot(
                state=LiveState.OFFLINE,
                streamer_name=streamer_name,
                room_url=room_url,
                reason=f"http_status:{response.status_code}",
                detected_at=now,
            )

        live_markers = [
            '"status":2',
            '"live_status":2',
            '"is_live":true',
            "直播中",
        ]
        if any(marker in text for marker in live_markers):
            return AgentSnapshot(
                state=LiveState.LIVE,
                streamer_name=streamer_name,
                room_url=room_url,
                source_type=SourceType.DIRECT_STREAM if stream_url else SourceType.BROWSER_CAPTURE,
                stream_url=stream_url,
                reason="page_marker_detected",
                detected_at=now,
            )

        offline_markers = [
            '"status":4',
            '"live_status":4',
            "暂未开播",
            "还没开播",
        ]
        if any(marker in text for marker in offline_markers):
            return AgentSnapshot(
                state=LiveState.OFFLINE,
                streamer_name=streamer_name,
                room_url=room_url,
                reason="page_marker_detected",
                detected_at=now,
            )

        if stream_url:
            return AgentSnapshot(
                state=LiveState.LIVE,
                streamer_name=streamer_name,
                room_url=room_url,
                source_type=SourceType.DIRECT_STREAM,
                stream_url=stream_url,
                reason="stream_url_detected_http",
                detected_at=now,
            )

        return AgentSnapshot(
            state=LiveState.OFFLINE,
            streamer_name=streamer_name,
            room_url=room_url,
            reason="live_state_unknown",
            detected_at=now,
        )

    def _probe_with_playwright(
        self,
        room_url: str,
        streamer_name: str,
        now: datetime,
    ) -> AgentSnapshot | None:
        script_path = self.settings.playwright_script
        if not script_path.exists():
            return AgentSnapshot(
                state=LiveState.OFFLINE,
                streamer_name=streamer_name,
                room_url=room_url,
                reason="playwright_script_missing",
                detected_at=now,
            )

        command = [
            "node",
            str(script_path),
            "--room-url",
            room_url,
            "--profile-dir",
            self.settings.persistent_profile_dir,
            "--timeout-ms",
            str(self.settings.playwright_timeout_ms),
            "--headless",
            "0",
        ]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=Path.cwd(),
                timeout=(self.settings.playwright_timeout_ms / 1000) + 10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return AgentSnapshot(
                state=LiveState.OFFLINE,
                streamer_name=streamer_name,
                room_url=room_url,
                reason=f"playwright_exec_error:{exc.__class__.__name__}",
                detected_at=now,
            )

        stdout = (result.stdout or "").strip()
        payload = self._parse_playwright_payload(stdout)

        if payload is None:
            error_detail = (result.stderr or "").strip() or f"returncode:{result.returncode}"
            return AgentSnapshot(
                state=LiveState.OFFLINE,
                streamer_name=streamer_name,
                room_url=room_url,
                reason=f"playwright_error:{error_detail[:160]}",
                detected_at=now,
            )

        if not payload.get("ok", False):
            return AgentSnapshot(
                state=LiveState.OFFLINE,
                streamer_name=streamer_name,
                room_url=room_url,
                reason=f"playwright_error:{payload.get('error', 'unknown')}",
                detected_at=now,
            )

        state_value = payload.get("state", "offline")
        state = LiveState(state_value) if state_value in {"live", "offline"} else LiveState.OFFLINE
        source_type = self._normalize_source_type(
            source_value=payload.get("sourceType"),
            state=state,
            stream_url=payload.get("streamUrl"),
        )
        stream_url_raw = payload.get("streamUrl")
        stream_url = stream_url_raw if isinstance(stream_url_raw, str) else None
        reason_raw = payload.get("reason", "playwright_probe")
        reason = reason_raw if isinstance(reason_raw, str) else "playwright_probe"
        return AgentSnapshot(
            state=state,
            streamer_name=streamer_name,
            room_url=room_url,
            source_type=source_type,
            stream_url=stream_url,
            reason=reason,
            detected_at=now,
        )

    @staticmethod
    def _parse_playwright_payload(stdout: str) -> dict[str, object] | None:
        if not stdout:
            return None

        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        for line in reversed(lines):
            if not (line.startswith("{") and line.endswith("}")):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and "ok" in payload:
                return payload
        return None

    @staticmethod
    def _normalize_source_type(
        source_value: object,
        state: LiveState,
        stream_url: object,
    ) -> SourceType | None:
        source_type = None
        if isinstance(source_value, str) and source_value:
            try:
                source_type = SourceType(source_value)
            except ValueError:
                source_type = None

        has_stream_url = isinstance(stream_url, str) and stream_url.strip().startswith(
            ("http://", "https://")
        )
        if state == LiveState.LIVE:
            if source_type is None:
                return SourceType.DIRECT_STREAM if has_stream_url else SourceType.BROWSER_CAPTURE
            if source_type == SourceType.DIRECT_STREAM and not has_stream_url:
                return SourceType.BROWSER_CAPTURE
        return source_type

    @staticmethod
    def _should_fallback_to_http(snapshot: AgentSnapshot) -> bool:
        reason = snapshot.reason or ""
        return (
            reason == "playwright_script_missing"
            or reason.startswith("playwright_exec_error:")
            or reason.startswith("playwright_error:")
        )

    @classmethod
    def _extract_stream_url(cls, text: str) -> str | None:
        candidates = cls._extract_stream_url_candidates(text)
        if not candidates:
            return None
        return max(candidates, key=cls._stream_url_score)

    @classmethod
    def _extract_stream_url_candidates(cls, text: str) -> set[str]:
        candidates: set[str] = set()
        for match in cls._STREAM_KEY_PATTERN.finditer(text):
            normalized = cls._normalize_stream_url(match.group(1))
            if cls._is_likely_stream_url(normalized):
                candidates.add(normalized)
        for match in cls._URL_PATTERN.finditer(text):
            normalized = cls._normalize_stream_url(match.group(0))
            if cls._is_likely_stream_url(normalized):
                candidates.add(normalized)
        for match in cls._PERCENT_ENCODED_URL_PATTERN.finditer(text):
            normalized = cls._normalize_stream_url(match.group(0))
            if cls._is_likely_stream_url(normalized):
                candidates.add(normalized)
        return candidates

    @classmethod
    def _normalize_stream_url(cls, raw_value: str) -> str:
        normalized = cls._UNICODE_ESCAPE_PATTERN.sub(
            lambda match: chr(int(match.group(1), 16)),
            raw_value,
        )
        normalized = cls._HEX_ESCAPE_PATTERN.sub(
            lambda match: chr(int(match.group(1), 16)),
            normalized,
        )
        normalized = (
            normalized
            .replace("\\/", "/")
            .replace("&amp;", "&")
            .strip()
        )
        for _ in range(3):
            lowered = normalized.lower()
            if not re.match(r"^https?%[0-9a-f]{2}", lowered):
                break
            decoded = unquote(normalized)
            if decoded == normalized:
                break
            normalized = decoded
        return normalized

    @classmethod
    def _is_likely_stream_url(cls, raw_url: str) -> bool:
        lower = raw_url.lower()
        if not (lower.startswith("https://") or lower.startswith("http://")):
            return False
        no_query = lower.split("?")[0]
        if any(no_query.endswith(suffix) for suffix in cls._BLOCKED_SUFFIXES):
            return False
        if ".m3u8" in lower or ".flv" in lower:
            return True
        return "pull" in lower and ("stream" in lower or "live" in lower)

    @staticmethod
    def _stream_url_score(url: str) -> int:
        lower = url.lower()
        score = 0
        if ".m3u8" in lower:
            score += 50
        if ".flv" in lower:
            score += 40
        if "hls" in lower:
            score += 10
        if "pull" in lower:
            score += 8
        if "stream" in lower:
            score += 6
        if "live" in lower:
            score += 4
        return score

    def _forced_snapshot(
        self,
        forced_state: str,
        room_url: str,
        streamer_name: str,
        now: datetime,
    ) -> AgentSnapshot:
        if forced_state == "live":
            stream_url = os.getenv("ARL_AGENT_FORCE_STREAM_URL") or None
            source_type = (
                SourceType.DIRECT_STREAM if stream_url else SourceType.BROWSER_CAPTURE
            )
            return AgentSnapshot(
                state=LiveState.LIVE,
                streamer_name=streamer_name,
                room_url=room_url,
                source_type=source_type,
                stream_url=stream_url,
                reason="forced_state",
                detected_at=now,
            )

        return AgentSnapshot(
            state=LiveState.OFFLINE,
            streamer_name=streamer_name,
            room_url=room_url,
            reason="forced_state",
            detected_at=now,
        )
