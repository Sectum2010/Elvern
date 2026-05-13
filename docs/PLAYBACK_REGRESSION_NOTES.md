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
