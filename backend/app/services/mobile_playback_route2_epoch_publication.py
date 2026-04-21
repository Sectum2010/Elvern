from __future__ import annotations

from pathlib import Path

from .mobile_playback_models import PlaybackEpoch


def _route2_publish_init_locked(
    epoch: PlaybackEpoch,
    staged_init_path: Path,
    *,
    rebuild_route2_published_frontier_locked,
    write_route2_epoch_metadata_locked,
) -> Path:
    epoch.published_dir.mkdir(parents=True, exist_ok=True)
    if not staged_init_path.exists():
        raise FileNotFoundError("Route 2 staged init segment is missing")
    if not epoch.published_init_path.exists():
        staged_init_path.replace(epoch.published_init_path)
    rebuild_route2_published_frontier_locked(epoch)
    write_route2_epoch_metadata_locked(epoch)
    return epoch.published_init_path


def _route2_publish_segment_locked(
    epoch: PlaybackEpoch,
    segment_index: int,
    staged_segment_path: Path,
    *,
    route2_segment_destination,
    rebuild_route2_published_frontier_locked,
    write_route2_epoch_metadata_locked,
) -> Path:
    if segment_index < 0:
        raise ValueError("Route 2 segment index must be non-negative")
    if not staged_segment_path.exists():
        raise FileNotFoundError("Route 2 staged segment is missing")
    destination = route2_segment_destination(epoch, segment_index)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        staged_segment_path.replace(destination)
    rebuild_route2_published_frontier_locked(epoch)
    write_route2_epoch_metadata_locked(epoch)
    return destination


def _publish_route2_epoch_outputs_locked(
    epoch: PlaybackEpoch,
    *,
    route2_publish_init_locked,
    route2_publish_segment_locked,
) -> None:
    init_candidate = epoch.staging_dir / "init.mp4"
    if init_candidate.exists() and not epoch.init_published:
        route2_publish_init_locked(epoch, init_candidate)
    for child in sorted(epoch.staging_dir.glob("segment_*.m4s")):
        token = child.stem.removeprefix("segment_")
        try:
            segment_index = int(token)
        except ValueError:
            continue
        if segment_index in epoch.published_segments:
            continue
        route2_publish_segment_locked(epoch, segment_index, child)
