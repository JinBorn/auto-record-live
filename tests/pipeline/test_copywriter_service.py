from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from arl.config import OrchestratorSettings, Settings, StorageSettings
from arl.copywriter.cover import _draw_cover_text, render_cover
from arl.copywriter.llm import LlmProviderResponse
from arl.copywriter.models import (
    CopywriterSemanticAsset,
    CopywriterStateFile,
    PublishingPackage,
)
from arl.copywriter.service import CopywriterService
from arl.orchestrator.models import (
    OrchestratorStateFile,
    SessionRecord,
    SessionStatus,
)
from arl.shared.contracts import (
    CopyAsset,
    ExportAsset,
    HighlightClipWindow,
    HighlightPlanAsset,
    MatchBoundary,
    RecordingAsset,
    SourceType,
    SubtitleAsset,
)
from arl.shared.jsonl_store import append_model, load_models


class CopywriterServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.temp_root = self.root / "tmp"
        self.processed_root = self.root / "processed"
        self.export_root = self.root / "exports"
        self.settings = Settings(
            storage=StorageSettings(
                raw_dir=self.root / "raw",
                processed_dir=self.processed_root,
                export_dir=self.export_root,
                temp_dir=self.temp_root,
            ),
            orchestrator=OrchestratorSettings(
                state_file=self.temp_root / "orchestrator-state.json",
            ),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_generates_copy_asset_from_subtitle_and_export(self) -> None:
        session_id = "session-copywriter-001"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:00,000 --> 00:00:02,000\n你怎么出现这种装备跟在里?\n\n"
            "2\n00:00:02,000 --> 00:00:04,000\n刚才你们冒出这种装备\n",
        )
        export_path = self._write_export(session_id)
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        append_model(
            self.temp_root / "export-assets.jsonl",
            ExportAsset(
                session_id=session_id,
                match_index=1,
                path=str(export_path),
                subtitle_path=str(subtitle_path),
                created_at=self._now(),
            ),
        )

        CopywriterService(self.settings).run()

        copy_assets = load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)
        self.assertEqual(len(copy_assets), 1)
        asset = copy_assets[0]
        self.assertEqual(asset.session_id, session_id)
        self.assertEqual(asset.match_index, 1)
        self.assertEqual(asset.export_path, str(export_path))
        self.assertIn("装备", asset.title)
        self.assertIn("装备选择", asset.tags)

        output_path = Path(asset.path)
        self.assertTrue(output_path.exists())
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["recommended_title"], asset.title)
        self.assertEqual(
            payload["transcript_excerpt"],
            ["你怎么出现这种装备跟在里?", "刚才你们冒出这种装备"],
        )
        packages = load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)
        self.assertEqual(len(packages), 1)
        package = packages[0]
        self.assertEqual(package.session_id, session_id)
        self.assertEqual(package.match_index, 1)
        self.assertEqual(package.recommended_title, asset.title)
        self.assertIn("summary", json.loads(Path(package.path or "").read_text(encoding="utf-8")))
        self.assertEqual(package.cover_path, None)
        self._assert_no_mojibake(
            " ".join(
                [
                    asset.title,
                    asset.description,
                    " ".join(asset.tags),
                    package.recommended_title,
                    package.summary,
                    " ".join(package.cover_lines),
                ]
            )
        )

        state = CopywriterStateFile.model_validate_json(
            (self.temp_root / "copywriter-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [f"{session_id}:1"])

        CopywriterService(self.settings).run()
        self.assertEqual(len(load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)), 1)
        self.assertEqual(
            len(load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)),
            1,
        )

        package_output_path = Path(package.path or "")
        output_path.unlink()
        package_output_path.unlink()
        CopywriterService(self.settings).run()
        self.assertTrue(output_path.exists())
        self.assertTrue(package_output_path.exists())
        self.assertEqual(len(load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)), 1)
        self.assertEqual(
            len(load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)),
            1,
        )

    def test_force_reprocess_appends_fresh_copy_and_package_rows(self) -> None:
        session_id = "session-copywriter-force"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:00,000 --> 00:00:02,000\n第一版普通装备选择\n",
        )
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )

        service = CopywriterService(self.settings)
        service.run()
        self._write_subtitle(
            session_id,
            "1\n00:00:00,000 --> 00:00:02,000\n第二版电刀AP机器人套路\n",
        )
        service.run(force_reprocess=True)

        copy_assets = load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)
        self.assertEqual(len(copy_assets), 2)
        self.assertEqual(copy_assets[-1].title, "电刀AP机器人")
        packages = load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)
        self.assertEqual(len(packages), 2)
        self.assertEqual(packages[-1].recommended_title, "电刀AP机器人")
        state = CopywriterStateFile.model_validate_json(
            (self.temp_root / "copywriter-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [f"{session_id}:1"])
        self._assert_no_mojibake(
            " ".join([copy_assets[-1].title, packages[-1].recommended_title])
        )

    def test_duplicate_subtitle_manifest_rows_process_once(self) -> None:
        session_id = "session-copywriter-duplicate-subtitles"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:00,000 --> 00:00:02,000\nduplicate subtitle row\n",
        )
        for _ in range(2):
            append_model(
                self.temp_root / "subtitle-assets.jsonl",
                SubtitleAsset(
                    session_id=session_id,
                    match_index=1,
                    path=str(subtitle_path),
                    format="srt",
                ),
            )

        CopywriterService(self.settings).run(force_reprocess=True)

        copy_assets = load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)
        self.assertEqual(len(copy_assets), 1)
        packages = load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)
        self.assertEqual(len(packages), 1)
        state = CopywriterStateFile.model_validate_json(
            (self.temp_root / "copywriter-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [f"{session_id}:1"])

    def test_missing_subtitle_is_skipped_without_processing_key(self) -> None:
        session_id = "session-copywriter-missing"
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(self.processed_root / session_id / "missing.srt"),
                format="srt",
            ),
        )

        CopywriterService(self.settings).run()

        self.assertEqual(load_models(self.temp_root / "copy-assets.jsonl", CopyAsset), [])
        state = CopywriterStateFile.model_validate_json(
            (self.temp_root / "copywriter-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state.processed_match_keys, [])

    def test_llm_semantic_asset_drives_publishing_copy_and_uses_cache(self) -> None:
        session_id = "session-copywriter-llm"
        self.settings.llm.enabled = True
        self.settings.llm.api_key = "test-key"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:00,000 --> 00:00:02,000\nraw leading subtitle\n\n"
            "2\n00:01:00,000 --> 00:01:05,000\n关键团战一钩直接打开局面\n",
        )
        export_path = self._write_export(session_id)
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        append_model(
            self.temp_root / "match-boundaries.jsonl",
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=120.0,
                confidence=0.95,
            ),
        )
        append_model(
            self.temp_root / "highlight-plans.jsonl",
            HighlightPlanAsset(
                session_id=session_id,
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=120.0,
                windows=[
                    HighlightClipWindow(
                        started_at_seconds=60.0,
                        ended_at_seconds=70.0,
                        reason="highlight_keyword",
                    )
                ],
                created_at=self._now(),
            ),
        )
        append_model(
            self.temp_root / "export-assets.jsonl",
            ExportAsset(
                session_id=session_id,
                match_index=1,
                path=str(export_path),
                subtitle_path=str(subtitle_path),
                created_at=self._now(),
            ),
        )
        provider = _FakeLlmProvider(_llm_payload("神钩开团逆转"))

        service = CopywriterService(self.settings, llm_provider=provider)
        service.run()
        service.run()

        self.assertEqual(len(provider.calls), 1)
        semantic_assets = load_models(
            self.temp_root / "copywriter-semantic-assets.jsonl",
            CopywriterSemanticAsset,
        )
        self.assertEqual(len(semantic_assets), 1)
        self.assertEqual(semantic_assets[0].token_usage["total_tokens"], 42)
        copy_assets = load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)
        self.assertEqual(copy_assets[0].title, "神钩开团逆转")
        self.assertNotEqual(copy_assets[0].title, "raw leading subtitle")
        packages = load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)
        self.assertEqual(packages[0].recommended_title, "神钩开团逆转")
        self.assertEqual(packages[0].cover_lines, ["神钩开团", "团战逆转"])
        self.assertIn("英雄联盟", packages[0].tags)

    def test_llm_semantic_force_reprocess_bypasses_cache(self) -> None:
        session_id = "session-copywriter-llm-force"
        self.settings.llm.enabled = True
        self.settings.llm.api_key = "test-key"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:00,000 --> 00:00:02,000\n第一版团战素材\n",
        )
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        provider = _FakeLlmProvider(
            _llm_payload("神钩开团逆转"),
            _llm_payload("丝血反打名场面"),
        )
        service = CopywriterService(self.settings, llm_provider=provider)

        service.run_semantic()
        service.run_semantic(force_reprocess=True)

        self.assertEqual(len(provider.calls), 2)
        semantic_assets = load_models(
            self.temp_root / "copywriter-semantic-assets.jsonl",
            CopywriterSemanticAsset,
        )
        self.assertEqual(
            [asset.result.recommended_title for asset in semantic_assets],
            ["神钩开团逆转", "丝血反打名场面"],
        )

    def test_llm_schema_failure_falls_back_to_heuristic_copy(self) -> None:
        session_id = "session-copywriter-llm-fallback"
        self.settings.llm.enabled = True
        self.settings.llm.api_key = "test-key"
        self.settings.llm.max_retries = 0
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:00,000 --> 00:00:02,000\nfallback subtitle title\n",
        )
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        provider = _FakeLlmProvider('{"recommended_title": "broken"}')

        CopywriterService(self.settings, llm_provider=provider).run()

        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(
            load_models(
                self.temp_root / "copywriter-semantic-assets.jsonl",
                CopywriterSemanticAsset,
            ),
            [],
        )
        copy_assets = load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)
        self.assertEqual(len(copy_assets), 1)
        self.assertNotEqual(copy_assets[0].title, "broken")

    def test_filters_by_session_ids(self) -> None:
        for session_id in ["session-copywriter-filter-a", "session-copywriter-filter-b"]:
            subtitle_path = self._write_subtitle(
                session_id,
                "1\n00:00:00,000 --> 00:00:02,000\nfiltered subtitle\n",
            )
            append_model(
                self.temp_root / "subtitle-assets.jsonl",
                SubtitleAsset(
                    session_id=session_id,
                    match_index=1,
                    path=str(subtitle_path),
                    format="srt",
                ),
            )

        CopywriterService(self.settings).run(session_ids={"session-copywriter-filter-b"})

        copy_assets = load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)
        self.assertEqual(len(copy_assets), 1)
        self.assertEqual(copy_assets[0].session_id, "session-copywriter-filter-b")

    def test_gameplay_headline_beats_chat_finance_when_both_exist(self) -> None:
        session_id = "session-copywriter-gameplay-rank"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:00,000 --> 00:00:03,000\n"
            "一定要装没钱 这个人设还能聊炒股经济学\n\n"
            "2\n00:01:00,000 --> 00:01:04,000\n"
            "上单电刀AP机器人 清线快伤害高\n",
        )
        export_path = self._write_export(session_id)
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        append_model(
            self.temp_root / "export-assets.jsonl",
            ExportAsset(
                session_id=session_id,
                match_index=1,
                path=str(export_path),
                subtitle_path=str(subtitle_path),
                created_at=self._now(),
            ),
        )

        CopywriterService(self.settings).run()

        copy_assets = load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)
        self.assertEqual(len(copy_assets), 1)
        self.assertIn("电刀AP机器人", copy_assets[0].title)
        self.assertNotEqual(copy_assets[0].title, "装没钱人设 炒股经济学")

    def test_finance_persona_headline_beats_generic_damage_reaction(self) -> None:
        session_id = "session-copywriter-finance-persona"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:00,000 --> 00:00:03,000\n"
            "一定要装没钱 他这种人设不能装有钱\n\n"
            "2\n00:01:00,000 --> 00:01:04,000\n"
            "如果到那时候我是不是就变成炒股博主了\n\n"
            "3\n00:02:00,000 --> 00:02:04,000\n"
            "哇靠 什么伤害 伤害这么高\n",
        )
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )

        CopywriterService(self.settings).run()

        packages = load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)
        self.assertEqual(len(packages), 1)
        package = packages[0]
        self.assertIn("装没钱人设", package.recommended_title)
        self.assertIn("炒股经济学", package.recommended_title)
        self.assertNotIn("清线快伤害高", package.recommended_title)
        self.assertNotIn("伤害这么高", package.recommended_title)

    def test_publishing_package_prefers_highlight_window_cues(self) -> None:
        session_id = "session-copywriter-highlight"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:00,000 --> 00:00:02,000\n普通开局先补刀\n\n"
            "2\n00:01:00,000 --> 00:01:04,000\n上单电刀AP机器人 清线快伤害高\n",
        )
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        append_model(
            self.temp_root / "match-boundaries.jsonl",
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=0.0,
                ended_at_seconds=100.0,
                confidence=0.95,
            ),
        )
        append_model(
            self.temp_root / "highlight-plans.jsonl",
            HighlightPlanAsset(
                session_id=session_id,
                match_index=1,
                source_boundary_start_seconds=0.0,
                source_boundary_end_seconds=100.0,
                windows=[
                    HighlightClipWindow(
                        started_at_seconds=58.0,
                        ended_at_seconds=66.0,
                        reason="highlight_keyword",
                    )
                ],
                created_at=self._now(),
            ),
        )

        CopywriterService(self.settings).run()

        copy_assets = load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)
        self.assertEqual(len(copy_assets), 1)
        self.assertIn("电刀AP机器人", copy_assets[0].title)
        self.assertNotIn("普通开局", copy_assets[0].title)

        packages = load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)
        self.assertEqual(len(packages), 1)
        package = packages[0]
        self.assertIn("电刀AP机器人", package.recommended_title)
        self.assertIn("电刀", "".join(package.cover_lines))
        self.assertEqual(package.evidence[0], "01:00 上单电刀AP机器人 清线快伤害高")
        self._assert_no_mojibake(
            " ".join([package.recommended_title, package.summary, *package.cover_lines])
        )

    def test_title_uses_secondary_summary_instead_of_raw_first_subtitle(self) -> None:
        session_id = "session-copywriter-summary"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:00,000 --> 00:00:02,000\n不是,很多人就比如说,我跟你们不一样\n\n"
            "2\n00:01:00,000 --> 00:01:04,000\n一定要装没钱,他这种人设,不能装有钱\n\n"
            "3\n00:02:00,000 --> 00:02:04,000\n如果到那时候我是不是就变成炒股博主了\n\n"
            "4\n00:03:00,000 --> 00:03:04,000\n被粉丝认出来了这把有点尴尬\n",
        )
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )

        CopywriterService(self.settings).run()

        packages = load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)
        self.assertEqual(len(packages), 1)
        package = packages[0]
        self.assertIn("装没钱人设", package.recommended_title)
        self.assertIn("炒股经济学", package.recommended_title)
        self.assertIn("被粉丝认出来", package.recommended_title)
        self.assertNotIn("不是,很多人", package.recommended_title)
        cover_text = "".join(package.cover_lines)
        self.assertIn("装没钱人设", cover_text)
        self.assertIn("炒股经济学", cover_text)
        self.assertIn("被粉丝认出来", cover_text)
        self._assert_no_mojibake(
            " ".join([package.recommended_title, package.summary, *package.cover_lines])
        )

    def test_cover_lines_expand_short_title_with_summary_points(self) -> None:
        service = CopywriterService(self.settings)

        cover_lines = service._cover_lines(
            excerpt=[
                "上单电刀AP机器人这个清线快伤害高",
                "骗路人是韩服千分套路",
                "最后还是被粉丝认出来了",
            ],
            title="上单电刀AP机器人",
            match_index=1,
        )

        cover_text = "".join(cover_lines)
        self.assertLessEqual(len(cover_lines), 4)
        self.assertIn("上单电刀AP机器人", cover_text)
        self.assertIn("清线快伤害高", cover_text)
        self.assertIn("韩服千分套路", cover_text)
        self.assertIn("被粉丝认出来", cover_text)

    def test_short_weak_title_expands_with_context(self) -> None:
        service = CopywriterService(self.settings)

        candidates = service._title_candidates(
            excerpt=[
                "stacking?",
                "that depends how many games",
                "sixty percent win rate is fine",
            ],
            match_index=2,
        )

        self.assertNotEqual(candidates[0], "stacking?")
        self.assertIn("stacking?", candidates[0])
        self.assertIn("that depends", candidates[0])

    def test_single_short_theme_title_expands_with_context(self) -> None:
        service = CopywriterService(self.settings)

        candidates = service._title_candidates(
            excerpt=[
                "过几个月又有钱了然后冲进去结果亏了",
                "我认识那个16岁",
                "我有钱啊我钱给家里了",
            ],
            match_index=4,
        )

        self.assertIn("被粉丝认出来", candidates[0])
        self.assertIn("有钱", candidates[0])

    def test_cover_renderer_is_optional_and_records_cover_path(self) -> None:
        session_id = "session-copywriter-cover"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:02,000 --> 00:00:04,000\n上单电刀AP机器人 清线快伤害高\n",
        )
        export_path = self._write_export(session_id)
        recording_path = self._write_recording(session_id)
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        append_model(
            self.temp_root / "export-assets.jsonl",
            ExportAsset(
                session_id=session_id,
                match_index=1,
                path=str(export_path),
                subtitle_path=str(subtitle_path),
                created_at=self._now(),
            ),
        )
        append_model(
            self.temp_root / "recording-assets.jsonl",
            RecordingAsset(
                session_id=session_id,
                source_type=SourceType.DIRECT_STREAM,
                path=str(recording_path),
                started_at=self._now(),
                ended_at=self._now(),
            ),
        )
        append_model(
            self.temp_root / "match-boundaries.jsonl",
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=10.0,
                ended_at_seconds=100.0,
                confidence=0.95,
            ),
        )
        self._write_orchestrator_state(session_id, streamer_name="midu958")
        seen: dict[str, object] = {}

        def _cover_renderer(
            recording: Path,
            output: Path,
            cover_lines: list[str],
            *,
            at_seconds: float,
        ) -> bool:
            seen["recording"] = recording
            seen["cover_lines"] = cover_lines
            seen["at_seconds"] = at_seconds
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("cover", encoding="utf-8")
            return True

        CopywriterService(self.settings, cover_renderer=_cover_renderer).run()

        packages = load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)
        self.assertEqual(len(packages), 1)
        package = packages[0]
        self.assertIsNotNone(package.cover_path)
        self.assertTrue(Path(package.cover_path or "").exists())
        self.assertEqual(seen["recording"], recording_path)
        self.assertEqual(seen["at_seconds"], 12.0)
        seen_cover_lines = seen["cover_lines"]
        self.assertIsInstance(seen_cover_lines, list)
        self.assertIn("电刀", "".join(str(item) for item in seen_cover_lines))
        self.assertEqual(package.streamer_name, "midu958")
        self.assertIsNotNone(package.published_package_dir)
        self.assertIsNotNone(package.published_video_path)
        self.assertIsNotNone(package.published_cover_path)
        self.assertIsNotNone(package.published_metadata_path)
        published_package_dir = Path(package.published_package_dir or "")
        published_video = Path(package.published_video_path or "")
        published_cover = Path(package.published_cover_path or "")
        published_metadata = Path(package.published_metadata_path or "")
        self.assertTrue(published_package_dir.is_dir())
        self.assertTrue(published_video.exists())
        self.assertTrue(published_cover.exists())
        self.assertTrue(published_metadata.exists())
        self.assertEqual(published_package_dir.parent, export_path.parent)
        self.assertEqual(published_video.parent, published_package_dir)
        self.assertEqual(published_cover.parent, published_package_dir)
        self.assertEqual(published_metadata.parent, published_package_dir)
        self.assertIn("midu958", published_package_dir.name)
        self.assertIn("电刀AP机器人", published_package_dir.name)
        self.assertEqual(published_video.name, "video.mp4")
        self.assertEqual(published_cover.name, "cover.jpg")
        self.assertEqual(published_metadata.name, "upload.txt")
        metadata_text = published_metadata.read_text(encoding="utf-8")
        self.assertIn("Title:", metadata_text)
        self.assertIn("Description:", metadata_text)
        self.assertIn("Hashtags:", metadata_text)
        self.assertIn("Evidence:", metadata_text)

    def test_cover_renderer_uses_export_when_recording_is_unavailable(self) -> None:
        session_id = "session-copywriter-cover-export-fallback"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:02,000 --> 00:00:04,000\nexport fallback cover subtitle\n",
        )
        export_path = self._write_export(session_id)
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        append_model(
            self.temp_root / "export-assets.jsonl",
            ExportAsset(
                session_id=session_id,
                match_index=1,
                path=str(export_path),
                subtitle_path=str(subtitle_path),
                created_at=self._now(),
            ),
        )
        append_model(
            self.temp_root / "match-boundaries.jsonl",
            MatchBoundary(
                session_id=session_id,
                match_index=1,
                started_at_seconds=10.0,
                ended_at_seconds=100.0,
                confidence=0.95,
            ),
        )
        seen: dict[str, object] = {}

        def _cover_renderer(
            source: Path,
            output: Path,
            cover_lines: list[str],
            *,
            at_seconds: float,
        ) -> bool:
            seen["source"] = source
            seen["cover_lines"] = cover_lines
            seen["at_seconds"] = at_seconds
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("cover", encoding="utf-8")
            return True

        CopywriterService(self.settings, cover_renderer=_cover_renderer).run()

        packages = load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)
        self.assertEqual(len(packages), 1)
        package = packages[0]
        self.assertEqual(package.source_recording_path, None)
        self.assertEqual(seen["source"], export_path)
        self.assertEqual(seen["at_seconds"], 0.0)
        self.assertIsNotNone(package.cover_path)
        self.assertTrue(Path(package.cover_path or "").exists())
        self.assertIsNotNone(package.published_package_dir)
        self.assertIsNotNone(package.published_video_path)
        self.assertIsNotNone(package.published_cover_path)
        self.assertIsNotNone(package.published_metadata_path)
        published_package_dir = Path(package.published_package_dir or "")
        published_video = Path(package.published_video_path or "")
        published_cover = Path(package.published_cover_path or "")
        published_metadata = Path(package.published_metadata_path or "")
        self.assertTrue(published_package_dir.is_dir())
        self.assertTrue(published_video.exists())
        self.assertTrue(published_cover.exists())
        self.assertTrue(published_metadata.exists())
        self.assertEqual(published_package_dir.parent, export_path.parent)
        self.assertEqual(published_video.parent, published_package_dir)
        self.assertEqual(published_cover.parent, published_package_dir)
        self.assertEqual(published_metadata.parent, published_package_dir)
        self.assertEqual(published_video.name, "video.mp4")
        self.assertEqual(published_cover.name, "cover.jpg")
        self.assertEqual(published_metadata.name, "upload.txt")

    def test_missing_published_aliases_are_regenerated(self) -> None:
        session_id = "session-copywriter-publish-repair"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:02,000 --> 00:00:04,000\n"
            "publish repair subtitle\n",
        )
        export_path = self._write_export(session_id)
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        append_model(
            self.temp_root / "export-assets.jsonl",
            ExportAsset(
                session_id=session_id,
                match_index=1,
                path=str(export_path),
                subtitle_path=str(subtitle_path),
                created_at=self._now(),
            ),
        )

        def _cover_renderer(
            source: Path,
            output: Path,
            cover_lines: list[str],
            *,
            at_seconds: float,
        ) -> bool:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("cover", encoding="utf-8")
            return True

        service = CopywriterService(self.settings, cover_renderer=_cover_renderer)
        service.run()

        packages = load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)
        self.assertEqual(len(packages), 1)
        original = packages[0]
        published_package_dir = Path(original.published_package_dir or "")
        published_video = Path(original.published_video_path or "")
        published_cover = Path(original.published_cover_path or "")
        published_metadata = Path(original.published_metadata_path or "")
        self.assertTrue(published_package_dir.is_dir())
        self.assertTrue(published_video.exists())
        self.assertTrue(published_cover.exists())
        self.assertTrue(published_metadata.exists())
        published_video.unlink()
        published_cover.unlink()
        published_metadata.unlink()

        service.run()

        packages = load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)
        self.assertEqual(len(packages), 1)
        repaired = packages[0]
        repaired_package_dir = Path(repaired.published_package_dir or "")
        repaired_video = Path(repaired.published_video_path or "")
        repaired_cover = Path(repaired.published_cover_path or "")
        repaired_metadata = Path(repaired.published_metadata_path or "")
        self.assertTrue(repaired_package_dir.is_dir())
        self.assertTrue(repaired_video.exists())
        self.assertTrue(repaired_cover.exists())
        self.assertTrue(repaired_metadata.exists())
        self.assertEqual(repaired_video.parent, repaired_package_dir)
        self.assertEqual(repaired_cover.parent, repaired_package_dir)
        self.assertEqual(repaired_metadata.parent, repaired_package_dir)
        package_payload = json.loads(Path(repaired.path or "").read_text(encoding="utf-8"))
        self.assertEqual(package_payload["published_package_dir"], repaired.published_package_dir)
        self.assertEqual(repaired_video.name, "video.mp4")
        self.assertEqual(repaired_cover.name, "cover.jpg")
        self.assertEqual(repaired_metadata.name, "upload.txt")
        self.assertEqual(package_payload["published_cover_path"], repaired.published_cover_path)
        self.assertEqual(
            package_payload["published_metadata_path"],
            repaired.published_metadata_path,
        )
        self.assertEqual(len(load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)), 1)

    def test_legacy_flat_published_aliases_are_removed_on_rerun(self) -> None:
        session_id = "session-copywriter-legacy-publish-aliases"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:02,000 --> 00:00:04,000\n"
            "legacy publish alias cleanup subtitle\n",
        )
        export_path = self._write_export(session_id)
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        append_model(
            self.temp_root / "export-assets.jsonl",
            ExportAsset(
                session_id=session_id,
                match_index=1,
                path=str(export_path),
                subtitle_path=str(subtitle_path),
                created_at=self._now(),
            ),
        )

        def _cover_renderer(
            source: Path,
            output: Path,
            cover_lines: list[str],
            *,
            at_seconds: float,
        ) -> bool:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("cover", encoding="utf-8")
            return True

        service = CopywriterService(self.settings, cover_renderer=_cover_renderer)
        service.run()
        packages = load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)
        self.assertEqual(len(packages), 1)
        package = packages[0]
        published_package_dir = Path(package.published_package_dir or "")
        published_video = Path(package.published_video_path or "")
        published_cover = Path(package.published_cover_path or "")
        published_metadata = Path(package.published_metadata_path or "")
        self.assertTrue(published_package_dir.is_dir())
        self.assertTrue(published_video.exists())
        self.assertTrue(published_cover.exists())
        self.assertTrue(published_metadata.exists())

        legacy_stem = published_package_dir.name
        legacy_video = export_path.parent / f"{legacy_stem}{export_path.suffix}"
        legacy_cover = export_path.parent / f"{legacy_stem}.jpg"
        legacy_metadata = export_path.parent / f"{legacy_stem}.txt"
        legacy_video.write_text("old video alias", encoding="utf-8")
        legacy_cover.write_text("old cover alias", encoding="utf-8")
        legacy_metadata.write_text("old upload alias", encoding="utf-8")

        service.run()

        repaired_packages = load_models(
            self.temp_root / "publishing-packages.jsonl",
            PublishingPackage,
        )
        self.assertEqual(len(repaired_packages), 1)
        repaired = repaired_packages[0]
        self.assertTrue(export_path.exists())
        self.assertFalse(legacy_video.exists())
        self.assertFalse(legacy_cover.exists())
        self.assertFalse(legacy_metadata.exists())
        self.assertTrue(Path(repaired.published_video_path or "").exists())
        self.assertTrue(Path(repaired.published_cover_path or "").exists())
        self.assertTrue(Path(repaired.published_metadata_path or "").exists())
        self.assertEqual(len(load_models(self.temp_root / "copy-assets.jsonl", CopyAsset)), 1)

    def test_streamer_name_can_come_from_selected_recording_state(self) -> None:
        session_id = "session-20260617073649-4b5ec478"
        subtitle_path = self._write_subtitle(
            session_id,
            "1\n00:00:02,000 --> 00:00:04,000\n上单电刀AP机器人 清线快伤害高\n",
        )
        export_path = self._write_export(session_id)
        append_model(
            self.temp_root / "subtitle-assets.jsonl",
            SubtitleAsset(
                session_id=session_id,
                match_index=1,
                path=str(subtitle_path),
                format="srt",
            ),
        )
        append_model(
            self.temp_root / "export-assets.jsonl",
            ExportAsset(
                session_id=session_id,
                match_index=1,
                path=str(export_path),
                subtitle_path=str(subtitle_path),
                created_at=self._now(),
            ),
        )
        self._write_orchestrator_state(
            session_id,
            streamer_name="觅渡Dzg",
            state_path=(
                self.temp_root
                / "selected-recordings"
                / "20260617-073649-fixture"
                / "orchestrator-state.json"
            ),
        )

        CopywriterService(self.settings).run()

        packages = load_models(self.temp_root / "publishing-packages.jsonl", PublishingPackage)
        self.assertEqual(len(packages), 1)
        package = packages[0]
        self.assertEqual(package.streamer_name, "觅渡Dzg")
        published_package_dir = Path(package.published_package_dir or "")
        published_video = Path(package.published_video_path or "")
        self.assertTrue(published_package_dir.is_dir())
        self.assertTrue(published_video.exists())
        self.assertEqual(published_video.parent, published_package_dir)
        self.assertIn("觅渡Dzg", published_package_dir.name)
        self.assertIn("电刀AP机器人", published_package_dir.name)
        self.assertEqual(published_video.name, "video.mp4")

    def test_render_cover_skips_when_ffmpeg_is_missing(self) -> None:
        recording_path = self._write_recording("session-copywriter-no-ffmpeg")
        output_path = self.processed_root / "session-copywriter-no-ffmpeg" / "cover.jpg"

        with patch("arl.copywriter.cover.shutil.which", return_value=None):
            rendered = render_cover(recording_path, output_path, ["封面文案"])

        self.assertFalse(rendered)
        self.assertFalse(output_path.exists())

    def test_cover_text_renderer_omits_panel_and_preserves_secondary_size(self) -> None:
        draw = _FakeCoverDraw()

        _draw_cover_text(
            draw,
            (1920, 1080),
            [
                "Very long explosive headline that has to shrink to fit the cover image",
                "Fast clear",
                "Ladder trick",
            ],
            _FakeCoverFontFactory,
        )

        self.assertEqual(draw.panels, [])
        self.assertEqual(len(draw.text_calls), 3)
        self.assertEqual(draw.text_calls[0]["fill"], (255, 238, 0))
        self.assertEqual(draw.text_calls[1]["fill"], (255, 255, 255))
        self.assertLess(draw.text_calls[0]["font"].size, 126)
        self.assertEqual(draw.text_calls[1]["font"].size, 96)
        self.assertGreater(
            draw.text_calls[0]["stroke_width"],
            draw.text_calls[1]["stroke_width"],
        )

    def _write_subtitle(self, session_id: str, content: str) -> Path:
        path = self.processed_root / session_id / "match-01.srt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _write_export(self, session_id: str) -> Path:
        path = self.export_root / f"{session_id}_match01.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
        return path

    def _write_recording(self, session_id: str) -> Path:
        path = self.root / "raw" / session_id / "recording-source.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
        return path

    def _write_orchestrator_state(
        self,
        session_id: str,
        *,
        streamer_name: str,
        state_path: Path | None = None,
    ) -> None:
        target_path = state_path or self.settings.orchestrator.state_file
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            OrchestratorStateFile(
                sessions=[
                    SessionRecord(
                        session_id=session_id,
                        streamer_name=streamer_name,
                        room_url="https://live.example/room",
                        platform="bilibili",
                        source_type=SourceType.DIRECT_STREAM,
                        status=SessionStatus.STOPPED,
                        started_at=self._now(),
                        ended_at=self._now(),
                    )
                ]
            ).model_dump_json(indent=2)
            + "\n",
            encoding="utf-8",
        )

    def _assert_no_mojibake(self, text: str) -> None:
        bad_tokens = ["鐢", "瑁", "鑻", "绗", "锝", "銆", "浣犳", "涓€"]
        for token in bad_tokens:
            self.assertNotIn(token, text)

    def _now(self) -> datetime:
        return datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


