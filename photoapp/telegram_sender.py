"""Публикация фото в канал через Bot API. Токен есть в адресе запроса, поэтому текст ошибок httpx не показываем."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Sequence

import httpx

log = logging.getLogger("photo-app")
logging.getLogger("httpx").setLevel(logging.WARNING)  # иначе в лог попадает токен из адресов запросов
API_URL = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = httpx.Timeout(120, connect=30)
CONNECT_TRIES = 3
CONNECT_RETRY_S = 2
MAX_CAPTION = 1024


class TelegramError(Exception):
    """Telegram отказал: сообщение точно не опубликовано."""


class TelegramUnknown(Exception):
    """Ответа нет: сообщение могло уйти, повторять без проверки нельзя."""


class TelegramSender:
    def __init__(self, token: str, chat_id: str, transport: httpx.AsyncBaseTransport = None):
        self._token = token
        self._chat_id = chat_id
        self._transport = transport

    async def send(self, photos: Sequence[Path], caption: str) -> None:
        files = {f"p{n}": (path.name, path.read_bytes(), "image/jpeg") for n, path in enumerate(photos)}
        data = {"chat_id": self._chat_id}
        if len(photos) == 1:
            method = "sendPhoto"
            files = {"photo": files["p0"]}
            data = {**data, "caption": caption[:MAX_CAPTION], "parse_mode": "HTML"}
        else:
            method = "sendMediaGroup"
            media = [{"type": "photo", "media": f"attach://{name}"} for name in files]
            media[0] = {**media[0], "caption": caption[:MAX_CAPTION], "parse_mode": "HTML"}
            data = {**data, "media": json.dumps(media, ensure_ascii=False)}
        response = await self._post(method, data, files)
        if response.status_code != 200:
            description = response.json().get("description", "") if _is_json(response) else ""
            raise TelegramError(description or f"код {response.status_code}")

    async def _post(self, method: str, data: dict, files: dict) -> httpx.Response:
        url = API_URL.format(token=self._token, method=method)
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as client:
            for attempt in range(1, CONNECT_TRIES + 1):
                try:
                    return await client.post(url, data=data, files=files)
                except httpx.ConnectError:
                    # соединение не установилось: сообщение точно не ушло, можно повторить
                    log.warning("Telegram недоступен, попытка %s из %s", attempt, CONNECT_TRIES)
                    await asyncio.sleep(CONNECT_RETRY_S)
                except httpx.HTTPError as error:
                    raise TelegramUnknown(type(error).__name__) from None
        raise TelegramError("нет связи с Telegram")


def _is_json(response: httpx.Response) -> bool:
    return response.headers.get("content-type", "").startswith("application/json")
