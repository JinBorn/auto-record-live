from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import ClassVar

import httpx

from arl.config import BilibiliSettings
from arl.shared.contracts import LiveState, SourceType
from arl.windows_agent.models import AgentSnapshot
from arl.windows_agent.platform_probe import PlatformProbe


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_REFERER = "https://live.bilibili.com"


class BilibiliRoomProbe(PlatformProbe):
    """Bilibili live-room probe using anonymous HTTP API.

    Per research/bilibili-live-detection.md: get_info gives the live status
    (1=LIVE, 0/2=OFFLINE — 2 is the carousel replay mode), getRoomPlayInfo
    returns the FLV/HLS pull URL with a short-lived token. Both endpoints
    work anonymously without WBI signing or SESSDATA cookie. ffmpeg pulling
    the returned URL strictly requires the Referer header, which we surface
    via stream_headers() so the recorder can forward it.
    """

    platform_name: ClassVar[str] = "bilibili"

    _STATUS_ENDPOINT = "https://api.live.bilibili.com/room/v1/Room/get_info"
    _PLAYINFO_ENDPOINT = (
        "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo"
    )
    _ROOM_ID_PATTERN = re.compile(r"live\.bilibili\.com/(\d+)")
    _HTTP_TIMEOUT_SECONDS = 10.0

    def __init__(self, settings: BilibiliSettings) -> None:
        self.settings = settings

    def stream_headers(self) -> dict[str, str]:
        return {"Referer": _REFERER, "User-Agent": _USER_AGENT}

    def detect(self) -> AgentSnapshot:
        now = datetime.now(timezone.utc)
        room_url = self.settings.room_url
        streamer_name = self.settings.streamer_name or "unknown-streamer"

        if not room_url:
            return self._offline(
                room_url=room_url,
                streamer_name=streamer_name,
                reason="room_url_not_configured",
                now=now,
            )

        room_id = self._extract_room_id(room_url)
        if room_id is None:
            return self._offline(
                room_url=room_url,
                streamer_name=streamer_name,
                reason="room_id_not_parsed",
                now=now,
            )

        try:
            status_payload = self._fetch_json(
                self._STATUS_ENDPOINT,
                params={"room_id": room_id, "from": "room"},
            )
        except httpx.HTTPError as exc:
            return self._offline(
                room_url=room_url,
                streamer_name=streamer_name,
                reason=f"http_error:{exc.__class__.__name__}",
                now=now,
            )
        except ValueError as exc:
            # Raised by _fetch_json on JSON decode / non-200 / B 站 negative code.
            return self._offline(
                room_url=room_url,
                streamer_name=streamer_name,
                reason=str(exc),
                now=now,
            )

        live_status = self._extract_live_status(status_payload)
        if live_status is None:
            return self._offline(
                room_url=room_url,
                streamer_name=streamer_name,
                reason="live_status_missing",
                now=now,
            )
        if live_status == 2:
            return self._offline(
                room_url=room_url,
                streamer_name=streamer_name,
                reason="carousel_playback",
                now=now,
            )
        if live_status != 1:
            return self._offline(
                room_url=room_url,
                streamer_name=streamer_name,
                reason="not_live",
                now=now,
            )

        try:
            playinfo_payload = self._fetch_json(
                self._PLAYINFO_ENDPOINT,
                params={
                    "room_id": room_id,
                    "protocol": "0,1",
                    "format": "0,1,2",
                    "codec": "0,1",
                    "qn": "10000",
                },
            )
        except httpx.HTTPError as exc:
            return AgentSnapshot(
                state=LiveState.LIVE,
                streamer_name=streamer_name,
                room_url=room_url,
                source_type=SourceType.BROWSER_CAPTURE,
                stream_url=None,
                stream_headers=self.stream_headers(),
                reason=f"playinfo_http_error:{exc.__class__.__name__}",
                detected_at=now,
                platform=self.platform_name,
            )
        except ValueError as exc:
            return AgentSnapshot(
                state=LiveState.LIVE,
                streamer_name=streamer_name,
                room_url=room_url,
                source_type=SourceType.BROWSER_CAPTURE,
                stream_url=None,
                stream_headers=self.stream_headers(),
                reason=f"playinfo_error:{exc}",
                detected_at=now,
                platform=self.platform_name,
            )

        stream_url = self._extract_stream_url(playinfo_payload)
        if stream_url is None:
            return AgentSnapshot(
                state=LiveState.LIVE,
                streamer_name=streamer_name,
                room_url=room_url,
                source_type=SourceType.BROWSER_CAPTURE,
                stream_url=None,
                stream_headers=self.stream_headers(),
                reason="stream_url_missing",
                detected_at=now,
                platform=self.platform_name,
            )

        return AgentSnapshot(
            state=LiveState.LIVE,
            streamer_name=streamer_name,
            room_url=room_url,
            source_type=SourceType.DIRECT_STREAM,
            stream_url=stream_url,
            stream_headers=self.stream_headers(),
            reason="api_live_with_stream_url",
            detected_at=now,
            platform=self.platform_name,
        )

    def _offline(
        self,
        *,
        room_url: str,
        streamer_name: str,
        reason: str,
        now: datetime,
    ) -> AgentSnapshot:
        return AgentSnapshot(
            state=LiveState.OFFLINE,
            streamer_name=streamer_name,
            room_url=room_url,
            reason=reason,
            detected_at=now,
            platform=self.platform_name,
        )

    @classmethod
    def _extract_room_id(cls, room_url: str) -> str | None:
        match = cls._ROOM_ID_PATTERN.search(room_url)
        if match is None:
            return None
        return match.group(1)

    def _fetch_json(self, url: str, *, params: dict[str, str]) -> dict[str, object]:
        response = httpx.get(
            url,
            params=params,
            headers={"User-Agent": _USER_AGENT, "Referer": _REFERER},
            timeout=self._HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        if response.status_code >= 400:
            raise ValueError(f"http_status:{response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError(f"json_decode_error:{exc.__class__.__name__}") from exc

        if not isinstance(payload, dict):
            raise ValueError("payload_not_object")

        code = payload.get("code")
        if isinstance(code, int) and code != 0:
            message_raw = payload.get("message", "")
            message = (message_raw or "").strip() if isinstance(message_raw, str) else ""
            detail = message[:80] if message else "no_message"
            raise ValueError(f"api_error:code={code}:{detail}")

        return payload

    @staticmethod
    def _extract_live_status(payload: dict[str, object]) -> int | None:
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        live_status = data.get("live_status")
        if isinstance(live_status, bool):
            # bool is a subclass of int — guard so True/False don't sneak through.
            return None
        if isinstance(live_status, int):
            return live_status
        return None

    @classmethod
    def _extract_stream_url(cls, payload: dict[str, object]) -> str | None:
        """Drill into data.playurl_info.playurl.stream[].format[].codec[].url_info[].

        Returns the first URL composed by joining url_info.host + base_url +
        url_info.extra (the extra carries the time-limited token). Bilibili's
        URL is split into three pieces so callers can deduplicate the host;
        we just glue them back together.
        """
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        playurl_info = data.get("playurl_info")
        if not isinstance(playurl_info, dict):
            return None
        playurl = playurl_info.get("playurl")
        if not isinstance(playurl, dict):
            return None
        streams = playurl.get("stream")
        if not isinstance(streams, list):
            return None
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            formats = stream.get("format")
            if not isinstance(formats, list):
                continue
            for format_entry in formats:
                if not isinstance(format_entry, dict):
                    continue
                codecs = format_entry.get("codec")
                if not isinstance(codecs, list):
                    continue
                for codec_entry in codecs:
                    if not isinstance(codec_entry, dict):
                        continue
                    base_url = codec_entry.get("base_url")
                    if not isinstance(base_url, str) or not base_url:
                        continue
                    url_infos = codec_entry.get("url_info")
                    if not isinstance(url_infos, list):
                        continue
                    for url_info in url_infos:
                        if not isinstance(url_info, dict):
                            continue
                        host = url_info.get("host")
                        extra = url_info.get("extra", "")
                        if not isinstance(host, str) or not host:
                            continue
                        if not isinstance(extra, str):
                            extra = ""
                        return f"{host}{base_url}{extra}"
        return None