class _FakeCoverFont:
    def __init__(self, size: int) -> None:
        self.size = size


class _FakeCoverFontFactory:
    @staticmethod
    def truetype(path: str, *, size: int) -> _FakeCoverFont:
        return _FakeCoverFont(size)

    @staticmethod
    def load_default() -> _FakeCoverFont:
        return _FakeCoverFont(96)


class _FakeCoverDraw:
    def __init__(self) -> None:
        self.panels: list[tuple[int, int, int, int]] = []
        self.text_calls: list[dict[str, object]] = []

    def textbbox(
        self,
        xy: tuple[int, int],
        text: str,
        *,
        font: _FakeCoverFont,
        stroke_width: int,
    ) -> tuple[int, int, int, int]:
        return (
            xy[0],
            xy[1],
            xy[0] + int(len(text) * font.size * 0.62) + stroke_width * 2,
            xy[1] + font.size + stroke_width * 2,
        )

    def rounded_rectangle(
        self,
        box: tuple[int, int, int, int],
        *,
        radius: int,
        fill: tuple[int, int, int],
        outline: tuple[int, int, int],
        width: int,
    ) -> None:
        self.panels.append(box)

    def rectangle(
        self,
        box: tuple[int, int, int, int],
        *,
        fill: tuple[int, int, int],
        outline: tuple[int, int, int],
        width: int,
    ) -> None:
        self.panels.append(box)

    def text(
        self,
        xy: tuple[int, int],
        text: str,
        *,
        font: _FakeCoverFont,
        fill: tuple[int, int, int],
        stroke_width: int,
        stroke_fill: tuple[int, int, int],
    ) -> None:
        self.text_calls.append(
            {
                "xy": xy,
                "text": text,
                "font": font,
                "fill": fill,
                "stroke_width": stroke_width,
            }
        )


