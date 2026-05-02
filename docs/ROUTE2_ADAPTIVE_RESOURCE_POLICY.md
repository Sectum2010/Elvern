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
- Elvern-owned ffmpeg/ffprobe helpers are internal Elvern workload, not non-Elvern external workload.
- The external ffmpeg detector must distinguish known Route2 workers, Elvern-owned helper children, and true external ffmpeg/ffprobe processes.
- When measurable, Elvern-owned helper CPU should be subtracted from the external CPU residual so helper probes do not look like outside workload.
- The detector must not read, expose, or log full command lines because media paths and provider URLs may be private.
- High host CPU caused primarily by Route2 itself must not be misclassified as external pressure.
- Elvern must never kill, pause, renice, throttle, or modify non-Elvern processes.

## Real Adaptive Thread Control Flags

- Real adaptive thread control is now wired behind feature flags and remains disabled by default.
- `ELVERN_ROUTE2_ADAPTIVE_THREAD_CONTROL_ENABLED` defaults to `false`. When it is false, real `assigned_threads` must use the existing fixed dispatch calculation.
- `ELVERN_ROUTE2_ADAPTIVE_THREAD_CONTROL_LOCAL_ONLY` defaults to `true`. The first real-control phase is local-only.
- `ELVERN_ROUTE2_ADAPTIVE_THREAD_CONTROL_CLOUD_ENABLED` defaults to `false`. Cloud real adaptive assignment remains deferred.
- `ELVERN_ROUTE2_ADAPTIVE_THREAD_CONTROL_STRICT_12_ENABLED` defaults to `false`. Real strict-12 assignment is not implemented in the first phase.
- If the global flag is enabled, the only real adaptive assignment currently allowed is an initial local 6-thread spawn after mature telemetry, no external pressure, no external ffmpeg, RAM safety, active playback health, user/global headroom, protected-floor capacity, and adaptive ceiling checks all pass.
- The initial 6-thread boost is gated on a single active Route2 playback workload, not merely a single account. Standard users usually map one-to-one with workloads because they are limited to one active playback, but an admin running multiple playbacks is running multiple Route2 workloads.
- Admin multi-playback remains allowed when capacity permits. Only the first admin playback is eligible for the single-workload initial 6 boost; additional admin playbacks fall back to the conservative fixed assignment in this phase.
- Real 9-thread and 12-thread tiers remain future phases. The benchmark-informed 4/6/9/strict-12 ladder is still available to shadow/runtime recommendations, but real assignment currently supports only the initial local 6 path.
- Cloud, stale telemetry, pressure, RAM, active-stream protection, exceptions, or unsupported targets must fall back to the fixed assignment instead of failing playback.
- No reclaim/downshift is implemented. The dispatcher must not count theoretical reclaimable capacity as current capacity.

## Phase 1J-1A Bad-Condition Reserve Instrumentation

- Full Playback's normal healthy startup target remains `120s`; this phase does not change the Full attach/readiness gate.
- Lite Playback behavior is unchanged in this phase, including the existing 45s slow-start path and pre-existing 15s fast-start path.
- The future Full bad-condition contract is `target_position_seconds + 30 minutes` of actual contiguous published Route2 content, bounded by media duration.
- The 30-minute reserve must be satisfied by the published frontier; projected goodput or ETA estimates must not mark the reserve as satisfied.
- Phase 1J-1A only exposes reserve status and runway-delta instrumentation in admin/status payloads. It does not enforce the reserve in startup readiness or admission.
- The reserve status uses the epoch attach/target position as the reserve start so the first reserve window for a Full epoch is stable while the user watches.
- Mature Full supply below `1.05x` is a bad-condition signal; mature supply below `1.0x` is marked as a stronger bad condition. Immature samples must report immature rather than false-trigger the reserve.
- Background and paused users remain protected. A background/preparing Full session under bad conditions should not be considered a donor until its future reserve contract is satisfied and ongoing health is safe.
- Active in-memory Route2 session directories and queued/running/stopping Route2 worker session directories must be excluded from orphan cleanup, even if the parent session directory mtime is old while nested epoch/published files are being written.
- Explicitly stopped or expired orphan session directories may still be cleaned according to policy.
- Backend restart recovery is not guaranteed yet. A future durable guarantee needs session-level metadata for user id, media item id, profile, playback mode, source fingerprint, cache key, active epoch id, reserve state, explicit stop state, expiry, and last activity.

## Current State

- Real adaptive control remains disabled by default, so `assigned_threads` remains controlled by the fixed Route2 dispatch path unless an operator explicitly enables the new flag.
- Adaptive spawn/runtime decisions remain dry-run or shadow-only in default configuration.
- Reclaim/downshift is not implemented.
- Admission failures use structured machine-readable codes such as `same_user_active_playback_limit` and `server_max_capacity`.

## Worker Count Config Semantics

- `max_concurrent_mobile_workers` is not currently a hard cap on active Route2 workers.
- Current Route2 admission is governed by CPU budget, the protected 2-thread active-user floor, active playback health, RAM pressure, external host pressure, and provider/source guards.
- Do not reinterpret `max_concurrent_mobile_workers` as a Route2 hard cap without an explicit product decision.
- If a hard Route2 worker-count cap is needed later, add a separate clearly named setting such as `ELVERN_ROUTE2_MAX_ACTIVE_WORKERS`.
