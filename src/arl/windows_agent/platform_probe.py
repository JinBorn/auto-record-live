from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import ClassVar

from arl.windows_agent.models import AgentSnapshot


class CookieState(str, Enum):
    """Authentication cookie health classification for one probe cycle.

    - ``FRESH``: the platform's auth cookie is configured and the latest
      snapshot does not match a cookie-expiration signature.
    - ``EXPIRED``: the cookie is configured and the latest snapshot matches a
      high-confidence cookie-expiration signature (Bilibili ``code=-101`` at
      playinfo; Douyin gate-rejection at the ``_hd`` anonymous baseline).
    - ``NOT_CONFIGURED``: the platform has no auth cookie set, so cookie
      health is not applicable. Probes without cookie support keep this as
      the default to avoid false-positive ``EXPIRED`` events.
    """

    FRESH = "fresh"
    EXPIRED = "expired"
    NOT_CONFIGURED = "not_configured"


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

    ``classify_cookie_state()`` lets a probe expose authentication-cookie
    health on top of the snapshot it just produced. Default returns
    ``NOT_CONFIGURED`` so probes that don't rely on cookie auth never emit a
    cookie-expired event. Cookie-aware subclasses override this to recognize
    their platform-specific expiration signature.
    """

    platform_name: ClassVar[str]

    @abstractmethod
    def detect(self) -> AgentSnapshot:
        """Probe current live state and return a snapshot."""

    def stream_headers(self) -> dict[str, str]:
        return {}

    def classify_cookie_state(self, snapshot: AgentSnapshot) -> CookieState:
        return CookieState.NOT_CONFIGURED
