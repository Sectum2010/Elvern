# Playback Regression Notes

This file is a living project memory for difficult Elvern playback and platform regressions. It is not a changelog. Add entries only for issues that were hard to diagnose, high-risk to regress, or dependent on live-device evidence.

Future Codex rule: when a playback/platform bug takes real diagnostics, live-device evidence, or disproves an early hypothesis, update this file with the evidence, root cause, fix, and regression guards before closing the task.

Each entry should preserve:

- Status.
- Affected platforms.
- Symptoms.
- Wrong or incomplete hypotheses.
- Evidence that identified the real cause.
- Real root cause.
- Correct fix.
- Regression guards.
- Do not regress.

## Fake Audio 1 / Fallback Track Discovery Regression

### Status
Fixed for local trusted probe metadata and guarded for cloud/provider failures. Cloud multi-track discovery is provider-aware through the same local Elvern stream URL path used by playback probes; keep mocked provider probe tests before claiming broader provider behavior.

### Affected Platforms
iPhone/PWA Browser Playback, cloud Browser Playback, local Route2 Browser Playback.

### Symptoms
- The audio menu could show only fake Audio 1 / Default audio.
- Real multi-track files could collapse to one fallback track.
- Cloud files could lack trusted probe metadata and still look like they had a switchable default audio stream.

### Wrong Or Incomplete Hypotheses
- Native browser `audioTracks` are enough for Browser Playback track authority.
- `media_items.audio_codec` is a real switchable audio track.
- Local-only ffprobe coverage solves cloud track discovery.

### Real Root Cause
Fallback metadata from the media row was allowed to masquerade as Browser Playback audio authority. Browser/native/HLS single-track exposure could also replace the backend ffprobe track list, producing fake Audio 1 behavior. Cloud probing did not have an explicit provider-aware trusted probe path with honest failure diagnostics.

### Correct Fix
Only `raw_probe_summary_json` tracks from `probe_status = probed` are switchable Browser Playback audio tracks. `media_row_fallback` remains diagnostic/coarse metadata only. Cloud track probing uses the provider-authenticated Elvern stream URL path with ffprobe network options, and provider/auth failures are surfaced as scan unavailable/reconnect-required states instead of fake audio tracks.

### Regression Guards
- Backend diagnostics tests prove fallback audio is diagnostic, trusted raw-probe audio keeps global ffprobe stream indexes, cloud provider URL probing stores raw tracks, and provider auth failure does not create fake Audio 1.
- Frontend tests prove Route2 uses trusted backend audio over native Audio 1, filters `media_row_fallback`, hides commentary, preserves distinct codec/channel names, and keeps the Phase A rectangular phone menu path.
- Cloud mocked provider probe tests are required before claiming cloud multi-track discovery solved.

### Do Not Regress
- Never show `media_row_fallback` as switchable audio.
- Never let native browser/HLS `Audio 1` replace trusted backend tracks for Route2 Browser Playback.
- Never claim cloud multi-track solved without provider-aware probe tests.
- Keep Phase A phone menu UI/scroll behavior intact.

### Phase B.1 Addendum: Audio Switch Pending State

Bug: after real audio tracks were discovered correctly, clicking an unselected backend audio row could look like nothing happened.

Root cause: the `/audio` response was not explicitly synced into the frontend mobile session state at the selection call site, and local row-level pending UI could be cleared before the backend active/pending snapshot was visible.

Correct fix: sync the accepted `/audio` payload immediately, keep the old active row selected while the new row is pending, and move the selected highlight only when backend `active_audio_stream_index` changes. Current audio/video keeps playing while the replacement audio epoch prepares.

Do not regress: do not add fake local selected state for backend audio, do not clear pending before backend active/pending state is reflected, and do not stop current playback while audio replacement prepares.

### Phase B.2 Addendum: Audio Switch Completion / Failure Convergence

Bug: audio track switching could remain stuck in a pending spinner forever after the replacement epoch failed, ended before attach-ready, or was superseded by another recovery.

Root cause: audio replacement failure/discard paths did not reliably clear `pending_audio_stream_index` or mark the switch failed, and the frontend could keep a local pending spinner after backend state had stopped supporting that pending row.

Correct fix: audio switching is a finite state machine: `preparing` must resolve to either `active` or `failed`. Failed or superseded audio replacements clear pending, keep the old active stream selected, and expose a short switch error while current playback continues.

Do not regress: never leave `pending_audio_stream_index` pointing to a failed/discarded replacement, never keep a local pending spinner after backend failed or cleared pending, and never stop current playback while the audio replacement prepares.

### Phase B.2 Addendum: Stale Failure Must Not Clear A New Audio Request

Bug: the Phase B.2 failure cleanup could erase the row-level spinner for a new audio click when the existing session snapshot still carried an older `audio_switch_state = "failed"`.

Root cause: the frontend treated stale global audio switch failure as the current request failure. That collapsed request-local pending before the `/audio` response or matching backend pending/active/failed state arrived.

