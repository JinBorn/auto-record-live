from __future__ import annotations

from arl.config import PlatformSettings
from arl.windows_agent.platform_probe import PlatformProbe
from arl.windows_agent.probe import DouyinRoomProbe

PROBE_REGISTRY: dict[str, type[PlatformProbe]] = {
    "douyin": DouyinRoomProbe,
}


class UnknownPlatformError(ValueError):
    pass


def build_probe(platform: PlatformSettings) -> PlatformProbe:
    probe_cls = PROBE_REGISTRY.get(platform.type)
    if probe_cls is None:
        raise UnknownPlatformError(
            f"unknown platform type={platform.type!r}; "
            f"registered={sorted(PROBE_REGISTRY)}"
        )
    return probe_cls(platform)


def build_probes(platforms: list[PlatformSettings]) -> list[PlatformProbe]:
    return [build_probe(p) for p in platforms]
