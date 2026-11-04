"""Настройки приложения из .env. Без обязательных значений приложение не запускается."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
MAX_PHOTOS = 10  # столько фото принимает один альбом Telegram
MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # на одно фото
MAX_NOTE_CHARS = 500
REQUIRED = ("APP_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "FORM_URL", "FORM_HELPER")


@dataclass(frozen=True)
class Settings:
    app_key: str
    telegram_token: str
    telegram_chat_id: str
    data_dir: Path
    auto_submit: bool = True
    host: str = "0.0.0.0"
    port: int = 8765


def load_settings() -> Settings:
    load_dotenv(PROJECT_DIR / ".env")
    missing = [name for name in REQUIRED if not os.getenv(name)]
    if missing:
        raise SystemExit(f"В .env не заданы: {', '.join(missing)}")
    return Settings(
        app_key=os.environ["APP_KEY"],
        telegram_token=os.environ["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"],
        data_dir=Path(os.getenv("APP_DATA_DIR", PROJECT_DIR / "data")),
        auto_submit=os.getenv("AUTO_SUBMIT", "1") == "1",
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8765")),
    )
