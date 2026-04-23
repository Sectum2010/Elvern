# Local Library Source Model Plan

This document is a repo-aware research and design pass based on the current Elvern tree as inspected on 2026-04-22.

It is intentionally narrow and practical:

- it is about replacing the current label-only Media Library Reference idea with a real local library source model
- it does not propose a full multi-library storage platform
- it keeps the current privacy rule that admin should not automatically see standard users' private local library paths
- it does not claim any of the proposed model is implemented yet unless the current repo already proves it

Assumptions:

- current implementation references below are based on files present in this repo today
- the current repo already has a label-only Media Library Reference setting path, but it does not affect scan, storage, playback, or visibility behavior
- the current repo already has an ownership/shared-source model for cloud libraries that can inform the local-source design

## 1. Current Local Scan / Index Model

### Current repo observations

- `backend/app/config.py` still treats local media as one global path:
  - `ELVERN_MEDIA_ROOT`
  - `settings.media_root`
- `backend/app/media_scan.py` scans that one root with `media_root.rglob("*")`.
- The current local scanner writes local items as:
  - `source_kind = 'local'`
  - no local-source-specific record
  - `library_source_id` is not populated for local rows
- `media_items.file_path` is globally unique in `backend/app/db.py`, which assumes one canonical server-visible path per indexed item.
- `backend/app/services/scan_service.py` has one local-library freshness snapshot and one local-library freshness probe state, both keyed globally in `app_settings`.
- `backend/app/main.py` starts one global local scan service and, when enabled, performs one startup scan decision for that single local root.

### Proposed v1 direction

- Replace the current single-root mental model with a source model:
  - one shared local source managed by admin
  - at most one private local source per standard user
- Keep scan/index storage source-tagged:
  - every local media row should belong to a concrete local source
  - local rows should no longer be treated as anonymous "anything under the one global root"
- Preserve the idea that local scan remains server-visible filesystem scanning, but make it source-aware rather than root-global.

### Risks / open questions

- The current local index has legacy rows that are implicitly global. A future migration/backfill plan is needed.
- `media_items.file_path` is globally unique today. That may still be acceptable if the same real file cannot belong to two different sources, but the model should decide whether source identity or path identity is the primary uniqueness boundary.
- The scanner and freshness gate are currently cheaper because they only track one root. A source-aware model will need to preserve that discipline per source.

### Recommended first implementation PR

- Backend data-model prep PR:
  - introduce a real local-source concept and source-tagging plan for local rows
  - do not change query visibility yet
  - define how current single-root local rows backfill into the future shared source

## 2. Visibility and Ownership Boundaries

### Current repo observations

- Cloud sources already have an ownership/shared model:
  - `library_sources.owner_user_id`
  - `library_sources.is_shared`
  - `user_hidden_library_sources`
- `backend/app/services/cloud_library_source_service.py` already filters visible cloud sources as:
  - owner sees own sources
  - everyone sees shared sources
- Local media does not follow that rule yet.
- `backend/app/services/library_service.py` `_base_query()` currently treats local rows as universally visible:
  - `COALESCE(m.source_kind, 'local') = 'local'`
  - only cloud-like rows go through source ownership checks
- The same "local bypass" pattern appears in:
  - `backend/app/services/library_hidden_service.py`
  - `backend/app/services/cloud_stream_access_service.py`
  - related playback/stream access helpers that assume local means globally accessible under the one root
- `backend/app/services/library_service.py::get_media_item_detail()` returns `file_path` in the payload today.

### Proposed v1 direction

- Local sources should adopt explicit ownership and scope semantics:
  - shared local source: visible to everyone
  - private local source: visible only to the owning standard user
- Admin should not receive implicit "super-visibility" into private local sources in normal product flows.
- API and UI access should follow the same visibility rule:
  - library listing
  - search
  - detail access
  - stream/playback access
  - hidden-items flows

### Risks / open questions

