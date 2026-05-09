from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from .auth import build_login_rate_limiters, ensure_admin_user
from .config import refresh_settings
from .db import init_db
from .argon2_calibration import resolve_argon2_params
from .routes.admin import router as admin_router
from .routes.admin_assistant import router as admin_assistant_router
from .routes.assistant import raw_router as assistant_raw_router
from .routes.assistant import router as assistant_router
from .routes.auth import router as auth_router
from .routes.browser_playback import router as browser_playback_router
from .routes.cloud_libraries import router as cloud_libraries_router
from .routes.debug import router as debug_router
from .routes.desktop_helper import router as desktop_helper_router
from .routes.desktop_playback import router as desktop_playback_router
from .routes.download import router as download_router
from .routes.library import router as library_router
from .routes.mobile_playback import router as mobile_playback_router
from .routes.native_playback import router as native_playback_router
from .routes.playback import router as playback_router
from .routes.progress import router as progress_router
from .routes.stream import router as stream_router
from .routes.system import router as system_router
from .routes.user_hidden_items import router as user_hidden_items_router
from .routes.user_settings import router as user_settings_router
from .services.admin_events_service import admin_event_hub
from .services.transcode_service import TranscodeManager
from .services.mobile_playback_service import MobilePlaybackManager
from .services.scan_service import ScanService
from .spa_static import install_manifest_middleware, mount_spa
from .url_prefix_service import resolve_url_prefix


logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = refresh_settings()
    configure_logging(settings.log_level)
    resolve_argon2_params(settings, logger)
    init_db(settings)
    ensure_admin_user(settings)
    url_prefix = resolve_url_prefix(settings, logger)
    app.state.settings = settings
    app.state.url_prefix = url_prefix
    mount_spa(app, prefix=url_prefix)
    app.state.scan_service = ScanService(settings)
    app.state.transcode_manager = TranscodeManager(settings)
    app.state.mobile_playback_manager = MobilePlaybackManager(settings)
    app.state.admin_event_hub = admin_event_hub
    app.state.transcode_manager.start()
    app.state.mobile_playback_manager.start()
    app.state.admin_event_hub.start()
    app.state.login_ip_rate_limiter, app.state.login_username_rate_limiter = build_login_rate_limiters(settings)
    logger.info(
        "Elvern API starting with media root=%s db=%s",
        settings.media_root,
        settings.db_path,
    )
    if settings.scan_on_startup:
        app.state.scan_service.enqueue_scan(reason="startup")
    yield
    app.state.admin_event_hub.shutdown()
    app.state.mobile_playback_manager.shutdown()
    app.state.transcode_manager.shutdown()
    logger.info("Elvern API shutting down")


app = FastAPI(title="Elvern API", version="0.8.0", lifespan=lifespan)
install_manifest_middleware(app)
app.include_router(admin_router)
app.include_router(admin_assistant_router)
app.include_router(assistant_router)
app.include_router(assistant_raw_router)
app.include_router(auth_router)
app.include_router(browser_playback_router)
app.include_router(cloud_libraries_router)
app.include_router(debug_router)
app.include_router(desktop_helper_router)
app.include_router(desktop_playback_router)
app.include_router(download_router)
app.include_router(library_router)
app.include_router(mobile_playback_router)
app.include_router(native_playback_router)
app.include_router(playback_router)
app.include_router(progress_router)
app.include_router(stream_router)
app.include_router(system_router)
app.include_router(user_hidden_items_router)
app.include_router(user_settings_router)


@app.middleware("http")
async def require_spa_url_prefix(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") or path == "/health":
        return await call_next(request)
    url_prefix = getattr(request.app.state, "url_prefix", None)
    if url_prefix and (path == f"/{url_prefix}" or path.startswith(f"/{url_prefix}/")):
        return await call_next(request)
    return PlainTextResponse("Not found", status_code=404)


@app.middleware("http")
async def add_totp_setup_header(request: Request, call_next):
    response = await call_next(request)
    if getattr(request.state, "totp_setup_required", False):
        response.headers["X-Elvern-Totp-Setup-Required"] = "true"
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": "Elvern"}
