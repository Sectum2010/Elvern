from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.config import PROJECT_ROOT
from backend.app.db import get_connection, utcnow_iso
from backend.app.services.local_library_source_service import (
    MEDIA_LIBRARY_REFERENCE_KEY,
    get_effective_library_reference_locations,
    serialize_library_reference_locations,
    validate_library_reference_locations,
    validate_shared_local_library_path,
)
from backend.app.services.local_path_security import (
    LIBRARY_REFERENCE_ELVERN_PATH_DETAIL,
    LIBRARY_REFERENCE_LOCAL_URI_DETAIL,
    LIBRARY_REFERENCE_PATH_FORMAT_DETAIL,
    LIBRARY_REFERENCE_SYSTEM_PATH_DETAIL,
    is_restricted_system_path,
)


def _login_admin(client, admin_credentials) -> None:
    response = client.post("/api/auth/login", json=admin_credentials)
    assert response.status_code == 200


def test_library_reference_validator_accepts_absolute_paths_and_local_file_uris(initialized_settings) -> None:
    media_root = Path(initialized_settings.media_root)
    library_root = media_root / "Videos" / "Elvern Media Root"
    library_root.mkdir(parents=True)

    assert validate_shared_local_library_path(initialized_settings, value=str(library_root)) == str(library_root.resolve())
    assert validate_shared_local_library_path(initialized_settings, value=library_root.as_uri()) == str(library_root.resolve())
    assert validate_shared_local_library_path(
        initialized_settings,
        value=f"file://localhost{library_root.as_posix()}",
    ) == str(library_root.resolve())
    assert validate_library_reference_locations(initialized_settings, value="") == [str(media_root.resolve())]


@pytest.mark.parametrize(
    ("candidate", "detail"),
    [
        ("relative/path", "Library reference location must be an absolute Linux directory path."),
        ("https://example.com/media", LIBRARY_REFERENCE_PATH_FORMAT_DETAIL),
        ("smb://fileserver/media", LIBRARY_REFERENCE_PATH_FORMAT_DETAIL),
        ("file://fileserver/mnt/media", LIBRARY_REFERENCE_LOCAL_URI_DETAIL),
        ("file:///mnt/media?query=1", LIBRARY_REFERENCE_PATH_FORMAT_DETAIL),
        ("/mnt/media\x00suffix", LIBRARY_REFERENCE_PATH_FORMAT_DETAIL),
    ],
)
def test_library_reference_validator_rejects_nonlocal_and_malformed_paths(
    initialized_settings,
    candidate: str,
    detail: str,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_shared_local_library_path(initialized_settings, value=candidate)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == detail


def test_library_reference_validator_rejects_missing_directory(initialized_settings) -> None:
    missing_root = Path(initialized_settings.media_root) / "missing-library"

    with pytest.raises(HTTPException) as exc_info:
        validate_shared_local_library_path(initialized_settings, value=str(missing_root))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Library reference location does not exist on this host."


def test_system_path_denylist_blocks_broad_paths_and_allows_media_examples() -> None:
    denied_paths = [
        "/",
        "/home",
        "/home/sectum",
        "/etc",
        "/proc",
        "/sys",
        "/dev",
        "/run",
        "/root",
        "/boot",
        "/usr",
        "/bin",
        "/sbin",
        "/lib",
        "/lib64",
        "/var",
        "/tmp",
        "/tmp/elvern-media",
    ]
    allowed_examples = [
        "/home/sectum/Videos",
        "/home/sectum/Videos/Elvern Media Root",
        "/mnt/media",
        "/media/drive/Movies",
        "/srv/media",
    ]

    assert all(is_restricted_system_path(Path(path)) for path in denied_paths)
    assert not any(is_restricted_system_path(Path(path)) for path in allowed_examples)


def test_library_reference_validator_rejects_system_and_elvern_internal_paths(initialized_settings) -> None:
    with pytest.raises(HTTPException) as system_exc:
        validate_shared_local_library_path(initialized_settings, value="/etc")
    assert system_exc.value.status_code == 400
    assert system_exc.value.detail == LIBRARY_REFERENCE_SYSTEM_PATH_DETAIL

    with pytest.raises(HTTPException) as project_exc:
        validate_shared_local_library_path(initialized_settings, value=str(PROJECT_ROOT))
    assert project_exc.value.status_code == 400
    assert project_exc.value.detail == LIBRARY_REFERENCE_ELVERN_PATH_DETAIL


def test_stored_library_reference_locations_skip_invalid_paths_and_fallback(initialized_settings) -> None:
    media_root = Path(initialized_settings.media_root)
    safe_root = media_root / "Safe Library"
    safe_root.mkdir()

    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (
                MEDIA_LIBRARY_REFERENCE_KEY,
                serialize_library_reference_locations(["/etc", str(safe_root), str(safe_root)]),
                utcnow_iso(),
            ),
        )
        connection.commit()

    assert get_effective_library_reference_locations(initialized_settings) == [safe_root.resolve()]

    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            UPDATE app_settings
            SET value = ?, updated_at = ?
            WHERE key = ?
            """,
            (serialize_library_reference_locations(["/etc"]), utcnow_iso(), MEDIA_LIBRARY_REFERENCE_KEY),
        )
        connection.commit()

    assert get_effective_library_reference_locations(initialized_settings) == [media_root.resolve()]


def test_admin_media_library_reference_route_rejects_system_path(client, admin_credentials) -> None:
    _login_admin(client, admin_credentials)

    response = client.put("/api/admin/media-library-reference", json={"value": "/etc"})

    assert response.status_code == 400
    assert response.json()["detail"] == LIBRARY_REFERENCE_SYSTEM_PATH_DETAIL