- The current local bypass is deep enough that source-aware visibility has to reach more than just library listing.
- Returning raw `file_path` to the frontend becomes much more sensitive once local paths can be private and user-specific.
- Admin operational tooling may be tempted to reintroduce a broad "see everything" shortcut. That should be treated as a separate later policy decision, not the default v1 model.

### Recommended first implementation PR

- Access-control refactor PR:
  - remove unconditional local visibility bypasses once local rows are source-tagged
  - make local item visibility follow explicit source ownership/shared rules
  - keep admin scoped to shared local source only

## 3. Source Model Design

### Current repo observations

- The repo already has a generic-enough source anchor in `media_items.library_source_id`.
- The repo already has a `library_sources` table with:
  - `owner_user_id`
  - `provider`
  - `display_name`
  - `is_shared`
- The current `library_sources` table is cloud-biased:
  - `google_drive_account_id`
  - `resource_type`
  - `resource_id`
- Local media currently bypasses the table entirely.

### Proposed v1 direction

- Prefer extending the existing source model rather than inventing a second parallel ownership system.
- Minimal local source shape for v1:
  - `source id`
  - `provider = 'local_filesystem'` or similarly explicit local provider type
  - `scope = shared/private` or reuse `is_shared`
  - `owner_user_id`
    - admin for shared source
    - standard user for private source
  - `real path`
  - `display label` if useful
  - `validation state`
  - `allowed root id` or equivalent link back to the allowlist that approved it
- Product constraint for v1:
  - exactly one shared local source
  - at most one private local source per standard user

### Risks / open questions

- Reusing `library_sources` is the smallest conceptual fit, but it requires adding local-path-specific columns rather than overloading cloud-only fields.
- A separate `local_library_sources` table would isolate concerns better, but it would duplicate source visibility concepts the repo already has.
- The model should decide whether private local sources may have a user-facing label separate from the real path in v1, or whether path plus derived folder name is enough.

### Recommended first implementation PR

- Schema/model PR:
  - extend the existing source model to represent local filesystem sources directly
  - keep the v1 constraint of one shared local source and one private local source per standard user
  - avoid multi-source-per-user complexity for now

## 4. Allowed Roots / Safe Path Selection

### Current repo observations

- The repo currently validates poster directories and media root paths via normalized local paths and existence checks in `backend/app/services/app_settings_service.py` and `backend/app/config.py`.
- There is no current server-side browse/select flow for local library path selection.
- Standard users currently have no path-selection capability at all for local storage.
- The only fully configured local path concept today is the global `settings.media_root`.

### Proposed v1 direction

- Add an explicit admin-managed allowlist of server-visible roots for user private local sources.
- Standard-user private paths must be chosen only from that allowlist.
- Safe validation rules for any local source path:
  - normalize to realpath
  - path must exist
  - path must be a directory
  - path must remain under one allowlisted root after normalization
  - path should be readable by the Elvern server process
  - symlink escapes must be rejected after realpath resolution
- Safe browse/select model:
  - admin may browse/select the shared local source path
  - standard users may browse/select only within the already allowlisted roots
  - no unrestricted filesystem browsing for standard users

Recommended allow-root metadata for v1:

- allow-root id
- normalized root path
- enabled/disabled status
- optional admin label
- created/updated timestamps

### Risks / open questions

- NAS and Docker mount setups may make "readable now" checks flaky if the mount is temporarily unavailable.
- A browse API can accidentally become a filesystem discovery surface if it is not tightly scoped.
- The plan should decide whether standard users can type a path manually inside an allowlisted root, or must choose only from a server-provided browse flow.

### Recommended first implementation PR

- Admin allow-roots PR:
  - add allowlisted-root persistence and validation service
  - add admin-only management for those roots
  - no private source selection yet

## 5. Query / Library Composition Model

### Current repo observations

- `backend/app/services/library_service.py` already composes user-visible results from:
  - all local rows
  - owned cloud rows
  - shared cloud rows
