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

1. Publish the contents of `landing/` as the root of a Pages source.
2. The simplest no-build path is a dedicated `gh-pages` branch whose root
   contains `index.html` and the `assets/` folder from this directory.
3. A later GitHub Actions workflow can also publish `landing/` directly without
   moving files, but that workflow is intentionally not included in this
   prototype.

Cloudflare Pages:

```text
Build command: none
Output directory: landing
```

No npm install or build step is required.

## Custom Domain With GitHub Pages

1. Publish the landing page with GitHub Pages.
2. In the repository Pages settings, add the purchased domain as the custom
   domain.
3. At the domain registrar, point the domain to GitHub Pages:
   - For `www.example.com`, add a `CNAME` record pointing to
     `Sectum2010.github.io`.
   - For an apex domain like `example.com`, add GitHub Pages `A` records.
4. Add a `CNAME` file in the published Pages source containing only the domain
   name when the final domain is chosen.
5. Wait for DNS to propagate, then enable HTTPS in GitHub Pages.