Correct fix: audio pending is split into request-local state and backend snapshot state. A stale failed snapshot cannot clear a new request spinner; only the matched `/audio` response or matching backend pending/active/failed stream can take over or clear it.

Do not regress: an old audio failure must never erase a newly clicked backend audio row spinner.

### Phase B.2.1 Addendum: Audio Pending Spinner / Highlight Visibility

Bug: after Phase B.2, clicking an unselected backend audio row could lose the visible pending spinner/color, making the click look like a no-op even though audio discovery was correct.

Root cause: frontend pending state could still be cleared by stale backend session payload or by a missing/unaccepted `/audio` response, and the pending row styling was too weak to be a reliable visible state.

Correct fix: use request-local visual pending state keyed to the clicked track/stream, let only matching response/backend pending/active/failed state clear it, and give `.elvern-overlay__track-menu-item--pending` a visible tinted background plus spinner styling.

Do not regress: a clicked backend audio row must immediately show spinner plus pending highlight until the matching backend preparing/active/failed response resolves it.

### Phase B.3 Addendum: Real Audio Switch Convergence

Bug: audio rows were discovered and pending feedback appeared, but the actual replacement audio switch could still fail or stall behind a generic `Could not switch audio track` message.

Root causes: the frontend treated accepted-but-incomplete `/audio` responses as failures; the backend accepted selected stream indexes before proving they were trusted raw-probe audio streams; replacement readiness used the heavier generic attach gate; and ffmpeg/source failures were collapsed into generic audio errors.

Correct fix: accepted ambiguous `/audio` responses keep the clicked row pending until polling resolves them; selected streams are validated against trusted `raw_probe_summary_json`; audio-track replacements can promote after a 15s audio-specific ready runway; and `audio_switch_error` carries the short backend/ffmpeg/source reason while the old active playback stays alive.

Do not regress: do not show fake generic failure without backend failed/error state, do not create replacements for invalid or fallback stream indexes, do not change global Lite startup thresholds, and do not stop the old active playback while selected audio prepares.

### Phase B.5 Addendum: Galaxy Item Audio Switch Attach And Error State

Bug: on the user-tested `The Super Mario Galaxy Movie` item (`media_item_id=1424`), audio discovery showed the real tracks, but live UI could still look failed or inconclusive after clicking a track.

Evidence: item-specific Route2 mobile audio-switch tests found trusted raw-probe audio streams at ffprobe global indexes `1`, `2`, `3`, and `4`. Switching through `/api/mobile-playback/sessions/{session_id}/audio` prepared and promoted target streams `2`, `3`, and `4`; the replacement snapshots carried maps `0:2?`, `0:3?`, and `0:4?`, and `active_audio_stream_index` changed after the audio-specific runway. The API-only run had no browser client to acknowledge the new `attach_revision`, so `client_attach_revision` stayed behind even after backend promotion.

Real root cause category: backend replacement/promotion was working for this item. The remaining regression was client/UI convergence: backend active stream change alone was treated as final success, failed/unusable options only showed generic text, and the audio button/row did not preserve a clear red failed-option state.

Correct fix: keep the old active row selected while an audio-switch promotion waits for client attach acknowledgement, keep the clicked row pending until `client_attach_revision` catches up to `attach_revision`, and mark explicit backend failures with a red row plus red audio button until the user chooses a usable/current track.

Do not regress: backend active stream change is not enough proof of client switch; the client attach must be acknowledged. Failed options must be visibly red and not selected. Stream indexes must remain ffprobe global indexes, the mobile audio route must stay covered, and old playback must continue while the selected audio replacement prepares.

### Plan B Addendum: Verified Audio Switch Attach

Bug: real iPhone/PWA testing showed that backend `active_audio_stream_index` changing was not enough proof that Safari's native-HLS media pipeline had actually loaded the new audio-switch manifest.

Evidence: on item `1424`, backend audio switching promoted stream `2` and changed `active_epoch_id`/`attach_revision`, but a browser-side trace showed a gap where backend was active on the new stream while the video element still held the old blob/source. A generic heartbeat could carry the pending `client_attach_revision` without proving it came after the new audio-switch source loaded.

Real root cause: Route2 attach acknowledgement was revision-based rather than audio-switch-source-based. The client could acknowledge a new attach revision from generic heartbeat/video paths before the expected replacement epoch manifest had produced a fresh `loadedmetadata`/`loadeddata`/`canplay` event.

Correct fix: audio-switch promotion now creates an explicit expected attach record: target stream, expected attach revision, expected active epoch, and expected manifest URL. Normal heartbeats cannot acknowledge that revision. The hook force-attaches the replacement manifest, waits for the expected source/epoch/revision to be set, waits for a load/canplay event from that attach, and only then sends `client_attach_revision`.

Do not regress: never treat backend active audio as final client success, never send audio-switch attach ack before the expected source loads, never mark the new row selected before `client_attach_revision >= attach_revision`, and do not route ordinary native-HLS window slides through this special audio-switch reattach path.

## macOS / Windows Browser HLS Scrubber Regression

### Status
Fixed.

