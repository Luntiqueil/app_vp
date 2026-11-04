"""Запуск: python -m photoapp"""
import logging

import uvicorn

from .config import load_settings
from .form_flow import run_form
from .jobs import JobStore
from .pipeline import Pipeline
from .telegram_sender import TelegramSender
from .web import create_app
from .worker import Worker


def main() -> None:
    settings = load_settings()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
    store = JobStore(settings.data_dir)
    pipeline = Pipeline(
        store=store,
        sender=TelegramSender(settings.telegram_token, settings.telegram_chat_id),
        run_form=run_form,
        auto_submit=settings.auto_submit,
    )
    app = create_app(settings, store, Worker(pipeline.process))
    # access_log выключен: в адресе первого входа есть ключ
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False)


if __name__ == "__main__":
    main()