class _FakeLlmProvider:
    def __init__(self, *contents: str) -> None:
        self.contents = list(contents)
        self.calls: list[tuple[str, str]] = []

    def generate(self, *, system_prompt: str, user_prompt: str) -> LlmProviderResponse:
        self.calls.append((system_prompt, user_prompt))
        index = min(len(self.calls) - 1, len(self.contents) - 1)
        return LlmProviderResponse(
            content=self.contents[index],
            token_usage={"prompt_tokens": 20, "completion_tokens": 22, "total_tokens": 42},
        )


def _llm_payload(recommended_title: str) -> str:
    return json.dumps(
        {
            "title_candidates": [
                recommended_title,
                "团战逆转全局",
                "上分名场面",
            ],
            "recommended_title": recommended_title,
            "cover_lines": ["神钩开团", "团战逆转"],
            "summary": "关键团战一波打开局面，适合作为发布切片。",
            "description": "这局通过关键开团建立优势，后续节奏连续滚起。",
            "tags": ["英雄联盟", "直播切片", "神钩", "团战", "上分"],
            "hook_line": "神钩开团，团战逆转",
            "teaser_recommendations": [
                {
                    "source_start_seconds": 60.0,
                    "source_end_seconds": 68.0,
                    "hook_reason": "关键开团瞬间",
                }
            ],
        },
        ensure_ascii=False,
    )


if __name__ == "__main__":
    unittest.main()
