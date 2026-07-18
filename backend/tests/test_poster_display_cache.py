from __future__ import annotations

from dataclasses import replace
import hashlib
from io import BytesIO
import os
from pathlib import Path

from PIL import Image, JpegImagePlugin

from backend.app.db import get_connection
from backend.app.media_scan import scan_media_library
from backend.app.routes import library as library_routes
from backend.app.services.local_library_source_service import ensure_current_shared_local_source_binding
from backend.app.services.poster_derivative_manager import PosterDerivativeQueueFullError
from backend.app.services.poster_display_cache_service import (
    PosterDerivativeDisposition,
    PosterDerivativeResult,
    get_or_create_card_poster_display_cache,
    get_or_create_card_poster_display_result,
)


def _login(client, *, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_jpeg(path: Path, *, size: tuple[int, int], color=(120, 40, 200)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color)
    image.save(path, format="JPEG", quality=100, subsampling=0, progressive=True, optimize=True)


def _create_png(path: Path, *, size: tuple[int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", size, (30, 120, 240, 160))
    image.save(path, format="PNG", optimize=True)


def _display_cache_settings(settings, tmp_path: Path):
    return replace(
        settings,
        poster_display_cache_enabled=True,
        poster_display_cache_dir=(tmp_path / "backend" / "data" / "poster_display_cache").resolve(),
        poster_card_cache_max_width=1400,
        poster_card_cache_jpeg_quality=97,
    )


def _sync_client_settings(client, settings) -> None:
    client.app.state.settings = settings
    if hasattr(client.app.state, "scan_service") and hasattr(client.app.state.scan_service, "settings"):
        client.app.state.scan_service.settings = settings


def _seed_movie_with_poster(settings, *, movie_filename: str, poster_filename: str, large: bool = True) -> Path:
    media_root = Path(settings.media_root)
    media_root.mkdir(parents=True, exist_ok=True)
    poster_dir = media_root / "Posters"
    poster_dir.mkdir(parents=True, exist_ok=True)
    (media_root / movie_filename).write_bytes(b"movie-bytes")
    poster_path = poster_dir / poster_filename
    if large:
        _create_jpeg(poster_path, size=(2800, 4200))
    else:
        _create_jpeg(poster_path, size=(1200, 1800))
    scan_media_library(settings, reason="manual")
    return poster_path


def test_original_poster_is_unchanged_after_cache_generation(initialized_settings, tmp_path) -> None:
    settings = _display_cache_settings(initialized_settings, tmp_path)
    original_path = tmp_path / "posters" / "large.jpg"
    _create_jpeg(original_path, size=(2800, 4200))
    before_stat = original_path.stat()
    before_hash = _file_sha256(original_path)

    cache_path = get_or_create_card_poster_display_cache(settings, original_path)

    after_stat = original_path.stat()
    assert cache_path != original_path
    assert original_path.stat().st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert _file_sha256(original_path) == before_hash


def test_large_jpeg_generates_high_quality_cache_width_capped(initialized_settings, tmp_path) -> None:
    settings = _display_cache_settings(initialized_settings, tmp_path)
    original_path = tmp_path / "posters" / "large.jpg"
    _create_jpeg(original_path, size=(3200, 4800))

    cache_path = get_or_create_card_poster_display_cache(settings, original_path)

    assert cache_path.suffix.lower() == ".jpg"
    assert cache_path.is_file()
    with Image.open(cache_path) as cached_image:
        assert cached_image.width == 1400
        assert cached_image.height > 0
        assert cached_image.format == "JPEG"
        assert JpegImagePlugin.get_sampling(cached_image) == 0
        assert bool(cached_image.info.get("progressive") or cached_image.info.get("progression"))


def test_card_cache_target_width_is_part_of_variant_path(initialized_settings, tmp_path) -> None:
    settings = _display_cache_settings(initialized_settings, tmp_path)
    original_path = tmp_path / "posters" / "large.jpg"
    _create_jpeg(original_path, size=(3200, 4800))

    cache_1000 = get_or_create_card_poster_display_cache(settings, original_path, target_width=1000)
    cache_1400 = get_or_create_card_poster_display_cache(settings, original_path, target_width=1400)

    assert cache_1000 != cache_1400
    assert "card_1000" in str(cache_1000)
    assert "card_1400" in str(cache_1400)
    with Image.open(cache_1000) as cached_image:
        assert cached_image.width == 1000
    with Image.open(cache_1400) as cached_image:
        assert cached_image.width == 1400


def test_cache_is_reused_when_source_is_unchanged(initialized_settings, tmp_path) -> None:
    settings = _display_cache_settings(initialized_settings, tmp_path)
    original_path = tmp_path / "posters" / "large.jpg"
    _create_jpeg(original_path, size=(3000, 4500))

    first_path = get_or_create_card_poster_display_cache(settings, original_path)
    second_path = get_or_create_card_poster_display_cache(settings, original_path)

    assert first_path == second_path


def test_cache_key_changes_when_source_changes(initialized_settings, tmp_path) -> None:
    settings = _display_cache_settings(initialized_settings, tmp_path)
    original_path = tmp_path / "posters" / "large.jpg"
    _create_jpeg(original_path, size=(3000, 4500))

    first_path = get_or_create_card_poster_display_cache(settings, original_path)
    original_stat = original_path.stat()
    os.utime(original_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 10_000))
    second_path = get_or_create_card_poster_display_cache(settings, original_path)

    assert first_path != second_path


def test_small_source_is_not_upscaled(initialized_settings, tmp_path) -> None:
    settings = _display_cache_settings(initialized_settings, tmp_path)
    original_path = tmp_path / "posters" / "small.jpg"
    _create_jpeg(original_path, size=(1200, 1800))

    cache_path = get_or_create_card_poster_display_cache(settings, original_path)

    assert cache_path == original_path

    result = get_or_create_card_poster_display_result(settings, original_path)
    assert result.disposition == PosterDerivativeDisposition.ORIGINAL_ALREADY_SMALL
    assert result.immutable is True


def test_alpha_png_source_returns_png_cache(initialized_settings, tmp_path) -> None:
    settings = _display_cache_settings(initialized_settings, tmp_path)
    original_path = tmp_path / "posters" / "alpha.png"
    _create_png(original_path, size=(2400, 3600))

    cache_path = get_or_create_card_poster_display_cache(settings, original_path)

    assert cache_path.suffix.lower() == ".png"
    with Image.open(cache_path) as cached_image:
        assert cached_image.width == 1400
        assert ("A" in cached_image.getbands()) or ("transparency" in cached_image.info)


def test_corrupt_source_falls_back_without_modifying_original(initialized_settings, tmp_path) -> None:
    settings = _display_cache_settings(initialized_settings, tmp_path)
    original_path = tmp_path / "posters" / "broken.jpg"
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(b"not-a-real-image")
    before_hash = _file_sha256(original_path)

    cache_path = get_or_create_card_poster_display_cache(settings, original_path)

    assert cache_path == original_path
    assert _file_sha256(original_path) == before_hash
    result = get_or_create_card_poster_display_result(settings, original_path)
    assert result.disposition == PosterDerivativeDisposition.FALLBACK_GENERATION_ERROR
    assert result.immutable is False


def test_route_variant_card_returns_display_cache(client, admin_credentials, initialized_settings, tmp_path) -> None:
    settings = _display_cache_settings(initialized_settings, tmp_path)
    _sync_client_settings(client, settings)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    poster_path = _seed_movie_with_poster(
        settings,
        movie_filename="Interstellar.2014.2160p.REMUX.mkv",
        poster_filename="Interstellar (2014).jpg",
    )

    library_response = client.get("/api/library")
    assert library_response.status_code == 200
    item = library_response.json()["items"][0]

    poster_response = client.get(f"{item['poster_url']}&variant=card")
    assert poster_response.status_code == 200
    assert poster_response.headers["cache-control"] == "private, max-age=604800, immutable"
    assert poster_response.headers["vary"] == "Cookie"
    assert poster_response.content != poster_path.read_bytes()
    with Image.open(BytesIO(poster_response.content)) as cached_image:
        assert cached_image.width == 1400


def test_route_variant_card_uses_normal_priority(
    client,
    admin_credentials,
    initialized_settings,
    monkeypatch,
    tmp_path,
) -> None:
    settings = _display_cache_settings(initialized_settings, tmp_path)
    _sync_client_settings(client, settings)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    poster_path = _seed_movie_with_poster(
        settings,
        movie_filename="Normal.Priority.2026.mkv",
        poster_filename="Normal Priority (2026).jpg",
    )
    item = client.get("/api/library").json()["items"][0]
    priorities = []
    real_manager = client.app.state.poster_derivative_manager

    class ManagerStub:
        async def get_or_create_result(self, source_path, *, target_width, priority):
            priorities.append(priority)
            return PosterDerivativeResult(
                path=poster_path,
                disposition=PosterDerivativeDisposition.ORIGINAL_ALREADY_SMALL,
                immutable=True,
            )

    client.app.state.poster_derivative_manager = ManagerStub()
    try:
        response = client.get(f"{item['poster_url']}&variant=card")
    finally:
        client.app.state.poster_derivative_manager = real_manager

    assert response.status_code == 200
    assert priorities == [library_routes.PosterDerivativePriority.NORMAL]


def test_route_queue_full_original_fallback_is_not_cached(
    client,
    admin_credentials,
    initialized_settings,
    tmp_path,
) -> None:
    settings = _display_cache_settings(initialized_settings, tmp_path)
    _sync_client_settings(client, settings)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _seed_movie_with_poster(
        settings,
        movie_filename="Queue.Full.2026.mkv",
        poster_filename="Queue Full (2026).jpg",
    )
    item = client.get("/api/library").json()["items"][0]
    real_manager = client.app.state.poster_derivative_manager

    class QueueFullManager:
        async def get_or_create_result(self, *_args, **_kwargs):
            raise PosterDerivativeQueueFullError("full")

    client.app.state.poster_derivative_manager = QueueFullManager()
    try:
        response = client.get(f"{item['poster_url']}&variant=card")
    finally:
        client.app.state.poster_derivative_manager = real_manager

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-cache, max-age=0, must-revalidate"
    assert response.headers["vary"] == "Cookie"


def test_route_generation_error_original_fallback_is_not_cached(
    client,
    admin_credentials,
    initialized_settings,
    tmp_path,
) -> None:
    settings = _display_cache_settings(initialized_settings, tmp_path)
    _sync_client_settings(client, settings)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _seed_movie_with_poster(
        settings,
        movie_filename="Generation.Error.2026.mkv",
        poster_filename="Generation Error (2026).jpg",
    )
    item = client.get("/api/library").json()["items"][0]
    real_manager = client.app.state.poster_derivative_manager

    class FailureManager:
        async def get_or_create_result(self, *_args, **_kwargs):
            raise RuntimeError("generation failed")

    client.app.state.poster_derivative_manager = FailureManager()
    try:
        response = client.get(f"{item['poster_url']}&variant=card")
    finally:
        client.app.state.poster_derivative_manager = real_manager

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-cache, max-age=0, must-revalidate"


def test_route_variant_card_respects_user_poster_width_setting(client, admin_credentials, initialized_settings, tmp_path) -> None:
    settings = _display_cache_settings(initialized_settings, tmp_path)
    _sync_client_settings(client, settings)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    _seed_movie_with_poster(
        settings,
        movie_filename="Dune.2021.2160p.REMUX.mkv",
        poster_filename="Dune (2021).jpg",
    )

    settings_response = client.patch("/api/user-settings", json={"poster_card_display_max_width": "1000"})
    assert settings_response.status_code == 200
    library_response = client.get("/api/library")
    item = library_response.json()["items"][0]

    poster_response = client.get(f"{item['poster_url']}&variant=card&display_width=2200")
    assert poster_response.status_code == 200
    assert poster_response.headers["cache-control"] == "private, max-age=604800, immutable"
    assert poster_response.headers["vary"] == "Cookie"
    with Image.open(BytesIO(poster_response.content)) as cached_image:
        assert cached_image.width == 1000

    original_setting_response = client.patch("/api/user-settings", json={"poster_card_display_max_width": "original"})
    assert original_setting_response.status_code == 200
    original_poster_response = client.get(f"{item['poster_url']}&variant=card")
    assert original_poster_response.status_code == 200
    with Image.open(BytesIO(original_poster_response.content)) as original_image:
        assert original_image.width == 2800


def test_route_original_or_missing_variant_keeps_original_behavior(client, admin_credentials, initialized_settings, tmp_path) -> None:
    settings = _display_cache_settings(initialized_settings, tmp_path)
    _sync_client_settings(client, settings)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    poster_path = _seed_movie_with_poster(
        settings,
        movie_filename="Arrival.2016.2160p.REMUX.mkv",
        poster_filename="Arrival (2016).jpg",
    )

    library_response = client.get("/api/library")
    item = library_response.json()["items"][0]

    default_response = client.get(item["poster_url"])
    original_response = client.get(f"{item['poster_url']}&variant=original")
    assert default_response.status_code == 200
    assert original_response.status_code == 200
    assert default_response.content == poster_path.read_bytes()
    assert original_response.content == poster_path.read_bytes()
    assert default_response.headers["cache-control"] == "private, no-cache, max-age=0, must-revalidate"
    assert original_response.headers["cache-control"] == "private, no-cache, max-age=0, must-revalidate"


def test_detail_route_enters_interactive_window_before_loading_metadata(
    client,
    admin_credentials,
    initialized_settings,
    monkeypatch,
    tmp_path,
) -> None:
    settings = _display_cache_settings(initialized_settings, tmp_path)
    _sync_client_settings(client, settings)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _seed_movie_with_poster(
        settings,
        movie_filename="Contact.1997.1080p.mkv",
        poster_filename="Contact (1997).jpg",
    )
    item_id = client.get("/api/library").json()["items"][0]["id"]
    real_manager = client.app.state.poster_derivative_manager
    entered = False

    class InteractionManagerStub:
        def enter_interactive_window(self) -> None:
            nonlocal entered
            entered = True

    original_detail_loader = library_routes.get_media_item_detail

    def asserting_detail_loader(*args, **kwargs):
        assert entered
        return original_detail_loader(*args, **kwargs)

    monkeypatch.setattr(library_routes, "get_media_item_detail", asserting_detail_loader)
    client.app.state.poster_derivative_manager = InteractionManagerStub()
    try:
        response = client.get(f"/api/library/item/{item_id}")
    finally:
        client.app.state.poster_derivative_manager = real_manager

    assert response.status_code == 200
    assert entered is True


def test_unsupported_variant_returns_400(client, admin_credentials, initialized_settings, tmp_path) -> None:
    settings = _display_cache_settings(initialized_settings, tmp_path)
    _sync_client_settings(client, settings)
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    _seed_movie_with_poster(
        settings,
        movie_filename="Blade.Runner.1982.2160p.REMUX.mkv",
        poster_filename="Blade Runner (1982).jpg",
    )

    with get_connection(settings) as connection:
        shared_source_id = ensure_current_shared_local_source_binding(settings, connection=connection)
        row = connection.execute(
            """
            SELECT id
            FROM media_items
            WHERE library_source_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (shared_source_id,),
        ).fetchone()
    response = client.get(f"/api/library/item/{int(row['id'])}/poster?variant=thumb")
    assert response.status_code == 400


def test_display_cache_directory_is_gitignored() -> None:
    gitignore_text = Path(".gitignore").read_text(encoding="utf-8")
    assert "backend/data/poster_display_cache/" in gitignore_text
