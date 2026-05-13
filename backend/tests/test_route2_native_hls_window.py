from __future__ import annotations

import pytest

from backend.app.services.route2_native_hls_window import (
    BUFFER_TIER_FORWARD_SECONDS,
    HLS_JS_ENGINE_LABEL,
    NATIVE_HLS_BACK_WINDOW_SECONDS,
    NATIVE_HLS_ENGINE_LABEL,
    NATIVE_HLS_WINDOW_POLICY,
    WINDOW_ANCHOR_DRIFT_REFRESH_SECONDS,
    WINDOW_EDGE_REFRESH_RUNWAY_SECONDS,
    build_active_window_snapshot_fields,
    client_back_buffer_prune_supported,
    compute_native_hls_window,
    compute_window_forward_seconds,
    is_native_hls_engine,
    is_position_in_active_window,
    render_route2_epoch_manifest_text,
    resolve_window_anchor_seconds,
    should_refresh_native_hls_window,
    slice_manifest_segments_for_window,
)


class TestLockedConstants:
    def test_back_window_is_120_seconds(self) -> None:
        assert NATIVE_HLS_BACK_WINDOW_SECONDS == pytest.approx(120.0)

    def test_full_bad_condition_forward_window_is_900_seconds(self) -> None:
        assert BUFFER_TIER_FORWARD_SECONDS["full_bad_condition"] == pytest.approx(900.0)

    def test_lite_fast_forward_window_is_15_seconds(self) -> None:
        assert BUFFER_TIER_FORWARD_SECONDS["lite_fast"] == pytest.approx(15.0)

    def test_lite_uncertain_forward_window_is_45_seconds(self) -> None:
        assert BUFFER_TIER_FORWARD_SECONDS["lite_uncertain"] == pytest.approx(45.0)

    def test_lite_undersupply_forward_window_is_180_seconds(self) -> None:
        assert BUFFER_TIER_FORWARD_SECONDS["lite_undersupply"] == pytest.approx(180.0)

    def test_full_healthy_forward_window_is_120_seconds(self) -> None:
        assert BUFFER_TIER_FORWARD_SECONDS["full_healthy"] == pytest.approx(120.0)


class TestComputeWindowForwardSeconds:
    @pytest.mark.parametrize(
        "tier, expected",
        [
            ("lite_fast", 15.0),
            ("lite_uncertain", 45.0),
            ("lite_undersupply", 180.0),
            ("full_healthy", 120.0),
            ("full_bad_condition", 900.0),
        ],
    )
    def test_known_tiers_map_to_locked_targets(self, tier: str, expected: float) -> None:
        assert compute_window_forward_seconds(buffer_tier=tier) == pytest.approx(expected)

    def test_unknown_tier_falls_back_to_lite_uncertain_for_lite_mode(self) -> None:
        result = compute_window_forward_seconds(buffer_tier=None, playback_mode="lite")
        assert result == pytest.approx(45.0)

    def test_unknown_tier_falls_back_to_full_healthy_for_full_mode(self) -> None:
        result = compute_window_forward_seconds(buffer_tier="???", playback_mode="full")
        assert result == pytest.approx(120.0)


class TestEngineLabels:
    def test_native_hls_label_detected(self) -> None:
        assert is_native_hls_engine("native_hls") is True
        assert is_native_hls_engine(" Native_HLS ") is True

    def test_other_engines_are_not_native_hls(self) -> None:
        assert is_native_hls_engine("hls_js") is False
        assert is_native_hls_engine(None) is False

    def test_only_hls_js_supports_back_buffer_prune(self) -> None:
        assert client_back_buffer_prune_supported(HLS_JS_ENGINE_LABEL) is True
        assert client_back_buffer_prune_supported(NATIVE_HLS_ENGINE_LABEL) is False
        assert client_back_buffer_prune_supported(None) is False
        assert client_back_buffer_prune_supported("legacy") is False


