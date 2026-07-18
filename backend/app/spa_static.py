from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from .config import PROJECT_ROOT


FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
_manifest_cache: dict[str, bytes] = {}


def render_manifest_for_prefix(prefix: str, frontend_dist: Path) -> bytes:
    if prefix in _manifest_cache:
        return _manifest_cache[prefix]

    manifest_path = frontend_dist / "manifest.webmanifest"
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    prefix_root = f"/{prefix.strip('/')}/"

    if "start_url" in manifest:
        manifest["start_url"] = _prefix_manifest_path(manifest["start_url"], prefix_root)

    if "scope" in manifest:
        scope = manifest["scope"]
        if scope == "":
            manifest["scope"] = prefix_root
        else:
            manifest["scope"] = _prefix_manifest_path(scope, prefix_root)

    for icon in manifest.get("icons", []):
        icon["src"] = _prefix_manifest_path(icon.get("src", ""), prefix_root)

    for shortcut in manifest.get("shortcuts", []):
        shortcut["url"] = _prefix_manifest_path(shortcut.get("url", ""), prefix_root)

    rendered = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    _manifest_cache[prefix] = rendered
    return rendered


def clear_manifest_cache() -> None:
    _manifest_cache.clear()


class DynamicManifestMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, frontend_dist: Path):
        super().__init__(app)
        self.frontend_dist = frontend_dist

    async def dispatch(self, request: Request, call_next):
        current_prefix = getattr(request.app.state, "url_prefix", None)
        if not current_prefix:
            return await call_next(request)
        target_path = f"/{current_prefix}/manifest.webmanifest"
        if request.url.path != target_path:
            return await call_next(request)

        try:
            content = render_manifest_for_prefix(current_prefix, self.frontend_dist)
        except FileNotFoundError:
            return Response(status_code=404)
        return Response(
            content=content,
            media_type="application/manifest+json",
            headers={"Cache-Control": "public, max-age=300"},
        )


def install_manifest_middleware(app: FastAPI, *, frontend_dist: Path | None = None) -> None:
    if getattr(app.state, "dynamic_manifest_middleware_installed", False):
        return
    app.add_middleware(
        DynamicManifestMiddleware,
        frontend_dist=frontend_dist or FRONTEND_DIST,
    )
    app.state.dynamic_manifest_middleware_installed = True


def _prefix_manifest_path(value: object, prefix_root: str) -> object:
    if not isinstance(value, str):
        return value
    if value == "":
        return value
    if value.startswith(prefix_root):
        return value
    if value.startswith("/"):
        return prefix_root + value.lstrip("/")
    return value


class SpaStaticFiles(StaticFiles):
    def __init__(self, *args, prefix: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.prefix = prefix.strip("/")

    async def get_response(self, path: str, scope):
        normalized_path = path.strip("/")
        if normalized_path in {"", ".", "index.html"}:
            return self._index_response()
        try:
            response = await super().get_response(path, scope)
            if normalized_path in {"sw.js", "offline.html"}:
                response.headers["Cache-Control"] = "no-cache"
            return response
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            last_segment = path.rsplit("/", 1)[-1]
            if "." in last_segment:
                raise
            return self._index_response()

    def _index_response(self) -> HTMLResponse:
        index_path = Path(str(self.directory)) / "index.html"
        try:
            html = index_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StarletteHTTPException(status_code=404) from exc
        base_tag = f'<base href="/{self.prefix}/">'
        base_start = html.find("<base ")
        if base_start >= 0:
            base_end = html.find(">", base_start)
            if base_end >= 0:
                html = f"{html[:base_start]}{base_tag}{html[base_end + 1:]}"
        else:
            html = html.replace("<head>", f"<head>\n    {base_tag}", 1)
        return HTMLResponse(
            html,
            headers={
                "Cache-Control": "no-cache",
                "X-Elvern-App-Shell": "1",
            },
        )


def mount_spa(app: FastAPI, *, prefix: str, frontend_dist: Path | None = None) -> None:
    mounted_prefixes = getattr(app.state, "mounted_spa_prefixes", set())
    if prefix in mounted_prefixes:
        return
    app.mount(
        f"/{prefix}",
        SpaStaticFiles(directory=frontend_dist or FRONTEND_DIST, html=True, check_dir=False, prefix=prefix),
        name=f"spa-{prefix}",
    )
    mounted_prefixes.add(prefix)
    app.state.mounted_spa_prefixes = mounted_prefixes
