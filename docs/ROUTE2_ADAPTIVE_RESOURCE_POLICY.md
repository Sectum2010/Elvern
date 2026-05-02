# Route2 Adaptive Resource Policy

This document records resource-admission constraints for future Route2 real adaptive control. It is not an enablement note; real adaptive thread control remains disabled.

## Admission Floor

- Each active playback user has a protected minimum floor of 2 Route2 worker threads by default.
- `ELVERN_ROUTE2_PROTECTED_MIN_THREADS_PER_ACTIVE_USER` controls the floor and defaults to `2`.
- The protected floor must not exceed `ELVERN_ROUTE2_MAX_WORKER_THREADS`; that configuration would make admission impossible.
- Current admission is conservative: it uses current real spare capacity only.
- Current admission must not count theoretical reclaimable threads from already-running workers as available capacity.
- The protected floor is a minimum service guarantee, not a proof that 2 threads is always enough for real-time playback health.

## Active Playback Health Before New Admission

- Active playback health has priority over admitting new users.
- When CPU/thread is the limiting factor, an active playback should keep producing more than 1 second of ready runway per 1 second of watching.
- Mature runtime supply below real time is a protection signal. If an already-watching Route2 stream is CPU/thread-starved or otherwise real-time supply-at-risk, new admission should be blocked with `server_max_capacity` and an internal reason such as `active_stream_protection`.
- Manifest-complete or non-refilling sessions should not be treated as unhealthy merely because supply rate is zero.
- Immature supply metrics should be treated conservatively when capacity is tight; missing data must not be interpreted as healthy stream capacity.
- Source, provider, and client bottlenecks must remain distinct from CPU/thread starvation. Provider/source failures should not be mislabeled as generic CPU busy.

## Rebalance Dry-Run Only

- Runtime rebalance advice may identify active streams that need resources and theoretical donor candidates with surplus runway/supply above the protected floor.
- Donor capacity is not admission capacity until a future implementation actually reclaims it and fresh telemetry proves the host has released enough CPU/RAM/headroom.
- Current rebalance advice is metadata only. It must not change `assigned_threads`, mutate running ffmpeg, or admit a new user based on hypothetical donation.

## Reclaim Is Future Work

Running ffmpeg workers cannot safely have `-threads` mutated in place. Any future reclaim/downshift model must use a safe replacement/new-epoch mechanism and must be transactional and reversible.

### Phase A: Tentative Reclaim

- Identify active workers/users above the protected 2-thread floor.
- Select reclaim candidates fairly.
- Mark the worker/user as `reclaim_candidate` or `reclaiming`.
- Record `original_threads`, `reclaimed_threads`, `target_restore_threads`, `reclaim_reason`, and `reclaim_started_at`.
- Do not admit the new user yet if current spare capacity is insufficient.
- Do not violate the protected floor for existing users.

### Phase B: Observe Actual Headroom

- After a safe replacement/downshift, wait for continuous telemetry to mature.
- Recalculate actual host CPU, Route2 CPU, external CPU, RAM, and minimum-thread capacity.
- Admit the new user only if measured capacity can provide at least the protected floor.
- If measured headroom is still insufficient, return `server_max_capacity` to the new user.

## Reclaim Rollback

- Existing users are protected first.
- If tentative reclaim is not enough, the original user's previous tier should be restored when resource conditions allow.
- If immediate restoration is unsafe because external/system pressure changed, mark the original user as `priority_reexpand_pending`.
- Future spare resources must first be offered back to the user whose resources were reclaimed before admitting more users or promoting other users.
- Never let a failed new admission permanently downgrade an existing user unless ongoing host pressure truly requires it.

## External Workload Priority

- Non-Elvern CPU, ffmpeg, and system workload has priority over Elvern Route2 speed.
- High external CPU pressure or meaningful external ffmpeg pressure must block or reduce future adaptive promotion.
- Elvern must never kill, pause, renice, throttle, or modify non-Elvern processes.

## Current State

- `assigned_threads` remains controlled by the fixed Route2 dispatch path.
- Adaptive spawn/runtime decisions are still dry-run or shadow-only.
- Reclaim/downshift is not implemented.
- Admission failures use structured machine-readable codes such as `same_user_active_playback_limit` and `server_max_capacity`.