class TestComputeNativeHlsWindow:
    def test_window_starts_at_max_zero_anchor_minus_120(self) -> None:
        window = compute_native_hls_window(
            anchor_seconds=300,
            duration_seconds=3600,
            buffer_tier="full_healthy",
        )
        assert window["active_window_start_seconds"] == pytest.approx(180.0)

    def test_anchor_at_zero_clamps_window_start_to_zero(self) -> None:
        window = compute_native_hls_window(
            anchor_seconds=0,
            duration_seconds=3600,
            buffer_tier="full_healthy",
        )
        assert window["active_window_start_seconds"] == pytest.approx(0.0)

    def test_window_does_not_grow_unbounded_with_watched_history(self) -> None:
        # Phase 2 contract: after the playhead advances from 0 → 300, the
        # window_start must be ~180 (not 0). Watched history has been pruned.
        window = compute_native_hls_window(
            anchor_seconds=300,
            duration_seconds=7200,
            buffer_tier="full_healthy",
        )
        assert window["active_window_start_seconds"] == pytest.approx(180.0)
        assert window["active_window_end_seconds"] == pytest.approx(420.0)

    def test_full_bad_condition_uses_900_second_forward_window(self) -> None:
        window = compute_native_hls_window(
            anchor_seconds=600,
            duration_seconds=7200,
            buffer_tier="full_bad_condition",
        )
        assert window["active_window_forward_seconds"] == pytest.approx(900.0)
        assert window["active_window_end_seconds"] == pytest.approx(1500.0)

    def test_window_clamped_to_full_duration(self) -> None:
        window = compute_native_hls_window(
            anchor_seconds=3550,
            duration_seconds=3600,
            buffer_tier="full_bad_condition",
        )
        assert window["active_window_end_seconds"] == pytest.approx(3600.0)

    def test_seeking_back_to_zero_recreates_window_around_zero(self) -> None:
        # User seeks back past retained history → new window centred at 0.
        window = compute_native_hls_window(
            anchor_seconds=0,
            duration_seconds=7200,
            buffer_tier="lite_fast",
        )
        assert window["active_window_start_seconds"] == pytest.approx(0.0)
        assert window["active_window_end_seconds"] == pytest.approx(15.0)

    def test_window_policy_label_is_stable(self) -> None:
        window = compute_native_hls_window(
            anchor_seconds=10,
            duration_seconds=100,
            buffer_tier="lite_fast",
        )
        assert window["active_window_policy"] == NATIVE_HLS_WINDOW_POLICY
        assert window["active_window_back_seconds"] == pytest.approx(120.0)

    def test_negative_anchor_is_clamped_to_zero(self) -> None:
        window = compute_native_hls_window(
            anchor_seconds=-5,
            duration_seconds=3600,
            buffer_tier="lite_fast",
        )
        assert window["active_window_start_seconds"] == pytest.approx(0.0)


class TestResolveWindowAnchor:
    def test_prefers_current_position_over_target_and_attach(self) -> None:
        anchor = resolve_window_anchor_seconds(
            current_position_seconds=120,
            target_position_seconds=60,
            attach_position_seconds=30,
        )
        assert anchor == pytest.approx(120.0)

    def test_falls_through_to_target_when_current_is_zero(self) -> None:
        anchor = resolve_window_anchor_seconds(
            current_position_seconds=0,
            target_position_seconds=60,
            attach_position_seconds=30,
        )
        assert anchor == pytest.approx(60.0)

    def test_falls_through_to_attach_when_current_and_target_are_zero(self) -> None:
        anchor = resolve_window_anchor_seconds(
            current_position_seconds=0,
            target_position_seconds=0,
            attach_position_seconds=30,
        )
        assert anchor == pytest.approx(30.0)

    def test_returns_zero_when_no_position_known(self) -> None:
        assert resolve_window_anchor_seconds() == 0.0


