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
    resolve_window_anchor_seconds,
    should_refresh_native_hls_window,
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
