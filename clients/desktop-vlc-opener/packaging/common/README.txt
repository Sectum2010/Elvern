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
- Elvern must remain reachable at the configured private DGX server URL.
- Portable/framework-dependent packages also require the .NET 8 runtime on the client machine.
