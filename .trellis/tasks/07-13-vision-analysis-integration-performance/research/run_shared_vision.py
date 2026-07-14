from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from arl.config import load_settings
from arl.vision_analysis.detectors import RefinementRequest
from arl.vision_analysis.service import VisionAnalysisService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_id")
    parser.add_argument("--force-reprocess", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    service = VisionAnalysisService(load_settings())
    original_merge = service._merge_refinement_requests
    refinement_details: dict[str, object] = {}

    def capture_merge(
        requests: list[RefinementRequest],
        *,
        duration: float,
    ) -> list[tuple[float, float, set[str]]]:
        merged = original_merge(requests, duration=duration)
        candidate_counts = Counter(request.detector for request in requests)
        candidate_seconds: defaultdict[str, float] = defaultdict(float)
        for request in requests:
            candidate_seconds[request.detector] += max(
                0.0,
                min(duration, request.ended_at_seconds)
                - max(0.0, request.started_at_seconds),
            )
        merged_seconds: defaultdict[str, float] = defaultdict(float)
        for start, end, detectors in merged:
            for detector in detectors:
                merged_seconds[detector] += end - start
        refinement_details.update(
            {
                "candidate_count_by_detector": dict(sorted(candidate_counts.items())),
                "candidate_seconds_by_detector": {
                    key: round(value, 3)
                    for key, value in sorted(candidate_seconds.items())
                },
                "merged_seconds_by_detector": {
                    key: round(value, 3)
                    for key, value in sorted(merged_seconds.items())
                },
                "merged_ranges": [
                    {
                        "started_at_seconds": round(start, 3),
                        "ended_at_seconds": round(end, 3),
                        "detectors": sorted(detectors),
                    }
                    for start, end, detectors in merged
                ],
            }
        )
        return merged

    service._merge_refinement_requests = capture_merge
    assets = service.run(
        session_ids={args.session_id},
        force_reprocess=args.force_reprocess,
    )
    if len(assets) != 1:
        raise SystemExit(f"expected one asset, got {len(assets)}")
    asset = assets[0]
    event_counts = Counter(event.kind for event in asset.events)
    payload = {
        "session_id": asset.session_id,
        "schema_version": asset.schema_version,
        "status": asset.status,
        "source_duration_seconds": asset.source_duration_seconds,
        "metrics": asset.metrics.model_dump(),
        "detector_health": [item.model_dump() for item in asset.detector_health],
        "event_count_by_kind": dict(sorted(event_counts.items())),
        "events": [
            {
                "kind": event.kind,
                "observed_at_seconds": event.observed_at_seconds,
                "ended_at_seconds": event.ended_at_seconds,
                "attributes": event.attributes,
            }
            for event in asset.events
        ],
        "refinement": refinement_details,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
