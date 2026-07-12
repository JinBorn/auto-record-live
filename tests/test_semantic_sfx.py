from pathlib import Path

from arl.shared.semantic_sfx import (
    discover_semantic_sfx_candidates,
    load_semantic_sfx_catalog,
)


def test_catalog_and_candidate_discovery_are_bounded(tmp_path: Path) -> None:
    track = tmp_path / "mistake.wav"
    track.write_bytes(b"wav")
    manifest = tmp_path / "library.json"
    manifest.write_text(
        '{"tracks":[{"category":"mistake","path":"mistake.wav",'
        '"description":"streamer mistake"},{"category":"kill_coin",'
        '"path":"mistake.wav"}]}',
        encoding="utf-8",
    )

    catalog = load_semantic_sfx_catalog(manifest)
    candidates = discover_semantic_sfx_candidates(
        session_id="session-test",
        match_index=1,
        cues=[
            (10.0, 12.0, "这波我操作失误了"),
            (20.0, 22.0, "队友在打龙"),
            (30.0, 32.0, "我们ADC伤害很高"),
            (40.0, 42.0, "现在连招破坏了"),
        ],
        allowed_categories={item.category for item in catalog},
    )

    assert [item.category for item in catalog] == ["mistake"]
    assert len(candidates) == 1
    assert candidates[0].category_hints == ("mistake",)
    assert candidates[0].anchor_seconds == 11.0
