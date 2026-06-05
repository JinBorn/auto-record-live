from __future__ import annotations

from arl.shared.contracts import RecordingAsset


UNKNOWN_RECORDING_DURATION_SECONDS = 1800.0
MIN_RECORDING_DURATION_SECONDS = 1.0


def recording_duration_seconds(asset: RecordingAsset) -> float:
    """Return manifest duration, preserving short completed recordings.

    Older assets and currently-live sessions may not carry ``ended_at``. Keep
    the existing 30 minute estimate for that unknown-duration case, but when a
    recorder writes a concrete end timestamp, downstream stages should respect
    it even for short smoke captures.
    """

    if asset.ended_at is None:
        return UNKNOWN_RECORDING_DURATION_SECONDS
    duration = (asset.ended_at - asset.started_at).total_seconds()
    return max(MIN_RECORDING_DURATION_SECONDS, duration)