class TestIsPositionInActiveWindow:
    def test_inside_window_returns_true(self) -> None:
        assert is_position_in_active_window(
            150,
            window_start_seconds=120,
            window_end_seconds=180,
        ) is True

    def test_outside_window_returns_false(self) -> None:
        assert is_position_in_active_window(
            5,
            window_start_seconds=120,
            window_end_seconds=180,
        ) is False
        assert is_position_in_active_window(
            300,
            window_start_seconds=120,
            window_end_seconds=180,
        ) is False

    def test_trailing_headroom_pushes_target_out_of_window(self) -> None:
        assert is_position_in_active_window(
            179.5,
            window_start_seconds=120,
            window_end_seconds=180,
            headroom_seconds=2.0,
        ) is False

    def test_empty_window_always_returns_false(self) -> None:
        assert is_position_in_active_window(
            150,
            window_start_seconds=180,
            window_end_seconds=180,
        ) is False


class TestShouldRefreshNativeHlsWindow:
    def test_approaching_window_end_triggers_refresh(self) -> None:
        result = should_refresh_native_hls_window(
            current_position_seconds=185,
            window_start_seconds=60,
            window_end_seconds=200,
        )
        assert result["should_refresh"] is True
        assert result["reason"] == "approaching_window_end"

    def test_with_runway_to_spare_no_refresh(self) -> None:
        result = should_refresh_native_hls_window(
            current_position_seconds=120,
            window_start_seconds=60,
            window_end_seconds=200,
        )
        assert result["should_refresh"] is False
        assert result["reason"] is None

    def test_seek_outside_window_triggers_refresh(self) -> None:
        result = should_refresh_native_hls_window(
            current_position_seconds=120,
            window_start_seconds=60,
            window_end_seconds=200,
            seek_target_seconds=900,
        )
        assert result["should_refresh"] is True
        assert result["reason"] == "seek_target_outside_window"

    def test_anchor_drift_triggers_refresh(self) -> None:
        result = should_refresh_native_hls_window(
            current_position_seconds=400,
            window_start_seconds=60,
            window_end_seconds=600,
            window_anchor_seconds=100,
        )
        # 400 vs anchor 100 → drift 300 > 10 → refresh
        assert result["should_refresh"] is True
        assert result["reason"] == "anchor_drift"

    def test_buffer_tier_change_triggers_refresh(self) -> None:
        result = should_refresh_native_hls_window(
            current_position_seconds=120,
            window_start_seconds=60,
            window_end_seconds=600,
            buffer_tier_changed=True,
        )
        assert result["should_refresh"] is True
        assert result["reason"] == "buffer_tier_changed"

    def test_runway_threshold_matches_constant(self) -> None:
        # Just above the threshold → no refresh.
        ok = should_refresh_native_hls_window(
            current_position_seconds=200 - WINDOW_EDGE_REFRESH_RUNWAY_SECONDS - 0.5,
            window_start_seconds=60,
            window_end_seconds=200,
        )
        # Right at the threshold → refresh.
        edge = should_refresh_native_hls_window(
            current_position_seconds=200 - WINDOW_EDGE_REFRESH_RUNWAY_SECONDS,
            window_start_seconds=60,
            window_end_seconds=200,
        )
        assert ok["should_refresh"] is False
        assert edge["should_refresh"] is True


