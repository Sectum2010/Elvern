from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from .config import PROJECT_ROOT


FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


class SpaStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


def mount_spa(app: FastAPI, *, prefix: str, frontend_dist: Path | None = None) -> None:
    mounted_prefixes = getattr(app.state, "mounted_spa_prefixes", set())
    if prefix in mounted_prefixes:
        return
    app.mount(
        f"/{prefix}",
        SpaStaticFiles(directory=frontend_dist or FRONTEND_DIST, html=True, check_dir=False),
        name=f"spa-{prefix}",
    )
    mounted_prefixes.add(prefix)
    app.state.mounted_spa_prefixes = mounted_prefixes
