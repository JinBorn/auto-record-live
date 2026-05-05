from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from arl.windows_agent.models import AgentSnapshot


class PlatformProbe(ABC):
    """Single-room live-state probe for one platform.

    Each subclass owns its platform's status detection (HTTP API, browser
    automation, etc.). The probe is bound to one room at construction time and
    returns one ``AgentSnapshot`` per ``detect()`` call. Snapshots must carry
    ``platform == cls.platform_name`` so downstream stages can route per
    platform.

    ``stream_headers()`` lets a probe attach platform-specific HTTP headers
    (for example Bilibili requires ``Referer: https://live.bilibili.com``) that
    the recorder must forward to ffmpeg. Default returns an empty dict so
    platforms with no header requirements (Douyin) keep the existing recorder
    behavior unchanged.
    """

    platform_name: ClassVar[str]

    @abstractmethod
    def detect(self) -> AgentSnapshot:
        """Probe current live state and return a snapshot."""

    def stream_headers(self) -> dict[str, str]:
        return {}