### Affected Platforms
Windows desktop/laptop browser Web/HLS, macOS desktop/laptop browser Web/HLS, Ubuntu/Linux browser Web/HLS as the working comparison baseline.

### Symptoms
Ubuntu/Linux browser HLS scrubber worked correctly. Windows and macOS browser HLS scrubbers did not work correctly, even though they were using the same Route2 sessionized HLS family.

### Wrong Or Incomplete Hypotheses
The open `EVENT` manifest without `#EXT-X-ENDLIST` was initially suspected as the whole problem. That was incomplete: Linux used the same open `EVENT` manifest and still exposed a finite local seekable window.

### Evidence That Identified The Real Cause
Linux diagnostic baseline:

- `selectedEngine = hls.js`
- `currentSrc = blob:`
- Native HLS support was false.
- Manifest was `EVENT` / open with no `#EXT-X-ENDLIST`.
- `video.duration` was finite.
- `seekable` was finite, `0 -> local window duration`.

Windows broken diagnostic:

- `selectedEngine = native_hls`
- `video.canPlayType("application/vnd.apple.mpegurl") = "maybe"`
- `Hls.isSupported() = true`
- `currentSrc = direct .m3u8 URL`
- `video.duration = Infinity`
- `seekable.length = 0`
- Manifest was the same `EVENT` / open shape with no `#EXT-X-ENDLIST`.

### Real Root Cause
The frontend HLS attach path checked native HLS support before hls.js support. Windows/Edge returned `"maybe"` for the HLS MIME type, so Elvern chose `native_hls` instead of hls.js. Windows native HLS interpreted the open Route2 `EVENT` manifest as live/unseekable, while hls.js/MediaSource exposed a finite local window.

### Correct Fix
For desktop/laptop browser HLS playback, prefer hls.js when `Hls.isSupported()` is true. Keep iPhone/iPad native HLS behavior unchanged. Keep macOS Safari native HLS fallback when hls.js is unavailable.

### Regression Guards
Keep `frontend/src/lib/browserHlsEngine.test.js`. The policy must continue to cover:

- Windows desktop with native `"maybe"` and hls.js supported resolves to `hls.js`.
- Linux desktop resolves to `hls.js`.
- macOS Chromium-style desktop browsers resolve to `hls.js` when supported.
- macOS Safari can still resolve to `native_hls` when hls.js is unsupported.
- iPhone/iPad native behavior remains unchanged.

### Do Not Regress
- Do not reorder desktop engine selection back to native-first.
- Do not trust `canPlayType(...) = "maybe"` as proof that native HLS is the right Route2 engine.
- Do not change Route2 manifest generation to explain this specific bug; the winning fix was engine selection.
- Do not add full-movie desktop scrub bars to iPad/iPhone/mobile while protecting this path.

## macOS Fullscreen White-Edge Flashing

### Status
Fixed for the app fullscreen button path by live user confirmation. Native fullscreen should not be claimed fixed without separate live Mac confirmation.

### Affected Platforms
macOS desktop/laptop browser Web/HLS fullscreen.

### Symptoms
Mac Web/HLS fullscreen showed thin white flashing strips around the video edge.

### Wrong Or Incomplete Hypotheses
Repeated player-shell/video fullscreen CSS patches were tried first. They did not fix the live Mac behavior, which meant the problem was not solved by ordinary nested player CSS alone.

### Evidence That Identified The Real Cause
Live Mac testing showed the earlier CSS-only fixes did not change the symptom. A controlled app fullscreen path that fullscreens the black player shell was later confirmed by the user to stop the white flashing edge.

### Real Root Cause
The native/video fullscreen path could expose compositor or surrounding surface edges that the earlier nested CSS selectors did not reliably control. The app-managed fullscreen path made Elvern's black player shell the fullscreen surface instead.

### Correct Fix
Use the app fullscreen button path for macOS browser playback and keep the fullscreen surface controlled by Elvern's black player wrapper.

### Regression Guards
Keep the app fullscreen path and the platform routing tests that keep it Mac desktop only.

### Do Not Regress
- Do not remove the app fullscreen controlled path without live Mac validation.
- Do not claim native fullscreen is fixed unless live Mac confirms it.
- Do not treat a build pass as evidence for this bug; it required live Mac validation.

## iPad Platform Misclassification / Handoff Regressions

### Status
Fixed.

### Affected Platforms
iPadOS Safari, especially desktop-class Safari that reports Macintosh/MacIntel with touch points.

### Symptoms
iPad showed desktop helper UI. iPad VLC handoff could be routed through the wrong desktop/helper path instead of the iOS external-app path.

### Wrong Or Incomplete Hypotheses
The iPad was treated like a macOS desktop because iPadOS Safari can present desktop-like platform values.

### Evidence That Identified The Real Cause
Platform detection needed to classify iPadOS desktop-class Safari before macOS desktop. Tests now cover Macintosh/MacIntel plus touch points as iPad.

### Real Root Cause
Platform detection and route selection did not consistently make iPad-first decisions before desktop macOS decisions.