class TestBuildActiveWindowSnapshotFields:
    def test_native_hls_session_marks_prune_unsupported(self) -> None:
        fields = build_active_window_snapshot_fields(
            selected_hls_engine="native_hls",
            duration_seconds=7200,
            buffer_tier="lite_fast",
            playback_mode="lite",
            current_position_seconds=300,
        )
        assert fields["selected_hls_engine"] == "native_hls"
        assert fields["client_back_buffer_prune_supported"] is False
        assert fields["native_hls_window_policy"] == NATIVE_HLS_WINDOW_POLICY

    def test_hls_js_session_reports_prune_supported(self) -> None:
        fields = build_active_window_snapshot_fields(
            selected_hls_engine="hls_js",
            duration_seconds=7200,
            buffer_tier="full_healthy",
            playback_mode="full",
            current_position_seconds=300,
        )
        assert fields["client_back_buffer_prune_supported"] is True

    def test_full_duration_is_independent_of_active_window(self) -> None:
        fields = build_active_window_snapshot_fields(
            selected_hls_engine="native_hls",
            duration_seconds=7200,
            buffer_tier="full_bad_condition",
            playback_mode="full",
            current_position_seconds=600,
        )
        # Full timeline still represents the full movie; window slides inside.
        assert fields["full_duration_seconds"] == pytest.approx(7200.0)
        assert fields["active_window_end_seconds"] < 7200.0

    def test_anchor_drift_constant_consistent(self) -> None:
        # Smoke check that the published constant matches the helper default
        # used by the orchestrator. Locks the contract.
        assert WINDOW_ANCHOR_DRIFT_REFRESH_SECONDS == pytest.approx(10.0)

    def test_revision_passes_through_as_int_when_possible(self) -> None:
        fields = build_active_window_snapshot_fields(
            selected_hls_engine="native_hls",
            duration_seconds=7200,
            buffer_tier="lite_fast",
            playback_mode="lite",
            active_window_revision=7,
        )
        assert fields["active_window_revision"] == 7

    def test_attach_anchor_falls_through_when_position_unknown(self) -> None:
        fields = build_active_window_snapshot_fields(
            selected_hls_engine="native_hls",
            duration_seconds=7200,
            buffer_tier="lite_fast",
            playback_mode="lite",
            attach_position_seconds=300,
        )
        assert fields["active_window_anchor_seconds"] == pytest.approx(300.0)
        assert fields["active_window_start_seconds"] == pytest.approx(180.0)


class TestSliceManifestSegmentsForWindow:
    def test_window_starting_at_zero_keeps_first_segment(self) -> None:
        result = slice_manifest_segments_for_window(
            manifest_end_segment=200,
            segment_duration_seconds=2.0,
            epoch_start_seconds=0.0,
            window_start_seconds=0.0,
            window_end_seconds=15.0,
        )
        assert result["first_segment_index"] == 0
        assert result["last_segment_index"] == 7
        assert result["media_sequence_number"] == 0
        assert result["first_segment_start_seconds"] == pytest.approx(0.0)

    def test_window_180_to_420_excludes_segments_before_180(self) -> None:
        # SEGMENT_DURATION = 2 → segment 90 starts at 180s, segment 209 ends at 420s.
        result = slice_manifest_segments_for_window(
            manifest_end_segment=400,
            segment_duration_seconds=2.0,
            epoch_start_seconds=0.0,
            window_start_seconds=180.0,
            window_end_seconds=420.0,
        )
        assert result["first_segment_index"] == 90
        assert result["media_sequence_number"] == 90
        assert result["first_segment_start_seconds"] == pytest.approx(180.0)
        # Last included segment must start strictly before the window end.
        first_kept_start = result["first_segment_start_seconds"]
        last_kept_start = first_kept_start + (result["last_segment_index"] - result["first_segment_index"]) * 2.0
        assert last_kept_start < 420.0

    def test_first_segment_keeps_window_start_inside_a_segment(self) -> None:
        # window_start=181 falls inside segment 90 (180–182), keep segment 90.
        result = slice_manifest_segments_for_window(
            manifest_end_segment=400,
            segment_duration_seconds=2.0,
            epoch_start_seconds=0.0,
            window_start_seconds=181.0,
            window_end_seconds=240.0,
        )
        assert result["first_segment_index"] == 90
        assert result["first_segment_start_seconds"] == pytest.approx(180.0)

    def test_slice_is_clamped_to_published_frontier(self) -> None:
        result = slice_manifest_segments_for_window(
            manifest_end_segment=10,
            segment_duration_seconds=2.0,
            epoch_start_seconds=0.0,
            window_start_seconds=0.0,
            window_end_seconds=120.0,
        )
        assert result["last_segment_index"] == 10

    def test_slice_with_epoch_start_offset(self) -> None:
        # Epoch starts at absolute 1000s; window 1180→1240 ⇒ first segment 90.
        result = slice_manifest_segments_for_window(
            manifest_end_segment=400,
            segment_duration_seconds=2.0,
            epoch_start_seconds=1000.0,
            window_start_seconds=1180.0,
            window_end_seconds=1240.0,
        )
        assert result["first_segment_index"] == 90
        assert result["first_segment_start_seconds"] == pytest.approx(1180.0)

    def test_degenerate_window_returns_safe_terminal_slice(self) -> None:
        # window_end <= window_start ⇒ degenerate; helper returns last published.
        result = slice_manifest_segments_for_window(
            manifest_end_segment=42,
            segment_duration_seconds=2.0,
            epoch_start_seconds=0.0,
            window_start_seconds=200.0,
            window_end_seconds=200.0,
        )
        assert result["first_segment_index"] == 42
        assert result["last_segment_index"] == 42
        assert result["media_sequence_number"] == 42

    def test_media_sequence_always_equals_first_index(self) -> None:
        # Phase 2B contract: HLS clients require MEDIA-SEQUENCE == first segment index.
        result = slice_manifest_segments_for_window(
            manifest_end_segment=500,
            segment_duration_seconds=2.0,
            epoch_start_seconds=0.0,
            window_start_seconds=300.0,
            window_end_seconds=420.0,
        )
        assert result["media_sequence_number"] == result["first_segment_index"]


