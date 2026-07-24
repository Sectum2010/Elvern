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
- The installer does not require Python.
- The package tree and selected payload are verified with SHA-256 before installation.
- Integrity-verified and bound to this Elvern server origin by hash.
- If the package was built for another Elvern server origin, Elvern will not offer it.