### Correct Fix
Classify iPad before macOS desktop and route iPad/iPhone through iOS external app handoff. Do not show desktop helper UI or Mac-only playback bars on iPad.

### Regression Guards
Keep `frontend/src/lib/platformDetection.test.js` and `frontend/src/lib/playbackRouting.test.js`.

### Do Not Regress
- iPad must not show desktop helper.
- iPad must not route VLC through desktop helper.
- iPad must not receive Mac-only fullscreen/absolute scrub UI.
- iPhone/mobile/cellular must not receive desktop-only scrub UI.

## iPhone Custom Player Inline Layout / Tap Interception

### Status
Fixed with CSS regression guards.

### Affected Platforms
iPhone browser/PWA custom player on the default movie detail page, especially inline non-fullscreen mode.

### Symptoms
Inline iPhone playback could show the progress bar around the middle of the player instead of anchored to the bottom. In fullscreen the visual layout could look closer to correct, but taps on controls could flash the overlay instead of activating the button.

### Real Root Cause
The phone inline player surface relied on `height: 100%` inside an aspect-ratio shell. Mobile Safari can treat that percentage height as non-definite, so the absolute overlay anchored to the wrong box. Separately, the transparent full-surface tap target lived in the same grid as the real controls and could stack above them, intercepting taps meant for the timeline/fullscreen/buttons.

### Correct Fix
For phone inline custom playback, make `player-fullscreen-surface` absolutely fill the 16:9 shell (`position: absolute; inset: 0`) instead of relying on percentage height. Stack real overlay controls above the transparent tap target with explicit positioned z-index rules. Keep fullscreen/cinema selectors specific enough to override the inline 16:9 shell.

### Regression Guards
Keep `frontend/src/features/playback/ElvernPlayerOverlayCssGuards.test.js`. It checks that:

- Phone inline surface uses absolute inset fill, not `height: 100%`.
- Top and bottom controls stack above the full-surface tap target.
- Phone fullscreen selectors use `100dvh` and override the inline shell.

### Do Not Regress
- Do not reuse desktop player controls or desktop hit-target assumptions for iPhone.
- Do not let the transparent surface button sit above real controls.
- Do not rely on percentage-height sizing for the iPhone inline player surface.

## Logout Active Playback Warning

### Status
Fixed.

### Affected Platforms
All platforms with active playback/preparation during explicit logout.

### Symptoms
The active playback warning modal appeared on logout. Choosing Keep Preparing closed the modal but did not actually log the user out.

### Wrong Or Incomplete Hypotheses
Closing the modal was treated as enough. It was not; explicit logout still had to complete after the user's choice.

### Evidence That Identified The Real Cause
The Keep Preparing branch preserved preparation but skipped the real logout/navigation flow.

### Real Root Cause
The modal decision paths were not both routed through the actual logout flow.

### Correct Fix
Keep Preparing keeps the worker/preparation alive but logs the user out and routes to login. Terminate Process attempts to stop the worker, then logs out and routes to login even if stop fails.

### Regression Guards
Explicit logout choices must be tested as logout flows, not just modal close flows.

### Do Not Regress
- Keep Preparing must log out.
- Terminate Process must log out even if worker stop fails.
- Do not kill background/page-close preparation behavior when only explicit Logout behavior is in scope.

## Fake Install Detection

### Status
Fixed.

### Affected Platforms
iPhone/iPad iOS external apps and desktop helper install status surfaces.

### Symptoms
iOS app status could show Installed based on blur/pagehide/visibility heuristics. Safari could still show "Cannot open link" while those heuristics fired.

### Wrong Or Incomplete Hypotheses
Browser blur, pagehide, or visibility changes were treated as reliable install confirmation.

### Evidence That Identified The Real Cause
Live iPad testing showed Infuse was not installed, Safari showed failed scheme-open behavior, and Elvern still showed Installed due to stale or heuristic-based state.

### Real Root Cause
The install-status UI overtrusted browser lifecycle heuristics and stale localStorage state. Those signals do not prove that a third-party iOS app is installed.

### Correct Fix
Do not mark iOS VLC/Infuse as Installed without real confirmation. Downgrade failed or unverified attempts to honest states such as Not verified, Launch attempted, Could not verify open, or App may not be installed. Keep desktop helper verification separate from VLC launch verification.

### Regression Guards
Keep platform/install status behavior honest: no installed label without a real confirmation signal.

### Do Not Regress
- Do not reintroduce fake Installed labels from blur/pagehide/visibilitychange.
- Do not collapse helper installed, helper verified, VLC installed, and VLC launch worked into one state.
- Failed scheme-open must downgrade stale Installed state.

## Google Drive Cloud Playback Range / Provider Error Regression

### Status
Fixed with service-level tiny-range validation. Full live playback was not stress-tested as part of the fix.

### Affected Platforms
All cloud movie playback paths that depend on the Google Drive cloud stream proxy, including browser playback, Route2 source resolution, and native/VLC cloud stream handoff.

### Symptoms
Cloud movies could fail with Google Drive quota-looking errors even when the cloud item and bounded range reads were still accessible. The visible message could be "The download quota for this file has been exceeded."

