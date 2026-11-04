"""Приводим любое фото к JPEG разумного размера: так его примут и Telegram, и форма (20 МБ на вопрос)."""
from __future__ import annotations

import io
from pathlib import Path

import pillow_heif
from PIL import Image, ImageOps, UnidentifiedImageError

pillow_heif.register_heif_opener()  # фото с iPhone
MAX_SIDE = 2560
JPEG_QUALITY = 88


class NotAnImage(ValueError):
    pass


def save_as_jpeg(data: bytes, target: Path) -> None:
    try:
        with Image.open(io.BytesIO(data)) as img:
            picture = ImageOps.exif_transpose(img).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise NotAnImage(str(error)) from error
    picture.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    picture.save(target, "JPEG", quality=JPEG_QUALITY, optimize=True)
