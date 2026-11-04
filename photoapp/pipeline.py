"""Шаги одного отчёта: распознавание, Telegram, форма. Шаги не зависят друг от друга,
а при повторе выполняется только то, что ещё не сделано."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from . import jobs as j
from .form_filler import service_for
from .form_flow import HELD, SUBMITTED, FormOutcome, FormRequest
from .jobs import Job, JobStore
from .recognize import Recognition, analyze, ticks_from_note
from .telegram_sender import TelegramError, TelegramUnknown

log = logging.getLogger("photo-app")
SCREENSHOT_NAME = "form.png"
FormRunner = Callable[[FormRequest, bool, bool, Callable[[], Awaitable[None]]], Awaitable[FormOutcome]]


@dataclass(frozen=True)
class Pipeline:
    store: JobStore
    sender: object  # TelegramSender: send(photos, caption)
    run_form: FormRunner
    auto_submit: bool = True
    recognize: Callable[..., Recognition] = analyze

    async def process(self, job_id: str) -> Job:
        job = self.store.update(job_id, status=j.PROCESSING, message="")
        paths = [self.store.folder(job_id) / name for name in job.photos]
        try:
            rec = await asyncio.to_thread(self.recognize, paths, job.note, job.bag_override)
        except Exception:
            log.exception("Отчёт %s: ошибка распознавания", job_id)
            return self.store.update(job_id, status=j.FAILED, message="Не получилось прочитать фото.")
        self.store.update(job_id, bag=rec.bag, service=_service(rec))
        await self._telegram(job_id, paths, rec)
        await self._form(job_id, rec)
        return self._finish(job_id)

    async def _telegram(self, job_id: str, paths, rec: Recognition) -> None:
        job = self.store.get(job_id)
        if job.telegram == j.SENT or (job.telegram == j.UNKNOWN and not job.force):
            return
        self.store.update(job_id, telegram=j.SENDING)
        try:
            await self.sender.send(paths, rec.caption)
            state = j.SENT
        except TelegramUnknown:
            log.warning("Отчёт %s: Telegram не ответил", job_id)
            state = j.UNKNOWN
        except TelegramError as error:
            log.warning("Отчёт %s: Telegram отказал: %s", job_id, error)
            state = j.FAILED
        except Exception as error:
            log.error("Отчёт %s: ошибка отправки в Telegram: %s", job_id, type(error).__name__)
            state = j.FAILED
        self.store.update(job_id, telegram=state)

    async def _form(self, job_id: str, rec: Recognition) -> None:
        job = self.store.get(job_id)
        if job.form == j.SUBMITTED or (job.form == j.UNCONFIRMED and not job.force):
            return
        folder = self.store.folder(job_id)
        request = FormRequest(
            bag=rec.bag,
            service=rec.service,
            ticks=tuple(ticks_from_note(job.note)),
            courier_id=rec.courier_id,
            phone=rec.phone,
            photos=tuple(str(folder / name) for name in job.photos),
            is_screen=rec.is_screen,
            screenshot=folder / SCREENSHOT_NAME,
        )

        async def mark_submitting() -> None:
            self.store.update(job_id, form=j.SUBMITTING)

        self.store.update(job_id, form=j.FILLING, problems=())
        may_submit = self.auto_submit or job.force
        try:
            outcome = await self.run_form(request, may_submit, job.force, mark_submitting)
        except Exception:
            log.exception("Отчёт %s: ошибка при заполнении формы", job_id)
            # упасть могло и после нажатия кнопки: тогда судьба формы неизвестна
            state = j.UNCONFIRMED if self.store.get(job_id).form == j.SUBMITTING else j.FAILED
            self.store.update(job_id, form=state, problems=("форма не заполнилась",))
            return
        self.store.update(
            job_id,
            form=outcome.state,
            problems=outcome.problems,
            has_screenshot=(folder / SCREENSHOT_NAME).exists(),
        )

    def _finish(self, job_id: str) -> Job:
        job = self.store.get(job_id)
        done = job.telegram == j.SENT and job.form == j.SUBMITTED
        status = j.DONE if done else (j.FAILED if j.FAILED in (job.telegram, job.form) else j.REVIEW)
        return self.store.update(job_id, status=status, force=False, message=_message(job))


def _service(rec: Recognition) -> Optional[str]:
    return service_for(rec.bag) or rec.service


def _message(job: Job) -> str:
    parts = []
    if job.form == HELD:
        parts.append("Форма заполнена, но не отправлена. Проверьте и нажмите «Отправить».")
    elif job.form == j.UNCONFIRMED:
        parts.append("Кнопка нажата, но форма не подтвердила отправку. Посмотрите снимок формы.")
    elif job.form == j.FAILED:
        parts.append("Форма не заполнилась.")
    if job.telegram == j.UNKNOWN:
        parts.append("Telegram не ответил: проверьте, появилось ли фото в канале.")
    elif job.telegram == j.FAILED:
        parts.append("В Telegram фото не ушло.")
    return " ".join(parts) or ("Готово." if job.form == SUBMITTED else "")