### Wrong Or Incomplete Hypotheses
Treating this only as a real Google Drive quota problem was incomplete. Treating it only as frontend copy was also incomplete. Source probes that use small explicit ranges can pass while playback/proxy paths still fail if those paths make a different upstream request shape.

### Evidence That Identified The Real Cause
Tiny direct Google Drive metadata and bounded range probes worked for multiple cloud items. For one large cloud item, direct no-range media access returned Google Drive `downloadQuotaExceeded`, while direct bounded ranges such as `bytes=0-0` and `bytes=0-1048575` returned `206`. A follow-up live failure showed ffmpeg/VLC requesting the native stream with an explicit open-ended range, `bytes=0-`; preserving that request exactly still produced the same provider quota response.

The Elvern cloud stream path could forward no client `Range` or an explicit open-ended client `Range` as an unbounded Google Drive media request. Route2 source validation also used a no-range `HEAD` probe. Those request shapes can hit provider quota behavior even when bounded byte ranges are still usable.

### Real Root Cause
The production cloud proxy/source-validation path allowed unbounded or open-ended Google Drive media opens for large cloud files. Google Drive can reject those with quota errors even when small bounded byte ranges work. The first fix only handled missing `Range`; live ffmpeg/VLC could still send `Range: bytes=0-`, which remained open-ended upstream.

### Correct Fix
When the client supplies a bounded `Range`, preserve it exactly. When the client does not supply a `Range`, or supplies an open-ended range such as `bytes=N-`, satisfy the client stream through stitched bounded upstream Google Drive ranges instead of one unbounded full-file media request. Route2 source validation should probe cloud stream inputs with `Range: bytes=0-0`. Google Drive provider errors should keep structured source taxonomy, including `provider_quota_exceeded`, without mapping them to server capacity or same-user playback conflicts.

### Regression Guards
Keep tests that verify:

- Elvern forwards explicit client `Range` headers to Google Drive.
- Elvern does not forward open-ended `bytes=N-` ranges to Google Drive as open-ended upstream requests.
- Open-ended native/VLC/ffmpeg stream reads are stitched from bounded upstream Google Drive range windows.
- Elvern includes `supportsAllDrives=true`.
- Elvern includes `resourceKey` when present.
- Elvern cloud stream tiny range returns `206` when mocked Drive returns `206`.
- Missing client `Range` and open-ended client `Range` become bounded upstream range windows, not full-file upstream requests.
- Google Drive `downloadQuotaExceeded` maps to `provider_quota_exceeded`, not `provider_auth_required`.
- Provider/source errors are not mapped to `server_max_capacity` or `same_user_active_playback_limit`.
- Non-retryable provider/source errors do not create Route2 replacement loops.

### Do Not Regress
- Do not debug live cloud playback failures by running benchmark matrices first.
- Do not assume source-probe success proves playback path success; compare the exact proxy/request shape.
- Do not make unbounded Google Drive media requests for large cloud files from playback/proxy validation, including explicit `bytes=N-` client ranges.
- Do not log access tokens, refresh tokens, cookies, signed URLs, or full private provider URLs.
- Do not hide cloud provider/source errors as server busy or generic playback failures.

## Custom Browser Player Overlay Auto-Hide Regression

### Status
Fixed.

### Affected Platforms
Lite and Full browser playback on desktop/laptop, tablet, and phone when Elvern's custom player overlay is active.

### Symptoms
The duplicate lower-left play/pause button was visible beside the volume controls. After removing it, the control bar could still remain visible forever while video was playing. On touch devices, a tap could reveal the controls only for a moment, making the buttons nearly impossible to use. On desktop/laptop, the buttons could hide while the mouse cursor remained visible over the movie. Later live testing also showed Space could activate the focused fullscreen/minimize button instead of toggling playback, and iPhone fullscreen/cinema sizing could push controls into an unusable flash loop.

### Wrong Or Incomplete Hypotheses
Treating this as only a CSS opacity problem was incomplete. Treating the helper's pure visibility calculation as proof of live behavior was also incomplete, because the live component can be pinned visible by focus state, stale video bindings, and native video controls.

### Evidence That Identified The Real Cause
Live testing showed the controls stayed visible after the duplicate button was removed. The component was passing fake time values into the visibility helper, pointer-created focus was treated like keyboard focus, and the keyed `<video>` element can be replaced without changing the ref object. A mobile warmup path could also set `video.controls = true`, which conflicts with the custom overlay.

### Real Root Cause
The custom overlay visibility state was not tied tightly enough to real runtime state. Pointer/touch focus could pin `controlsFocused`, the overlay did not explicitly rebind when the keyed video node changed, native controls were not forcibly disabled by the custom overlay, and touch taps shared the desktop hide timer instead of using a touch-friendly reveal window. Space was not owned by the player overlay, so it could bubble into the focused fullscreen button's native activation path. Touch `pointerleave` was also treated like mouse leave, allowing iOS touch/fullscreen transitions to hide controls immediately after reveal. A later generic fullscreen CSS rule could override the phone-specific `100dvh` sizing with `100vh`.

