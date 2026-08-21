from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from typing import Iterable

from .constants import CLOCK_ALGORITHM_VERSION


def monotonic_raw_time_ns() -> int | None:
    clock_id = getattr(time, "CLOCK_MONOTONIC_RAW", None)
    if clock_id is None or not hasattr(time, "clock_gettime_ns"):
        return None
    try:
        return time.clock_gettime_ns(clock_id)
    except (OSError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class ClockExchangeSample:
    client_send_wall_ns: int
    client_receive_wall_ns: int
    server_receive_wall_ns: int
    server_send_wall_ns: int

    @property
    def round_trip_ns(self) -> int:
        server_processing = max(0, self.server_send_wall_ns - self.server_receive_wall_ns)
        return max(
            0,
            (self.client_receive_wall_ns - self.client_send_wall_ns) - server_processing,
        )

    @property
    def offset_ns(self) -> int:
        client_midpoint = (self.client_send_wall_ns + self.client_receive_wall_ns) // 2
        server_midpoint = (self.server_receive_wall_ns + self.server_send_wall_ns) // 2
        return server_midpoint - client_midpoint


@dataclass(frozen=True, slots=True)
class ClockEstimate:
    algorithm_version: str
    offset_ns: int
    network_rtt_ns: int
    uncertainty_ns: int
    sample_count: int


def estimate_clock(samples: Iterable[ClockExchangeSample]) -> ClockEstimate:
    sample_list = list(samples)
    if not sample_list:
        raise ValueError("At least one clock sample is required")
    ordered = sorted(sample_list, key=lambda sample: sample.round_trip_ns)
    keep_count = max(1, min(len(ordered), (len(ordered) + 1) // 2))
    selected = ordered[:keep_count]
    offsets = [sample.offset_ns for sample in selected]
    rtts = [sample.round_trip_ns for sample in selected]
    median_offset = int(statistics.median(offsets))
    minimum_rtt = min(rtts)
    offset_spread = max(offsets) - min(offsets) if len(offsets) > 1 else 0
    uncertainty = max(minimum_rtt // 2, offset_spread // 2)
    return ClockEstimate(
        algorithm_version=CLOCK_ALGORITHM_VERSION,
        offset_ns=median_offset,
        network_rtt_ns=minimum_rtt,
        uncertainty_ns=uncertainty,
        sample_count=len(sample_list),
    )
