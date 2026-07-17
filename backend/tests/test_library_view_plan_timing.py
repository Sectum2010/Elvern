from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from backend.app.services import library_home_curation_service
from backend.app.services import library_service
from backend.app.services.library_service import list_library_summary_v2
from backend.app.services.library_view_plan_timing import LibraryViewPlanTiming


def test_disabled_timing_collects_nothing() -> None:
    timing = LibraryViewPlanTiming(enabled=False)
    with timing.stage("private-looking-stage"):
        pass
    timing.count("rows", 12)

    assert timing.snapshot() == {
        "correlation_id": "",
        "stages_ms": {},
        "counts": {},
    }


def test_v2_timing_keeps_response_parity_and_records_anonymous_stages(
    initialized_settings,
    caplog,
) -> None:
    disabled_settings = replace(initialized_settings, library_plan_timing_enabled=False)
    enabled_settings = replace(initialized_settings, library_plan_timing_enabled=True)

    expected = list_library_summary_v2(disabled_settings, user_id=1)
    with caplog.at_level("INFO"):
        actual = list_library_summary_v2(enabled_settings, user_id=1)

    assert actual == expected
    timing_message = next(
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Library view-plan timing:")
    )
    for stage_name in (
        "accessible_media_sql",
        "continue_watching_sql",
        "watch_event_aggregates",
        "tracking_event_aggregates",
        "hidden_access_loading",
        "user_settings",
        "genre_loading",
        "category_filter",
        "source_filter",
        "genre_filter",
        "quality_filter",
        "hidden_filtering",
        "duplicate_representative",
        "row_decoration",
        "sorting",
        "local_series_rail_build",
        "cloud_series_rail_build",
        "continue_watching_selection",
        "recently_added_selection",
        "poster_url_resolution",
        "v2_serialization",
        "revision_hash",
        "json_encoding",
        "route_total",
    ):
        assert stage_name in timing_message
    assert str(initialized_settings.media_root) not in timing_message


def test_disabled_timing_does_not_add_a_diagnostic_json_encode(
    initialized_settings,
    monkeypatch,
) -> None:
    settings = replace(initialized_settings, library_plan_timing_enabled=False)
    original_dumps = library_service.json.dumps
    calls = 0

    def counting_dumps(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_dumps(*args, **kwargs)

    monkeypatch.setattr(library_service.json, "dumps", counting_dumps)
    list_library_summary_v2(settings, user_id=1)

    assert calls == 1


def test_series_rail_fallback_resolves_local_root_once_per_view_plan(
    initialized_settings,
    monkeypatch,
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "media"
    series_root = media_root / "Example Series"
    series_root.mkdir(parents=True)
    calls = 0

    def resolve_root(_settings):
        nonlocal calls
        calls += 1
        return media_root

    monkeypatch.setattr(
        library_home_curation_service,
        "get_effective_shared_local_library_path",
        resolve_root,
    )
    rows = [
        {
            "id": item_id,
            "title": f"Example Series {item_id}",
            "year": 2020 + item_id,
            "original_filename": f"Example.Series.{item_id}.mkv",
            "source_kind": "local",
            "file_path": str(series_root / f"movie-{item_id}.mkv"),
            "series_folder_key": None,
            "series_folder_name": None,
        }
        for item_id in range(1, 5)
    ]

    rails = library_home_curation_service._build_series_rail_plans(
        initialized_settings,
        rows=rows,
    )

    assert calls == 1
    assert len(rails) == 1
    assert [row["id"] for row in rails[0]["rows"]] == [1, 2, 3, 4]