### Correct Fix
Remove the redundant lower-left transport button and let the volume controls lead the bottom row. Keep keyboard focus accessible, but do not let pointer-created focus pin controls visible. Rebind overlay listeners when `videoElementKey` changes. Force native controls off while the custom overlay is mounted. Use a five-second touch reveal timer for phone/tablet; a second background tap before the timer ends hides controls immediately. Hide the desktop/laptop cursor when the overlay is idle. Capture Space in the custom overlay for play/pause so fullscreen/minimize remains Escape-or-button only. Ignore non-mouse pointer leave for auto-hide. Keep phone custom player sizing scoped to the shell and preserve `100dvh` in fullscreen/cinema mode.

### Regression Guards
Keep tests that verify:

- Playing custom overlay auto-hides after the idle delay.
- Center-surface focus does not pin controls visible.
- Pointer-created control focus does not pin controls visible.
- Native video controls are disabled while the custom overlay is active.
- Phone touch reveal remains visible for five seconds.
- Touch pointer leave does not immediately hide phone controls.
- A second phone background tap hides controls before five seconds.
- Space toggles play/pause without activating the focused fullscreen button.
- Desktop mouse leave still hides controls.

### Do Not Regress
- Do not reintroduce a lower-left play/pause button in the bottom control row.
- Do not use pointer-created focus as a permanent visibility reason.
- Do not treat helper-unit tests as enough proof for live overlay behavior.
- Do not let iOS/mobile warmup paths leave native browser controls enabled under the custom overlay.
- Do not use the short desktop idle delay for phone/tablet touch controls.
- Do not let Space activate fullscreen/minimize while the custom overlay owns playback.
- Do not let touch/pen pointer leave hide phone/tablet controls.
- Do not reintroduce `100vh` overrides that defeat phone `100dvh` fullscreen/cinema sizing.

## Phase B.4 / Real Audio Switch Fix

### Status
Fixed for the reproduced iPhone/PWA route failure.

### Affected Platforms
iPhone/PWA Browser Playback using Route2 audio switching.

### Symptoms
Real audio tracks were discovered and displayed, but clicking another audio track showed a generic "Could not switch audio track" error and never created a backend audio replacement.

### Real Root Cause
The frontend correctly uses `/api/mobile-playback` for iOS/PWA sessions, but the mobile playback router did not expose `/api/mobile-playback/sessions/{session_id}/audio`. The same audio-switch route existed only under `/api/browser-playback`, so iPhone/PWA audio clicks hit a real HTTP 404 before the Route2 replacement path could run.

### Correct Fix
Add the mobile audio-selection endpoint and forward the selected global ffprobe stream index to the existing `select_audio_track()` backend path. Preserve the old active epoch while the audio replacement prepares. Expose the replacement audio stream index/map and short replacement error in session snapshots so live failures report the actual backend state instead of collapsing to a generic UI-only failure.

### Regression Guards
Keep route tests that verify mobile audio switching accepts a global stream index and returns `preparing` state with a pending audio stream. Keep backend contract tests that verify Route2 audio replacements report the selected stream and ffmpeg map string.

### Do Not Regress
- Discovery is not enough; clicking a trusted backend audio row must reach the backend audio replacement path.
- UI pending is not enough; the backend must either promote the new stream or report an exact failure reason.
- Do not leave iPhone/PWA routed to a missing mobile audio endpoint.
- Do not stop old playback while the selected audio replacement prepares.
- Do not change Lite startup thresholds or normal native-HLS window-slide behavior while fixing audio switching.

## Audio Switch In-Progress Lock And Real Failure Surfacing

### Status
Fixed.

### Affected Platforms
iPhone/PWA Browser Playback using Route2 backend audio tracks.

### Symptoms
On item `1424`, `The.Super.Mario.Galaxy.Movie.2026.1080p.Webrip.Multi.Line.Audio.x264-SyncUP`, the audio menu could show a generic "Could not switch audio track" after tapping `LMHD`, and the target row could lose its visible pending spinner. The UI also allowed more audio rows to be tapped while a switch was already requesting, preparing, or attaching.

### Real Root Cause
The item-specific mobile API check showed the backend was not failing for `LMHD`: stream `3` returned `audio_switch_state = preparing`, replacement map `0:3?`, and promoted to `active_audio_stream_index = 3`. Streams `2` and `4` behaved the same way. The live failure category was frontend state handling: the audio menu did not lock competing rows during an in-flight switch, and frontend error resolution could fall back to the generic message instead of preserving the most specific backend/API detail.

### Correct Fix
Treat audio switch state as exclusive while requesting, preparing, or attaching. The pending target row keeps the spinner and pending highlight, the old active row remains selected, and all other audio rows are disabled until the switch becomes active or failed. When a failure is real, resolve the shortest useful message from `audio_switch_error`, replacement last error, HTTP detail, or thrown API detail before using the generic fallback. Keep audio switch feedback inside the audio menu instead of showing page-level preparation text.