- Search, detail, hidden-item views, and stream access all follow the same "local is globally visible" assumption.
- Dedupe and continue-watching logic already operate on the user-visible row set after visibility filtering.
- `library_source_id` is already attached to cloud rows and carried through serialization.

### Proposed v1 direction

- Effective visible library set:
  - admin: shared local source only
  - standard user: shared local source + own private local source
- Query logic should stop treating local as a universal visibility shortcut.
- Source ownership needs to be attached to indexed local items so every consumer can answer:
  - which source does this item belong to?
  - is it shared or private?
  - if private, who owns it?

Effect on main product surfaces:

- library listing:
  - standard users see union of shared + own private
  - admin sees shared only
- search:
  - same visibility boundary as library listing
- detail page:
  - only accessible if the current user can see the source
- continue watching:
  - should continue to be per user, but against the visible union
- hidden movies:
  - existing personal/global hide logic can continue, but source-aware visibility has to happen before hide/dedupe logic
- dedupe / quality selection:
  - should compare visible shared + private candidates for the current user only

### Risks / open questions

- If the same movie exists in both shared and private local sources, dedupe policy must decide which copy becomes the visible representative.
- Continuing to expose raw `file_path` in the detail payload may be inappropriate once path ownership becomes private.
- Query refactors must not accidentally let admin see private-source rows just because admin has a higher role.

### Recommended first implementation PR

- Query composition PR:
  - make local queries source-aware
  - carry local source metadata through serialization
  - keep result composition as shared-only for admin, shared-plus-own-private for standard users

## 6. Scan Strategy Implications

### Current repo observations

- Local scan is currently one global job over `settings.media_root`.
- Startup and opportunistic freshness checks are currently global:
  - one snapshot
  - one cooldown/probe state
- `/api/library/rescan` currently means:
  - admin: real scan
  - standard user: only "Recent Watched" refresh
- Cloud sync is already source-based, but local scan is not.

### Proposed v1 direction

- Treat each local source as a separate scan unit.
- Source-aware local scanning should support:
  - one shared source scan
  - one private-source scan per user who configured one
- Indexed local rows should be tagged with their owning source.
- Freshness checks should eventually become per source, not one global local snapshot.
- Manual rescan direction for v1:
  - admin manual rescan: shared local source
  - standard user manual refresh: own private local source only, if configured

Recommended future scan state additions:

- scan job optionally scoped to `library_source_id`
- per-source freshness snapshot
- per-source last-successful-scan summary

### Risks / open questions

- Per-source scan jobs complicate the current simple "scan_in_progress" boolean.
- Startup freshness for many private sources raises a policy question:
  - should Elvern check all private sources at startup?
  - or only opportunistically when the owner logs in?
- If a private source is temporarily unavailable, Elvern should avoid turning that into a broad library outage.

### Recommended first implementation PR

- Source-aware scan foundation PR:
  - scan one local source at a time
  - write `library_source_id` for local rows
  - keep global single-flight discipline, but make job input source-specific

## 7. Settings / UI Model

### Current repo observations

- `frontend/src/pages/SettingsPage.jsx` already has:
  - admin-only settings sections
  - standard-user settings sections
  - the current label-only Media Library Reference split between shared default and private override
- The current UI already explains:
  - admin shared default
  - user private override
  - fallback to shared default when private is blank
- `frontend/src/pages/DetailPage.jsx` Info modal now surfaces the same label-only reference split for the current user.

### Proposed v1 direction

- Evolve the existing UI shape instead of replacing it.

Admin settings should grow into:

- shared local library path selector
- allowed-roots management
- optional shared display label if still useful

Standard-user settings should grow into:

- private local library path selector constrained to allowed roots
- visible display of:
  - shared local path/reference
  - own private local path/reference
  - effective composition note

User-facing presentation rules:

- standard users should see both shared and private local path state at the same time
- private may be empty
- "using now" later means shared + private union, not one collapsed label

### Risks / open questions

