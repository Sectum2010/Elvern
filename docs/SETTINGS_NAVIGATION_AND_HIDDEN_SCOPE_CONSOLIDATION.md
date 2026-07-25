# Settings Navigation and Hidden Scope Consolidation

## Settings information architecture

The canonical Settings sections are:

1. Preferences
2. Display
3. Libraries
4. Install
5. Advanced

Hidden lists are sibling cards under **Settings > Libraries**. **Hidden for me**
is available to every signed-in user. **Hidden for everyone** remains admin-only.
They follow Shared Libraries and precede Google Drive OAuth Setup.

The Install feature has one implementation under
`frontend/src/features/install/InstallSettingsPanel.jsx`. It mounts only while
the Install section is active, preserving its existing request cancellation,
resume, platform detection, Helper verification, and store-link behavior.

## Canonical navigation

The URL section is authoritative when it contains a valid Settings section.
Without a section parameter, Settings falls back to
`elvern:settings-active-section`, then to Preferences.

The historical `hidden` section is an input-only alias for `libraries`.
`?section=hidden` is replaced with `?section=libraries` while preserving other
query parameters, the hash, and router state. A stored `hidden` value is migrated
once to `libraries`.

The formal Install URL is `/settings?section=install`. The protected legacy
paths `/install` and `/desktop` remain permanent replace-redirects that preserve
their other query parameters, hash, and router state. They do not mount the
application shell before redirecting.

## Hidden scope transaction

Admins set a final hidden scope with:

```http
PUT /api/admin/hidden-items/{item_id}/scope
Content-Type: application/json

{"target_scope": "global"}
```

`target_scope` accepts only `global` or `personal`. One SQLite transaction:

- loads the media item and computes its existing stable movie identity once;
- creates both item-ID and stable-movie-key records in the target scope;
- removes both records from the source scope for the acting admin;
- leaves other users' personal hidden records unchanged;
- writes one audit event when database truth changes; and
- commits the existing Library revision triggers with the same transaction.

The operation is idempotent. If the exact requested state already exists,
`changed` is false, the authoritative `hidden_at` is retained, and no duplicate
audit or revision change is created.

## Atomic audit and rollback

Hidden primitives accept an existing SQLite connection and never commit.
The owning service commits once. Audit retention cleanup and insertion use that
same connection. A failure during target insertion, source deletion, or audit
insertion rolls back hidden state, audit state, and revision counters together.

The existing global hide/show endpoints keep their URLs, messages, and audit
action names while gaining the same audit atomicity.

## Network reconciliation

The Settings client sends one scope PUT. A definite HTTP 4xx/5xx or abort is
reported directly. A transport failure, or an unreadable successful response,
has an uncertain outcome, so the client re-reads the personal list and, for an
admin, the global list. It reports success only when those authoritative lists
confirm the requested final scope. No compensating reverse mutation is sent.

Hidden list refreshes are guarded by mounted component, authenticated identity,
and request generation. A late response from an old identity cannot replace the
current user's state.

## Rollback

The patch can be reverted as one source change. The old routes remain compatible,
and the existing personal and global hide/show endpoints remain available, so
rollback requires no database migration or data conversion.

## Preserved systems

This consolidation does not change Library list/search APIs, v1/v2 summaries,
poster indexing or derivatives, poster quality, smart loading, Library return
restoration, viewport/orientation recovery, playback, audio, subtitles, offline
recovery, Service Worker behavior, or desktop Helper packaging and installers.

## Validation

The implementation was validated with:

- focused frontend tests: 7 files and 135 tests passed;
- focused backend hidden-scope, API smoke, and Library revision tests:
  92 tests passed;
- complete frontend Vitest suite: 81 files and 1,111 tests passed;
- complete backend pytest suite: 1,949 tests passed;
- frontend production build: passed; and
- `git diff --check`: passed.

`./scripts/elvern-ci-local.sh --fresh` is the mandatory final gate. Its exact
result is reported with the completed working-tree patch.