### Regression Guards
Keep tests that verify:

- Clicking an unselected backend audio row immediately shows row-level pending.
- Backend `preparing` state locks other audio rows and shows no generic error.
- Backend `active` with client attach still behind keeps the target pending.
- Failed rows and the audio button remain red while the old active row stays selected.
- Thrown API/backend detail is shown instead of the generic fallback.
- The Phase A rectangular `.elvern-overlay__menu.elvern-overlay__track-menu` remains in place.

### Do Not Regress
- Do not allow repeated audio switches while one is requesting, preparing, or attaching.
- Do not clear the pending spinner before matched backend active or failed state.
- Do not show generic audio errors when backend/API detail exists.
- Do not move audio-switch feedback into the page-level preparing note.
- Do not change Phase A menu structure, subtitles/burn-in, cloud probing, recovery, native-HLS normal window slides, Lite thresholds, or adaptive policy while fixing audio switching.

## Client Buffer Gate Regression

### Status
Fixed.

### Affected Platforms
Route2 Lite and Full Browser Playback, especially iPhone/PWA native-HLS sessions where server-side Route2 cache can be ahead while Safari has only a few seconds in `video.buffered`.

### Symptoms
Browser Playback could appear ready, or attempt to start playback, when the server had prepared enough HLS segments but the device itself had only a small contiguous client buffer, such as 4 seconds. The visible "Prepared through X of Y." line could therefore be interpreted as satisfying the Lite 15-second gate even though the client-side media element did not actually have 15 seconds buffered.

### Root Cause
Server prepared/cache readiness and client `video.buffered` readiness were mixed in the final playback release path. The mobile readiness finalizer checked backend runway and media readyState, then could call `video.play()` as an iOS warmup probe before verifying that the device had the required contiguous client buffer from the target playhead.

### Correct Fix
Keep the normal visible wording as "Prepared through X of Y.", but base playback release on the stricter internal gate: server runway must exist and contiguous client buffer ahead from the current target must meet the selected mode threshold. Lite fast requires 15 seconds of client buffer, Lite uncertain requires 45 seconds, Lite undersupply requires 180 seconds, Full normal requires 120 seconds, and Full bad-condition reserve requires 900 seconds, capped only by remaining title duration when the remaining media is shorter than the configured threshold. Audio-switch attach release uses the audio-specific 15-second client buffer gate. The client-buffer check tolerates tiny leading media offsets, such as an fMP4 range beginning at 0.083s for a 0s target, and the warmup path re-checks readiness while `video.buffered` grows.

### Regression Guards
Keep tests that verify:

- The visible normal player note still contains "Prepared through".
- Normal player UI does not introduce "Device buffered", "Server ready", or "Client buffer" labels.
- Server prepared runway alone does not release playback when client buffer is below the required threshold.
- Lite 15/45/180 and Full 120/900 thresholds are enforced as client release thresholds.
- Audio-switch release remains gated by client buffer, not just replacement server readiness.

### Do Not Regress
- Do not lower Lite 15/45/180 or Full 120/900.
- Do not treat server `ready_end_seconds`, cache ranges, or Route2 prepared frontier as sufficient to release playback.
- Do not label normal user-facing client buffer separately; keep "Prepared through X of Y." in the player note.
- Do not call `video.play()`, set `mobilePlayerCanPlay = true`, or clear the waiting state at 4 seconds of client buffer when the selected threshold is 15 seconds or higher.
- Do not change Phase A menu structure, subtitles/burn-in, cloud probing, recovery/background/native-HLS normal window slide behavior, or adaptive policy while enforcing the client buffer gate.

## Prewarm vs Release Gate Regression

### Status
Fixed for the startup deadlock introduced by enforcing client-buffer release before starting the iOS media pipeline; still requires real iPhone/PWA confirmation because Firefox/iPhone-UA with hls.js can buffer while paused and does not reproduce the WebKit deadlock.

### Affected Platforms
iPhone/PWA Route2 Lite and Full Browser Playback after the client-buffer release gate.

### Symptoms
After tapping Lite Playback, the player could remain on the warmup shell with the normal "Prepared through 0:00 of 1:38:01." line and "Elvern is still preparing enough video for stable lite playback." The source was not allowed to make useful progress toward `video.buffered`, so the client-buffer release gate waited forever.

### Root Cause
The startup path treated the client-buffer threshold as the first media-pipeline gate. On iPhone/PWA, Safari may need a source attach plus a user-gesture-backed warmup `play()` before it will build client `video.buffered`. Because the code waited for the client-buffer gate before starting that warmup, it could deadlock: no client buffer, no release, and no warmup path to create client buffer.

### Correct Fix
Keep the visible "Prepared through X of Y." wording and keep Lite 15/45/180 plus Full 120/900 as release thresholds, but split the stages internally. Server attach readiness allows the frontend to attach and prewarm the HLS source. The formal release path still waits for contiguous client `video.buffered` to satisfy the selected threshold before setting `mobilePlayerCanPlay`, clearing the waiting state, or treating playback as ready.

