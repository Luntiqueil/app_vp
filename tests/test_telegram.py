import asyncio
import json

import httpx
import pytest

from photoapp import telegram_sender
from photoapp.telegram_sender import TelegramError, TelegramSender, TelegramUnknown

TOKEN = "SECRET-TOKEN"


def send(handler, photos, caption="подпись"):
    sender = TelegramSender(TOKEN, "-100", transport=httpx.MockTransport(handler))
    return asyncio.run(sender.send(photos, caption))


def test_single_photo_uses_send_photo(photo):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    send(handler, [photo])
    assert seen[0].url.path.endswith("/sendPhoto")
    assert "подпись".encode() in seen[0].content


def test_album_uses_media_group_with_caption_on_first(photo):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    send(handler, [photo, photo])
    assert seen[0].url.path.endswith("/sendMediaGroup")
    body = seen[0].content.decode("utf-8", "ignore")
    media = json.loads(body.split('name="media"\r\n\r\n')[1].split("\r\n--")[0])
    assert media[0]["caption"] == "подпись" and "caption" not in media[1]


def test_refusal_raises_error_without_token(photo):
    def handler(_request):
        return httpx.Response(400, json={"ok": False, "description": "Bad Request"})

    with pytest.raises(TelegramError) as caught:
        send(handler, [photo])
    assert "Bad Request" in str(caught.value) and TOKEN not in str(caught.value)


def test_timeout_after_sending_is_unknown_without_token(photo):
    def handler(request):
        raise httpx.ReadTimeout("timeout " + str(request.url), request=request)

    with pytest.raises(TelegramUnknown) as caught:
        send(handler, [photo])
    assert TOKEN not in str(caught.value)
    assert caught.value.__suppress_context__


def test_connection_failure_retries_then_gives_up(photo, monkeypatch):
    monkeypatch.setattr(telegram_sender, "CONNECT_RETRY_S", 0)
    attempts = []

    def handler(request):
        attempts.append(1)
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(TelegramError):
        send(handler, [photo])
    assert len(attempts) == telegram_sender.CONNECT_TRIES
