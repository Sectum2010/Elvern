from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from starlette.responses import HTMLResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from .config import PROJECT_ROOT


FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


class SpaStaticFiles(StaticFiles):
    def __init__(self, *args, prefix: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.prefix = prefix.strip("/")

    async def get_response(self, path: str, scope):
        normalized_path = path.strip("/")
        if normalized_path in {"", ".", "index.html"}:
            return self._index_response()
        try:
            return await super().get_response(path, scope)
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
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


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