### Regression Guards
Keep tests that prove:

- An attached iPhone source can enter prewarm while the client-buffer release gate is still false.
- Prewarm does not replace the release gate and does not run before a source is attached.
- The normal player UI keeps the "Prepared through" wording and does not expose separate "Device buffered", "Server ready", or "Client buffer" labels.

### Do Not Regress
- Do not lower or bypass the client-buffer release thresholds.
- Do not block source attach/prewarm on the final client-buffer release gate.
- Do not mark playback ready, set `mobilePlayerCanPlay`, or clear waiting/preparing until the client-buffer release gate passes.
- Do not change Phase A menu structure, subtitles/burn-in, cloud probing, adaptive policy, recovery/background behavior, or normal native-HLS window-slide handling while maintaining this split.

## Prewarm Visibility / Black Screen Regression

### Status
Fixed.

### Affected Platforms
iPhone/PWA Route2 Lite and Full Browser Playback during the internal prewarm phase.

### Symptoms
After the prewarm split, Lite Playback could start the media pipeline before release, but the user could see a black player shell and hear audio while the client-buffer gate was still closed. The normal "Prepared through X of Y." line remained correct, but the visual/audio boundary made internal prewarm look like failed playback.

### Root Cause
Internal prewarm was treated too much like user-visible playback. The hidden warmup video could run unmuted, the player shell was exposed while `mobilePlayerCanPlay` was still false, and release did not explicitly require the first decoded frame boundary before showing the real player.

### Correct Fix
Keep internal prewarm attached and loading, but cover it with a preparing card until user playback is actually released. Warmup `play()` mutes the media element first and restores the previous muted/volume state only after release. The release path still requires the client-buffer threshold and now also requires a first-frame-ready signal with nonzero video dimensions before the real player shell is exposed.

### Regression Guards
Keep tests that prove:

- iPhone prewarm with an attached source shows the prewarm card, not exposed controls.
- Warmup mutes before playback and restores the previous audio state at release.
- Client buffer release is still separate from source prewarm.
- The normal player UI keeps "Prepared through" wording and does not introduce "Device buffered", "Server ready", or "Client buffer" labels.

### Do Not Regress
- Do not show a black video shell during prewarm.
- Do not leak audible audio during prewarm.
- Do not mark playback ready before client buffer and first-frame readiness pass.
- Do not lower Lite 15/45/180 or Full 120/900.
- Do not change Phase A menu structure, subtitles/burn-in, cloud probing, adaptive policy, or normal native-HLS window-slide handling while maintaining this boundary.

## iPhone/PWA Continuous Preparation / Stall Regression

### Status
Fixed for the identified native-HLS window telemetry gap; still requires a real iPhone/PWA long-play ear/device verification.

### Affected Platforms
iPhone/PWA Route2 Lite Browser Playback using Safari/WebKit native HLS.

### Symptoms
Item `1424` (`The Super Mario Galaxy Movie`) could start Lite Playback on iPhone/PWA but later stall after several minutes. Local iPhone-UA/hls.js long-play diagnostics showed server preparation continuing and client buffer holding steady, which narrowed the live issue to the native-HLS sliding-window path.

### Real Root Cause
Failure category: `G` native-HLS sliding-window state was not reaching the client. The backend was actually sliding the native-HLS playlist window: the item-specific native-HLS API diagnostic showed `#EXT-X-MEDIA-SEQUENCE` moving from `0` to `9` to `21` while Route2 `ready_end_seconds` continued far ahead. However `MobilePlaybackSessionResponse` did not declare the `active_window_*`, `native_hls_window_policy`, `client_back_buffer_prune_supported`, or `full_duration_seconds` fields, so FastAPI/Pydantic filtered them out of `/api/browser-playback` and `/api/mobile-playback` responses. The frontend therefore fell back to server `ready_end_seconds` as the attached manifest edge and could not map native-HLS `video.buffered` or window exhaustion against the real sliding playlist window.

### Correct Fix
Expose the native-HLS active-window fields in the public playback session response schema. The existing backend sliding-window implementation can continue advancing the dynamic manifest without destructive normal reattach, while the frontend now receives the real active window edge and policy fields needed for client-buffer mapping, window-edge detection, and stale native playlist recovery.

### Regression Guards
Keep route tests that prove Route2 playback API responses preserve `active_window_start_seconds`, `active_window_end_seconds`, `active_window_revision`, and `native_hls_window_policy` instead of filtering them out.

### Do Not Regress
- Visible user-facing wording remains `Prepared through X of Y.`
- Server prepared/cache remains necessary but not sufficient for playback release.
- Native-HLS window slides must continue without normal attach-revision churn.
- Do not let response-model filtering hide `active_window_*` fields again.
- Do not confuse server `ready_end_seconds` with the currently attached native-HLS manifest edge.
- Do not change Phase A menu structure, audio switching, subtitles/burn-in, cloud probing, Lite 15/45/180, Full 120/900, background recovery, or adaptive policy while fixing native-HLS stall telemetry.