- The current label-only reference copy will become misleading once the path model exists unless the settings text is updated carefully.
- A path picker/browse UI can become noisy quickly if it tries to expose too much filesystem detail.
- The detail page and library page should eventually show source composition in user-readable terms, but not by dumping raw server paths everywhere.

### Recommended first implementation PR

- Settings conversion PR:
  - replace label-only path/reference controls with real shared/private local-source configuration
  - keep the existing split presentation style, but bind it to real sources

## 8. Privacy and Security

### Current repo observations

- Current repo behavior already allows admins to manage many global things, but the requested product rule here is stricter:
  - admin should not automatically see standard users' private local library paths
- Current local model does not enforce this because local is not source-owned yet.
- Several current local access paths would leak too much in a private-path world:
  - unconditional local visibility in list/search/detail
  - stream access based on root-only checks
  - detail payloads that include raw `file_path`

### Proposed v1 direction

- Treat private local paths as private account-level data, not just operational config.
- Explicit privacy boundary:
  - no admin "list all private paths" screen
  - no admin lookup of another user's private local path
  - no cross-user local-path lookup
  - no audit-log or status payload that casually includes other users' private paths
- Why this differs from typical admin-all-sees-everything systems:
  - local filesystem paths can reveal personal folder names, naming conventions, mount layout, and household structure
  - Elvern's product direction here is shared library plus private additions, not centralized surveillance of every user's server path choice

Implementation safety principles:

- path validation should happen server-side
- visibility enforcement should happen server-side
- UI should only ever render current-user or shared path data
- raw private paths should not be serialized into unrelated admin/system payloads

### Risks / open questions

- Support/debug workflows may pressure the design toward admin override visibility; that should require an explicit later policy decision if ever added.
- Audit logging needs a careful policy:
  - log source id / action type
  - avoid logging raw private path values broadly
- Future export/backup features will need to preserve the same privacy rule.

### Recommended first implementation PR

- Privacy boundary PR:
  - define and enforce non-disclosure rules for private local source paths at API, UI, and audit-log boundaries
  - pair this with source-aware local visibility, not as a separate afterthought

## 9. Future Topic: User's Own Computer Local Path

### Current repo observations

- Current local path handling assumes the Elvern server can see the filesystem directly.
- Desktop helper and external-player flows already exist, but they are about playback handoff, not library path discovery or indexing from a user's own computer.

### Proposed v1 direction

- Treat "user's own computer local path" as a separate later architecture topic, not part of the initial shared-plus-private server-visible path model.

Likely later options:

- server-visible mounted or network-shared path
- desktop agent/helper model that can expose or validate a local library on the user's machine
- sync/upload model where the server imports content instead of indexing the user's live filesystem directly

### Risks / open questions

- This topic can easily collapse into remote filesystem access, desktop-agent trust, sync policy, or upload architecture. It should stay explicitly out of the first allowed-roots implementation.
- The future model would need a very different threat model than the current server-visible filesystem design.

### Recommended first implementation PR

- No implementation PR in the first wave.
- Keep this as a later architecture discussion after the server-visible shared/private local-source model is stable.

## Recommended PR Sequence

1. Local source data-model PR.
   - extend the current source model so local filesystem sources are first-class and source-tagged
   - preserve one shared source and at most one private source per standard user
2. Allowed-roots and validation PR.
   - admin-managed allowlisted roots
   - secure normalized-path validation helpers
3. Shared local source settings PR.
   - admin configures the shared real local path
   - migrate current single-root deployments toward an explicit shared source
4. Private local source settings PR.
   - standard users configure their own private path only under allowed roots
   - no admin readback of those private paths
5. Source-aware local query/access PR.
   - library, search, detail, stream, and hidden-item paths respect shared vs owner-private visibility
   - remove unconditional local visibility bypasses
6. Source-aware local scan PR.
   - scan local sources individually
   - write `library_source_id` for local rows
   - define admin shared rescan and user private rescan behavior
7. Per-source freshness and operational polish PR.
   - per-source freshness snapshots
   - per-source opportunistic checks
   - clearer status/reporting that still preserves private-path privacy

