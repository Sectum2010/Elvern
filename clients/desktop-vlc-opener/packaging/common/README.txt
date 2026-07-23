Elvern VLC Opener
=================

This package installs the lightweight desktop helper that receives
`elvern-vlc://` links from the Elvern web app and opens your local VLC app.

Normal use after install:
1. Open Elvern in your browser.
2. Click "Open in VLC".
3. This helper resolves the short-lived Elvern handoff and launches installed VLC.

Notes:
- This helper does not contain your media library.
- It only resolves short-lived Elvern playback handoffs.
- VLC must already be installed on this client machine.
- Elvern must remain reachable at the configured server origin.
- The standard package is self-contained. No separate .NET installation is required.
- Package payloads are selected locally and verified with SHA-256 before installation.
