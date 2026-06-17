# Elvern Landing Prototype

Standalone static landing-page prototype for Elvern. It is intentionally not wired
into the FastAPI backend, the React/Vite app, or the Node server.

## Local Preview

```bash
cd landing && python3 -m http.server 5173
```

Then open:

```text
http://localhost:5173
```

## Swap Screenshots

All screenshots live in:

```text
landing/assets/screenshots/
```

The copied local PNGs are ignored by `landing/.gitignore` because some contain
admin/user/session/origin details. Replace them with blurred, redacted, or
public-safe assets before publishing the landing page.

The page reads screenshot placement from:

```text
landing/assets/js/screenshots.manifest.js
```

To replace, reorder, or recaption screenshots, update the manifest entries:

```js
{
  file: "Screenshot file name.png",
  section: "hero",
  caption: "Library poster grid",
  alt: "Meaningful accessible description."
}
```

Supported sections are `hero`, `lite`, `features`, `showcase`, `privacy`, and
`install`.

## Deployment

GitHub Pages:

1. Open repository Settings.
2. Open Pages.
3. Deploy from the `/landing` folder.

Cloudflare Pages:

```text
Build command: none
Output directory: landing
```

No npm install or build step is required.