class TestRenderRoute2EpochManifestText:
    def _common_kwargs(self, **overrides):
        kwargs = dict(
            epoch_start_seconds=0.0,
            attach_position_seconds=200.0,
            manifest_end_segment=400,
            duration_seconds=3600.0,
            segment_duration_seconds=2.0,
            manifest_complete=False,
        )
        kwargs.update(overrides)
        return kwargs

    def _segment_uris(self, body: str) -> list[str]:
        return [line for line in body.splitlines() if line.startswith("segments/")]

    def test_no_window_emits_full_playlist_from_segment_zero(self) -> None:
        # hls.js / legacy path: no window passed → playlist starts at segment 0.
        body = render_route2_epoch_manifest_text(**self._common_kwargs())
        assert "#EXT-X-MEDIA-SEQUENCE:0" in body
        assert "segments/0.m4s" in body
        assert "segments/400.m4s" in body
        assert self._segment_uris(body)[0] == "segments/0.m4s"
        assert self._segment_uris(body)[-1] == "segments/400.m4s"
        assert body.startswith("#EXTM3U\n#EXT-X-VERSION:7\n")

    def test_window_180_to_420_excludes_pre_180_segments(self) -> None:
        # Native_hls slide. Segment 90 starts at 180s, segment 209 starts at 418s.
        body = render_route2_epoch_manifest_text(
            **self._common_kwargs(
                window_start_seconds=180.0,
                window_end_seconds=420.0,
            )
        )
        assert "#EXT-X-MEDIA-SEQUENCE:90" in body
        assert "segments/90.m4s" in body
        assert "segments/89.m4s" not in body
        assert "segments/0.m4s" not in body

    def test_window_180_to_420_excludes_segments_after_window_end(self) -> None:
        # Segment 209 spans 418→420 and is the final segment intersecting the active window.
        body = render_route2_epoch_manifest_text(
            **self._common_kwargs(
                window_start_seconds=180.0,
                window_end_seconds=420.0,
            )
        )
        segment_uris = self._segment_uris(body)
        assert segment_uris[0] == "segments/90.m4s"
        assert segment_uris[-1] == "segments/209.m4s"
        assert "segments/210.m4s" not in segment_uris
        assert "segments/400.m4s" not in segment_uris

    def test_window_manifest_includes_neither_old_nor_future_segments(self) -> None:
        body = render_route2_epoch_manifest_text(
            **self._common_kwargs(
                window_start_seconds=180.0,
                window_end_seconds=420.0,
            )
        )
        segment_uris = self._segment_uris(body)
        segment_indices = [int(uri.removeprefix("segments/").removesuffix(".m4s")) for uri in segment_uris]
        assert min(segment_indices) == 90
        assert max(segment_indices) == 209
        assert all(90 <= index <= 209 for index in segment_indices)

    def test_media_sequence_still_equals_first_included_segment(self) -> None:
        body = render_route2_epoch_manifest_text(
            **self._common_kwargs(
                window_start_seconds=180.0,
                window_end_seconds=420.0,
            )
        )
        assert "#EXT-X-MEDIA-SEQUENCE:90" in body
        assert self._segment_uris(body)[0] == "segments/90.m4s"

    def test_window_emits_relative_time_offset(self) -> None:
        # Attach point 300s, first segment in window starts at 180s.
        # The TIME-OFFSET written into the manifest is RELATIVE to the playlist
        # head, so the emitted offset must be 300 - 180 = 120s.
        body = render_route2_epoch_manifest_text(
            **self._common_kwargs(
                attach_position_seconds=300.0,
                window_start_seconds=180.0,
                window_end_seconds=420.0,
            )
        )
        assert "#EXT-X-START:TIME-OFFSET=120.000,PRECISE=YES" in body

    def test_extinf_durations_remain_correct_inside_window(self) -> None:
        body = render_route2_epoch_manifest_text(
            **self._common_kwargs(
                window_start_seconds=180.0,
                window_end_seconds=240.0,
            )
        )
        # Every kept segment is 2 seconds long; check at least three of them.
        assert "#EXTINF:2.000,\nsegments/90.m4s" in body
        assert "#EXTINF:2.000,\nsegments/91.m4s" in body
        assert "#EXTINF:2.000,\nsegments/92.m4s" in body

    def test_endlist_only_when_manifest_complete(self) -> None:
        open_body = render_route2_epoch_manifest_text(**self._common_kwargs())
        assert "#EXT-X-ENDLIST" not in open_body
        closed_body = render_route2_epoch_manifest_text(
            **self._common_kwargs(manifest_complete=True),
        )
        assert closed_body.rstrip().endswith("#EXT-X-ENDLIST")

    def test_init_map_uri_present(self) -> None:
        body = render_route2_epoch_manifest_text(**self._common_kwargs())
        assert '#EXT-X-MAP:URI="init.mp4"' in body

    def test_window_with_epoch_start_offset(self) -> None:
        body = render_route2_epoch_manifest_text(
            **self._common_kwargs(
                epoch_start_seconds=1000.0,
                attach_position_seconds=1300.0,
                window_start_seconds=1180.0,
                window_end_seconds=1420.0,
            )
        )
        assert "#EXT-X-MEDIA-SEQUENCE:90" in body
        # 1300 - (1000 + 90*2) = 1300 - 1180 = 120
        assert "#EXT-X-START:TIME-OFFSET=120.000,PRECISE=YES" in body


