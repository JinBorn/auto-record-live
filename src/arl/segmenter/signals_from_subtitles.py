from __future__ import annotations

from pathlib import Path

from arl.config import Settings
from arl.segmenter.models import MatchStageSignal, StageSignalIngestStateFile
from arl.segmenter.stage_text import classify_stage_from_text, load_stage_keywords
from arl.shared.contracts import MatchStage, SubtitleAsset
from arl.shared.jsonl_store import append_model, load_models
from arl.shared.logging import log


class StageSignalFromSubtitlesService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.subtitle_assets_path = settings.storage.temp_dir / "subtitle-assets.jsonl"
        self.signals_path = settings.storage.temp_dir / "match-stage-signals.jsonl"
        self.state_path = settings.storage.temp_dir / "stage-signal-ingest-state.json"
        self.stage_keywords = load_stage_keywords(
            settings.segmenter.stage_keywords_path,
            component="segmenter",
        )
        self._stage_order = {
            MatchStage.CHAMPION_SELECT: 0,
            MatchStage.LOADING: 1,
            MatchStage.IN_GAME: 2,
            MatchStage.POST_GAME: 3,
            MatchStage.UNKNOWN: 4,
        }

    def run(
        self,
        *,
        force_reprocess: bool = False,
        session_ids: set[str] | None = None,
        subtitle_paths: set[Path] | None = None,
        match_indices: set[int] | None = None,
    ) -> None:
        log("segmenter", "stage-signals-from-subtitles starting")
        subtitle_assets = load_models(self.subtitle_assets_path, SubtitleAsset)
        state = self._load_state()
        self._compact_state(state, subtitle_assets)
        filtered_assets = self._filter_subtitle_assets(
            subtitle_assets,
            session_ids=session_ids,
            subtitle_paths=subtitle_paths,
            match_indices=match_indices,
        )
        if (
            session_ids is not None
            or subtitle_paths is not None
            or match_indices is not None
        ):
            log(
                "segmenter",
                (
                    "stage-signals-from-subtitles filter summary "
                    f"total_assets={len(subtitle_assets)} "
                    f"matched_assets={len(filtered_assets)}"
                ),
            )
        if not filtered_assets and (
            session_ids is not None
            or subtitle_paths is not None
            or match_indices is not None
        ):
            session_filter = (
                ",".join(sorted(session_ids)) if session_ids is not None else "-"
            )
            path_filter = (
                ",".join(
                    sorted(
                        self._normalize_path(path)
                        for path in subtitle_paths
                    )
                )
                if subtitle_paths is not None
                else "-"
            )
            match_index_filter = (
                ",".join(str(item) for item in sorted(match_indices))
                if match_indices is not None
                else "-"
            )
            log(
                "segmenter",
                (
                    "stage-signals-from-subtitles no assets matched filters "
                    f"session_ids={session_filter} subtitle_paths={path_filter} "
                    f"match_indices={match_index_filter}"
                ),
            )
            self._save_state(state)
            log(
                "segmenter",
                (
                    "stage-signals-from-subtitles "
                    "processed_subtitles=0 emitted_signals=0 matched_assets=0 "
                    "skipped_already_processed=0 skipped_missing_subtitle=0"
                ),
            )
            return

        matched_assets = len(filtered_assets)
        processed = 0
        emitted = 0
        skipped_already_processed = 0
        skipped_missing_subtitle = 0
        for asset in filtered_assets:
            key = self._key(asset)
            already_processed = key in state.processed_subtitle_keys
            if already_processed and not force_reprocess:
                skipped_already_processed += 1
                continue

            subtitle_path = Path(asset.path)
            if not subtitle_path.exists():
                skipped_missing_subtitle += 1
                log(
                    "segmenter",
                    (
                        "stage-signals-from-subtitles skip "
                        f"session_id={asset.session_id} match_index={asset.match_index} "
                        "reason=subtitle_missing"
                    ),
                )
                continue

            cues = self._parse_srt_cues(subtitle_path)
            signals = self._build_signals(asset.session_id, cues)
            existing_fingerprints = set(
                state.emitted_signal_fingerprints_by_subtitle_key.get(key, [])
            )
            emitted_fingerprints = set(existing_fingerprints)
            new_signals = 0
            for signal in signals:
                fingerprint = self._fingerprint(signal)
                if fingerprint in existing_fingerprints:
                    continue
                append_model(self.signals_path, signal)
                emitted += 1
                new_signals += 1
                emitted_fingerprints.add(fingerprint)

            if key not in state.processed_subtitle_keys:
                state.processed_subtitle_keys.append(key)
            if emitted_fingerprints:
                state.emitted_signal_fingerprints_by_subtitle_key[key] = sorted(
                    emitted_fingerprints
                )
            elif key in state.emitted_signal_fingerprints_by_subtitle_key:
                del state.emitted_signal_fingerprints_by_subtitle_key[key]
            processed += 1
            log(
                "segmenter",
                (
                    "stage-signals-from-subtitles processed "
                    f"session_id={asset.session_id} match_index={asset.match_index} "
                    f"signals={new_signals}"
                    + (" force_reprocess=1" if force_reprocess else "")
                ),
            )

        self._save_state(state)
        log(
            "segmenter",
            (
                "stage-signals-from-subtitles "
                f"processed_subtitles={processed} emitted_signals={emitted} "
                f"matched_assets={matched_assets} "
                f"skipped_already_processed={skipped_already_processed} "
                f"skipped_missing_subtitle={skipped_missing_subtitle}"
            ),
        )

    def _parse_srt_cues(self, path: Path) -> list[tuple[float, str]]:
        lines = path.read_text(encoding="utf-8").splitlines()
        cues: list[tuple[float, str]] = []
        index = 0
        while index < len(lines):
            line = lines[index].strip()
            if "-->" not in line:
                index += 1
                continue

            start_raw = line.split("-->", 1)[0].strip()
            start_seconds = self._parse_srt_timestamp(start_raw)
            if start_seconds is None:
                index += 1
                continue

            index += 1
            text_rows: list[str] = []
            while index < len(lines) and lines[index].strip():
                text_rows.append(lines[index].strip())
                index += 1
            text = " ".join(text_rows).strip()
            if text:
                cues.append((start_seconds, text))
            index += 1
        return cues

    def _build_signals(
        self,
        session_id: str,
        cues: list[tuple[float, str]],
    ) -> list[MatchStageSignal]:
        first_by_stage: dict[MatchStage, tuple[float, str]] = {}
        for at_seconds, text in cues:
            stage = classify_stage_from_text(text, self.stage_keywords)
            if stage is None:
                continue
            if stage not in first_by_stage:
                first_by_stage[stage] = (at_seconds, text)

        ordered = sorted(
            first_by_stage.items(),
            key=lambda item: (item[1][0], self._stage_order[item[0]]),
        )
        return [
            MatchStageSignal(
                session_id=session_id,
                text=text,
                source="subtitles_srt",
                at_seconds=round(at_seconds, 3),
            )
            for stage, (at_seconds, text) in ordered
        ]

    def _parse_srt_timestamp(self, raw: str) -> float | None:
        try:
            timestamp = raw.strip()
            separator = "," if "," in timestamp else "."
            hhmmss, millis = timestamp.split(separator, 1)
            hours, minutes, seconds = hhmmss.split(":", 2)
            total = (
                int(hours) * 3600
                + int(minutes) * 60
                + int(seconds)
                + int(millis) / 1000.0
            )
            return max(0.0, total)
        except ValueError:
            return None

    def _load_state(self) -> StageSignalIngestStateFile:
        if not self.state_path.exists():
            return StageSignalIngestStateFile()
        return StageSignalIngestStateFile.model_validate_json(
            self.state_path.read_text(encoding="utf-8")
        )

    def _save_state(self, state: StageSignalIngestStateFile) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def _compact_state(
        self,
        state: StageSignalIngestStateFile,
        subtitle_assets: list[SubtitleAsset],
    ) -> None:
        valid_keys = {self._key(asset) for asset in subtitle_assets}
        before_processed = len(state.processed_subtitle_keys)
        before_fingerprint_keys = len(state.emitted_signal_fingerprints_by_subtitle_key)
        before_fingerprint_rows = sum(
            len(rows)
            for rows in state.emitted_signal_fingerprints_by_subtitle_key.values()
        )

        state.processed_subtitle_keys = [
            key for key in state.processed_subtitle_keys if key in valid_keys
        ]
        compacted_map: dict[str, list[str]] = {}
        for key, fingerprints in state.emitted_signal_fingerprints_by_subtitle_key.items():
            if key not in valid_keys:
                continue
            deduped = sorted({item for item in fingerprints if item})
            if deduped:
                compacted_map[key] = deduped
        state.emitted_signal_fingerprints_by_subtitle_key = compacted_map

        after_processed = len(state.processed_subtitle_keys)
        after_fingerprint_keys = len(state.emitted_signal_fingerprints_by_subtitle_key)
        after_fingerprint_rows = sum(
            len(rows)
            for rows in state.emitted_signal_fingerprints_by_subtitle_key.values()
        )
        if (
            before_processed != after_processed
            or before_fingerprint_keys != after_fingerprint_keys
            or before_fingerprint_rows != after_fingerprint_rows
        ):
            log(
                "segmenter",
                (
                    "stage-signals-from-subtitles compacted ingest state "
                    f"processed_keys={before_processed}->{after_processed} "
                    f"fingerprint_keys={before_fingerprint_keys}->{after_fingerprint_keys} "
                    f"fingerprints={before_fingerprint_rows}->{after_fingerprint_rows}"
                ),
            )

    def _filter_subtitle_assets(
        self,
        subtitle_assets: list[SubtitleAsset],
        *,
        session_ids: set[str] | None,
        subtitle_paths: set[Path] | None,
        match_indices: set[int] | None,
    ) -> list[SubtitleAsset]:
        if session_ids is None and subtitle_paths is None and match_indices is None:
            return subtitle_assets

        normalized_paths: set[str] | None = None
        if subtitle_paths is not None:
            normalized_paths = {self._normalize_path(path) for path in subtitle_paths}

        filtered: list[SubtitleAsset] = []
        for asset in subtitle_assets:
            if session_ids is not None and asset.session_id not in session_ids:
                continue
            if normalized_paths is not None:
                if self._normalize_path(asset.path) not in normalized_paths:
                    continue
            if match_indices is not None and asset.match_index not in match_indices:
                continue
            filtered.append(asset)
        return filtered

    def _key(self, asset: SubtitleAsset) -> str:
        return f"{asset.session_id}:{asset.match_index}:{asset.path}"

    def _fingerprint(self, signal: MatchStageSignal) -> str:
        at_seconds = "" if signal.at_seconds is None else f"{signal.at_seconds:.3f}"
        detected_at = "" if signal.detected_at is None else signal.detected_at.isoformat()
        return "|".join(
            [
                signal.session_id,
                signal.source,
                at_seconds,
                detected_at,
                signal.text.strip(),
            ]
        )

    def _normalize_path(self, path: str | Path) -> str:
        return Path(path).expanduser().resolve(strict=False).as_posix()
