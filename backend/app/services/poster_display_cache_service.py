from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import tempfile

from PIL import Image, ImageOps

from ..config import Settings


logger = logging.getLogger(__name__)

POSTER_CARD_CACHE_ALGORITHM_VERSION = "poster-card-cache-v1"


def _source_has_alpha(image: Image.Image) -> bool:
    if "transparency" in image.info:
        return True
    bands = image.getbands()
    return "A" in bands


def _card_cache_output_format(image: Image.Image) -> tuple[str, str]:
    if _source_has_alpha(image):
        return "PNG", ".png"
    return "JPEG", ".jpg"


def _card_cache_key(
    *,
    original_poster_path: Path,
    source_stat,
    target_width: int,
    output_format: str,
    jpeg_quality: int,
) -> str:
    key_source = "|".join([
        str(original_poster_path.resolve()),
        str(int(source_stat.st_mtime_ns)),
        str(int(source_stat.st_size)),
        str(int(target_width)),
        output_format.lower(),
        str(int(jpeg_quality)),
        POSTER_CARD_CACHE_ALGORITHM_VERSION,
    ])
    return hashlib.sha256(key_source.encode("utf-8")).hexdigest()


def _card_cache_path(
    settings: Settings,
    *,
    cache_key: str,
    extension: str,
) -> Path:
    variant_dir = settings.poster_display_cache_dir / f"card_{int(settings.poster_card_cache_max_width)}"
    return variant_dir / f"{cache_key}{extension}"


def _atomic_save_image(image: Image.Image, *, target_path: Path, output_format: str, jpeg_quality: int, icc_profile) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    file_descriptor, temp_name = tempfile.mkstemp(
        dir=target_path.parent,
        prefix=f".{target_path.stem}-",
        suffix=target_path.suffix,
    )
    os.close(file_descriptor)
    temp_path = Path(temp_name)
    try:
        save_kwargs: dict[str, object] = {
            "format": output_format,
        }
        if icc_profile:
            save_kwargs["icc_profile"] = icc_profile
        if output_format == "JPEG":
            save_kwargs.update({
                "quality": int(jpeg_quality),
                "subsampling": 0,
                "progressive": True,
                "optimize": True,
            })
        else:
            save_kwargs.update({
                "optimize": True,
            })
        image.save(temp_path, **save_kwargs)
        temp_path.replace(target_path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def get_or_create_card_poster_display_cache(settings: Settings, original_poster_path: Path | str) -> Path:
    original_path = Path(original_poster_path)
    if not settings.poster_display_cache_enabled:
        return original_path

    try:
        source_stat = original_path.stat()
        with Image.open(original_path) as opened_image:
            icc_profile = opened_image.info.get("icc_profile")
            normalized_image = ImageOps.exif_transpose(opened_image)
            source_width, source_height = normalized_image.size
            if source_width <= int(settings.poster_card_cache_max_width):
                return original_path

            output_format, extension = _card_cache_output_format(normalized_image)
            cache_key = _card_cache_key(
                original_poster_path=original_path,
                source_stat=source_stat,
                target_width=int(settings.poster_card_cache_max_width),
                output_format=output_format,
                jpeg_quality=int(settings.poster_card_cache_jpeg_quality),
            )
            target_path = _card_cache_path(
                settings,
                cache_key=cache_key,
                extension=extension,
            )
            if target_path.is_file():
                return target_path

            resize_ratio = int(settings.poster_card_cache_max_width) / float(source_width)
            target_height = max(1, round(source_height * resize_ratio))
            resized_image = normalized_image.resize(
                (int(settings.poster_card_cache_max_width), target_height),
                Image.Resampling.LANCZOS,
            )
            if output_format == "JPEG" and resized_image.mode not in {"RGB", "L"}:
                resized_image = resized_image.convert("RGB")
            _atomic_save_image(
                resized_image,
                target_path=target_path,
                output_format=output_format,
                jpeg_quality=int(settings.poster_card_cache_jpeg_quality),
                icc_profile=icc_profile,
            )
            return target_path
    except Exception as exc:
        logger.warning(
            "Poster display cache generation failed for %s; falling back to original poster: %s",
            original_path,
            exc,
        )
        return original_path
