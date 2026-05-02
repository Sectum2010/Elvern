# Route2 Adaptive Resource Policy

This document records resource-admission constraints for future Route2 real adaptive control. It is not an enablement note; real adaptive thread control remains disabled.

## Admission Floor

- Each active playback user has a protected minimum floor of 2 Route2 worker threads by default.
- `ELVERN_ROUTE2_PROTECTED_MIN_THREADS_PER_ACTIVE_USER` controls the floor and defaults to `2`.
- The protected floor must not exceed `ELVERN_ROUTE2_MAX_WORKER_THREADS`; that configuration would make admission impossible.
- Current admission is conservative: it uses current real spare capacity only.
- Current admission must not count theoretical reclaimable threads from already-running workers as available capacity.

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