class TestNativeHlsRefreshTriggerHysteresis:
    """Phase 2B safeguard: continuous forward playback must not bump revisions
    every single heartbeat. The orchestrator-side wrapper passes a generous
    forward-drift threshold so refresh fires near the window edge, not on
    every 10s of playback.
    """

    def test_refresh_does_not_fire_every_heartbeat_during_steady_playback(self) -> None:
        # window [0, 120], anchor 0, forward window 120 (full_healthy).
        # Generous drift threshold: max(10, 120 * 0.5) = 60.
        # At current=30, we're not within 20s of the end (120) and drift=30 < 60.
        # Should NOT refresh.
        forward_drift_threshold = max(WINDOW_ANCHOR_DRIFT_REFRESH_SECONDS, 120.0 * 0.5)
        decision = should_refresh_native_hls_window(
            current_position_seconds=30,
            window_start_seconds=0,
            window_end_seconds=120,
            window_anchor_seconds=0,
            edge_runway_seconds=WINDOW_EDGE_REFRESH_RUNWAY_SECONDS,
            anchor_drift_seconds=forward_drift_threshold,
        )
        assert decision["should_refresh"] is False

    def test_refresh_fires_when_playhead_approaches_window_end(self) -> None:
        forward_drift_threshold = max(WINDOW_ANCHOR_DRIFT_REFRESH_SECONDS, 120.0 * 0.5)
        decision = should_refresh_native_hls_window(
            current_position_seconds=105,
            window_start_seconds=0,
            window_end_seconds=120,
            window_anchor_seconds=0,
            edge_runway_seconds=WINDOW_EDGE_REFRESH_RUNWAY_SECONDS,
            anchor_drift_seconds=forward_drift_threshold,
        )
        assert decision["should_refresh"] is True
        assert decision["reason"] == "approaching_window_end"
